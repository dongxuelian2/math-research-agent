#!/usr/bin/env node

import { randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { AgentCore, MathAgentConfigService, Session, createProvider, modelConfigOf } from "../dist/src/index.js";

const args = parseArgs(process.argv.slice(2));
const benchmarkDirectory = resolve(args.benchmark ?? "benchmarks/first-proof-long-horizon-20260831");
const reviewDirectory = join(benchmarkDirectory, "reviews");
const passes = positiveInt(args.passes, 2);
const maxTokens = positiveInt(args["max-tokens"], 32_768);
const resume = args.resume !== "false";

await mkdir(reviewDirectory, { recursive: true });
const manifest = JSON.parse(await readFile(join(benchmarkDirectory, "manifest.json"), "utf8"));
const config = new MathAgentConfigService(resolve(args.config ?? "configs/math-agent.toml"));
await config.load();
const configuredProfile = config.config.models.gemini37;
if (configuredProfile === undefined) throw new Error("configs/math-agent.toml has no models.gemini37 profile");
const model = { ...modelConfigOf(configuredProfile), maxTokens };
const provider = createProvider(model);
const reviewSummary = {};

for (const problem of manifest.problems) {
	const directSource = await loadSubmission(problem.id, "direct");
	const agentSource = await loadSubmission(problem.id, "agent");
	if (directSource === null || agentSource === null) {
		reviewSummary[problem.id] = { status: "SKIPPED", reason: "Both non-empty submissions are required", sources: { direct: directSource?.provenance ?? null, agent: agentSource?.provenance ?? null } };
		continue;
	}
	const direct = directSource.record;
	const agent = agentSource.record;
	const humanSolutionUrl = `https://raw.githubusercontent.com/1stproof/batch-2/${manifest.benchmark.sourceCommit}/batch-2-human-solution/problem-${problem.id.slice(-2)}/human-solution.tex`;
	const response = await fetch(humanSolutionUrl);
	if (!response.ok) throw new Error(`Human solution request failed for ${problem.id}: HTTP ${response.status}`);
	const humanSolution = await response.text();
	const passResults = [];
	for (let pass = 1; pass <= passes; pass += 1) {
		const path = join(reviewDirectory, problem.id, `pass-${String(pass).padStart(2, "0")}.json`);
		if (resume) {
			const prior = await readJson(path);
			if (prior?.status === "COMPLETED") { passResults.push(prior); continue; }
		}
		await mkdir(join(reviewDirectory, problem.id), { recursive: true });
		const agentFirst = pass % 2 === 0;
		const submissionA = agentFirst ? agent.output : direct.output;
		const submissionB = agentFirst ? direct.output : agent.output;
		const labelMap = agentFirst ? { A: "agent", B: "direct" } : { A: "direct", B: "agent" };
		const result = await review(problem, humanSolution, submissionA, submissionB, pass);
		const persisted = { ...result, problemId: problem.id, pass, labelMap, humanSolutionUrl, provenance: { direct: directSource.provenance, agent: agentSource.provenance } };
		await writeJson(path, persisted);
		passResults.push(persisted);
		console.log(JSON.stringify({ at: new Date().toISOString(), event: "reviewed", problem: problem.id, pass, status: result.status, winner: result.parsed?.winner ?? null }));
	}
	reviewSummary[problem.id] = aggregatePasses(passResults);
}

await writeJson(join(benchmarkDirectory, "review-summary.json"), {
	schemaVersion: 1,
	createdAt: new Date().toISOString(),
	benchmarkRunId: manifest.runId,
	judge: { provider: model.provider, model: model.model, reasoningEffort: model.reasoningEffort, maxTokens, passes },
	rubric: rubricDefinition(),
	problems: reviewSummary,
	overall: aggregateOverall(reviewSummary),
});
await config.close();

async function review(problem, humanSolution, submissionA, submissionB, pass) {
	const directory = join(reviewDirectory, problem.id, `judge-session-${String(pass).padStart(2, "0")}-${randomUUID().slice(0, 8)}`);
	await mkdir(directory, { recursive: true });
	const sessionId = `judge-${problem.id}-${pass}-${randomUUID().slice(0, 8)}`;
	const session = await Session.create({ projectId: sessionId, sessionId, cwd: directory, directory });
	const judge = new AgentCore({ session, model, provider, maxTurns: 1 });
	const prompt = reviewPrompt(problem, humanSolution, submissionA, submissionB);
	const started = Date.now();
	const result = await judge.prompt(prompt);
	const raw = assistantText(result);
	let parsed;
	let parseError;
	try { parsed = validateReview(parseJsonObject(raw)); } catch (error) { parseError = errorMessage(error); }
	return {
		status: result.error === undefined && parsed !== undefined ? "COMPLETED" : "FAILED",
		durationMs: Date.now() - started,
		stopReason: result.stopReason,
		error: result.error,
		parseError,
		raw,
		parsed,
	};
}

async function loadSubmission(problemId, track) {
	const primary = await readJson(join(benchmarkDirectory, "results", problemId, `${track}.json`));
	if (nonEmptyOutput(primary)) return { record: primary, provenance: "runner-result" };
	const recoveryCandidates = [
		join(benchmarkDirectory, "recovered-results", problemId, `${track}.json`),
		...(await recoveryDirectories()).map((directory) => join(directory, "results", problemId, `${track}.json`)),
	];
	for (const path of recoveryCandidates) {
		const recovered = await readJson(path);
		if (nonEmptyOutput(recovered)) return { record: recovered, provenance: recovered.provenance ?? `recovered:${path}` };
	}
	return null;
}

async function recoveryDirectories() {
	try {
		const entries = await readdir(benchmarkDirectory, { withFileTypes: true });
		return entries.filter((entry) => entry.isDirectory() && /^(?:recovery|recovered)-/iu.test(entry.name)).map((entry) => join(benchmarkDirectory, entry.name));
	} catch { return []; }
}

function nonEmptyOutput(value) { return value !== null && typeof value === "object" && typeof value.output === "string" && value.output.length > 0; }

function reviewPrompt(problem, humanSolution, submissionA, submissionB) {
	return [
		"You are a double-blind expert referee for a research-level mathematics benchmark.",
		"The official human solution is an evaluation anchor, not the only allowed route. Independently check whether each submitted argument is valid; accept a genuinely correct alternative proof. Do not infer author or system identity from style.",
		"Correctness has greatest weight, but this benchmark specifically tests long-horizon proof planning and whether the plan is actually executed without skipping the hard step.",
		"Use this 100-point rubric for each submission:",
		"- mathematical correctness: 0–30",
		"- proof planning and execution over the full argument: 0–25",
		"- critical-step completeness / absence of unjustified jumps: 0–25",
		"- insight, useful partial progress, and originality: 0–10",
		"- exposition, definitions, and responsible attributions: 0–10",
		"Also assign exactly one journal-style verdict: ESSENTIALLY_FLAWLESS, MINOR_REVISIONS, MAJOR_REVISIONS, or REJECT. A polished answer with a gap at the central lemma cannot receive MINOR_REVISIONS. An honestly incomplete answer may outscore a false complete proof if its partial results and obstruction analysis are substantial.",
		"Return only one JSON object with this exact shape:",
		'{"submissionA":{"verdict":"...","scores":{"correctness":0,"planningExecution":0,"criticalCompleteness":0,"insight":0,"exposition":0,"total":0},"fatalGaps":["..."],"strengths":["..."],"planExecutionAssessment":"...","confidence":0.0},"submissionB":{"verdict":"...","scores":{"correctness":0,"planningExecution":0,"criticalCompleteness":0,"insight":0,"exposition":0,"total":0},"fatalGaps":["..."],"strengths":["..."],"planExecutionAssessment":"...","confidence":0.0},"winner":"A","winnerReason":"..."}',
		`Field: ${problem.field}`,
		"Problem statement:",
		problem.latex,
		"Official human solution:",
		humanSolution,
		"Anonymous submission A:",
		submissionA,
		"Anonymous submission B:",
		submissionB,
	].join("\n\n");
}

function rubricDefinition() {
	return {
		mathematicalCorrectness: 30,
		proofPlanningAndExecution: 25,
		criticalStepCompleteness: 25,
		insightAndUsefulProgress: 10,
		expositionAndAttribution: 10,
		verdicts: ["ESSENTIALLY_FLAWLESS", "MINOR_REVISIONS", "MAJOR_REVISIONS", "REJECT"],
	};
}

function validateReview(value) {
	if (value === null || typeof value !== "object") throw new Error("Review is not an object");
	if (!["A", "B", "TIE"].includes(value.winner)) throw new Error("Invalid winner");
	for (const key of ["submissionA", "submissionB"]) {
		const item = value[key];
		if (item === null || typeof item !== "object") throw new Error(`Missing ${key}`);
		if (!["ESSENTIALLY_FLAWLESS", "MINOR_REVISIONS", "MAJOR_REVISIONS", "REJECT"].includes(item.verdict)) throw new Error(`Invalid ${key} verdict`);
		const maxima = { correctness: 30, planningExecution: 25, criticalCompleteness: 25, insight: 10, exposition: 10, total: 100 };
		for (const [score, maximum] of Object.entries(maxima)) {
			if (typeof item.scores?.[score] !== "number" || item.scores[score] < 0 || item.scores[score] > maximum) throw new Error(`Invalid ${key}.${score}`);
		}
		const computed = item.scores.correctness + item.scores.planningExecution + item.scores.criticalCompleteness + item.scores.insight + item.scores.exposition;
		if (Math.abs(computed - item.scores.total) > 0.01) throw new Error(`${key} total does not equal component sum`);
	}
	return value;
}

function aggregatePasses(passesForProblem) {
	const completed = passesForProblem.filter((item) => item.status === "COMPLETED" && item.parsed !== undefined);
	const tracks = { direct: emptyAggregate(), agent: emptyAggregate() };
	for (const pass of completed) {
		for (const anonymous of ["A", "B"]) {
			const track = pass.labelMap[anonymous];
			const submission = pass.parsed[`submission${anonymous}`];
			tracks[track].totals.push(submission.scores.total);
			tracks[track].planningExecution.push(submission.scores.planningExecution);
			tracks[track].criticalCompleteness.push(submission.scores.criticalCompleteness);
			tracks[track].verdicts.push(submission.verdict);
			tracks[track].fatalGaps.push(...submission.fatalGaps);
			tracks[track].strengths.push(...submission.strengths);
		}
	}
	return {
		status: completed.length === passes ? "COMPLETED" : "PARTIAL",
		completedPasses: completed.length,
		tracks: Object.fromEntries(Object.entries(tracks).map(([track, value]) => [track, finishAggregate(value)])),
		pairwiseWins: completed.map((pass) => pass.parsed.winner === "TIE" ? "TIE" : pass.labelMap[pass.parsed.winner]),
	};
}

function aggregateOverall(summary) {
	const output = { direct: emptyAggregate(), agent: emptyAggregate() };
	for (const problem of Object.values(summary)) {
		if (problem?.tracks === undefined) continue;
		for (const track of ["direct", "agent"]) {
			if (typeof problem.tracks[track].meanTotal === "number") output[track].totals.push(problem.tracks[track].meanTotal);
			if (typeof problem.tracks[track].meanPlanningExecution === "number") output[track].planningExecution.push(problem.tracks[track].meanPlanningExecution);
			if (typeof problem.tracks[track].meanCriticalCompleteness === "number") output[track].criticalCompleteness.push(problem.tracks[track].meanCriticalCompleteness);
			output[track].verdicts.push(...problem.tracks[track].verdicts);
		}
	}
	return Object.fromEntries(Object.entries(output).map(([track, value]) => [track, finishAggregate(value)]));
}

function emptyAggregate() { return { totals: [], planningExecution: [], criticalCompleteness: [], verdicts: [], fatalGaps: [], strengths: [] }; }
function finishAggregate(value) {
	return {
		meanTotal: mean(value.totals),
		meanPlanningExecution: mean(value.planningExecution),
		meanCriticalCompleteness: mean(value.criticalCompleteness),
		verdicts: value.verdicts,
		fatalGaps: [...new Set(value.fatalGaps)],
		strengths: [...new Set(value.strengths)],
	};
}

function parseJsonObject(text) {
	const stripped = text.trim().replace(/^```(?:json)?\s*/iu, "").replace(/\s*```$/u, "");
	const start = stripped.indexOf("{");
	const end = stripped.lastIndexOf("}");
	if (start < 0 || end < start) throw new Error("No JSON object found");
	const json = stripped.slice(start, end + 1);
	try { return JSON.parse(json); } catch (firstError) {
		// Judges occasionally emit LaTeX commands with a single backslash inside
		// an otherwise valid JSON object. Escape only backslashes that are not
		// part of JSON's own escape alphabet, then retry without hiding any
		// structural/schema errors.
		try {
			const repaired = json.replace(/\\+/gu, (run, offset, whole) => {
				const next = whole[offset + run.length] ?? "";
				return run.length % 2 === 1 && !/["\\/bfnrtu]/u.test(next) ? `${run}\\` : run;
			});
			return JSON.parse(repaired);
		} catch { throw firstError; }
	}
}

function assistantText(result) {
	for (let index = result.messages.length - 1; index >= 0; index -= 1) {
		const message = result.messages[index];
		if (message?.role === "assistant") return message.content.filter((part) => part.kind === "text").map((part) => part.text).join("");
	}
	return "";
}

function mean(values) { return values.length === 0 ? null : values.reduce((sum, value) => sum + value, 0) / values.length; }
function errorMessage(error) { return error instanceof Error ? error.message : String(error); }
function positiveInt(value, fallback) { const number = Number(value); return Number.isInteger(number) && number > 0 ? number : fallback; }
async function readJson(path) { try { return JSON.parse(await readFile(path, "utf8")); } catch { return null; } }
async function writeJson(path, value) { await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, "utf8"); }
function parseArgs(values) { const parsed = {}; for (let index = 0; index < values.length; index += 1) { const value = values[index]; if (!value.startsWith("--")) continue; const key = value.slice(2); const next = values[index + 1]; if (next === undefined || next.startsWith("--")) parsed[key] = "true"; else { parsed[key] = next; index += 1; } } return parsed; }
