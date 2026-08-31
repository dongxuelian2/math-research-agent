#!/usr/bin/env node

import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import {
	AgentCore,
	MathAgentConfigService,
	ProofWorkflow,
	Session,
	createAgentProofResearcher,
	createAgentProofRoles,
	createAgentProofVerifier,
	createCandidateVerifierPool,
	createProvider,
	modelConfigOf,
} from "../dist/src/index.js";

const FIRST_PROOF_COMMIT = "274625a22e4748d5f9264ba3614353461520bd20";
const SOURCE_URL = `https://raw.githubusercontent.com/1stproof/batch-2/${FIRST_PROOF_COMMIT}/batch-2-raw-outputs/Batch2Problems/problems.json`;
const SOURCE_PAGE = "https://github.com/1stproof/batch-2";
const PAPER_URL = "https://arxiv.org/abs/2606.18119";
const DEFAULT_PROBLEM_IDS = ["prob-001", "prob-003", "prob-004", "prob-006", "prob-009"];
const DEFAULT_OUT = resolve(process.cwd(), "benchmarks", "first-proof-long-horizon-20260831");

const args = parseArgs(process.argv.slice(2));
const outputDirectory = resolve(args.out ?? DEFAULT_OUT);
const requestedProblemIds = csvValues(args["problem-ids"] ?? DEFAULT_PROBLEM_IDS.join(","));
const requestedTracks = csvValues(args.tracks ?? "direct,agent");
const concurrency = positiveInt(args.concurrency, 1);
const agentWorkers = positiveInt(args["agent-workers"], 4);
const agentSteps = positiveInt(args["agent-steps"], 12);
const historyLimit = positiveInt(args["history-limit"], 8);
const maxAttempts = positiveInt(args.attempts, 3);
const maxTokens = positiveInt(args["max-tokens"], 65_536);
const callTimeoutMs = positiveInt(args["call-timeout-seconds"], 900) * 1_000;
const maxWallTimeMs = positiveInt(args["wall-time-seconds"], 86_400) * 1_000;
const resume = args.resume !== "false";

if (requestedTracks.length === 0 || requestedTracks.some((track) => track !== "direct" && track !== "agent")) {
	throw new Error(`--tracks must contain only direct and/or agent, got: ${requestedTracks.join(",") || "(empty)"}`);
}

await mkdir(join(outputDirectory, "results"), { recursive: true });
await mkdir(join(outputDirectory, "sessions"), { recursive: true });

const configPath = resolve(args.config ?? "configs/math-agent.toml");
const config = new MathAgentConfigService(configPath);
await config.load();
const configuredProfile = config.config.models.gemini37;
if (configuredProfile === undefined) throw new Error("configs/math-agent.toml has no models.gemini37 profile");
const model = { ...modelConfigOf(configuredProfile), maxTokens };
const provider = createProvider(model);

const sourceResponse = await fetch(SOURCE_URL);
if (!sourceResponse.ok) throw new Error(`First Proof source request failed: HTTP ${sourceResponse.status}`);
const sourceText = await sourceResponse.text();
const sourcePayload = JSON.parse(sourceText);
const allProblems = Array.isArray(sourcePayload.problems) ? sourcePayload.problems.map(normalizeProblem) : [];
const problems = requestedProblemIds.map((id) => {
	const problem = allProblems.find((item) => item.id === id);
	if (problem === undefined) throw new Error(`Unknown First Proof problem id: ${id}`);
	return problem;
});

const runId = args["run-id"] ?? `first-proof-long-horizon-${new Date().toISOString().replace(/[-:.TZ]/gu, "").slice(0, 14)}`;
await writeJson(join(outputDirectory, "manifest.json"), {
	schemaVersion: 1,
	runId,
	createdAt: new Date().toISOString(),
	benchmark: {
		name: "First Proof Second Batch — long-horizon subset",
		paperUrl: PAPER_URL,
		sourcePage: SOURCE_PAGE,
		sourceUrl: SOURCE_URL,
		sourceCommit: FIRST_PROOF_COMMIT,
		sourceSha256: createHash("sha256").update(sourceText).digest("hex"),
		license: "Problem documents are published by First Proof under CC BY-SA 4.0 (LICENSE-DOCS).",
		humanSolutionsHiddenDuringSolving: true,
	},
	model: {
		provider: model.provider,
		model: model.model,
		reasoningEffort: model.reasoningEffort,
		contextWindow: model.contextWindow,
		maxTokens: model.maxTokens,
	},
	selectedCount: problems.length,
	tracks: requestedTracks,
	concurrency,
	agentWorkers,
	agentSteps,
	historyLimit,
	maxAttempts,
	callTimeoutMs,
	maxWallTimeMs,
	promptContract: {
		focus: "long-horizon mathematical reasoning and proof planning",
		requiredSections: ["proof roadmap", "critical lemmas and bottlenecks", "complete derivation", "gap audit"],
		failurePolicy: "State the exact unresolved gap and rigorous partial progress; never disguise a missing key step as standard.",
	},
	problems,
});

const jobs = problems.flatMap((problem) => requestedTracks.map((track) => ({ problem, track })));
const totals = { completed: 0, reused: 0, failed: 0 };
let nextJob = 0;
const startedAt = Date.now();

async function workerLoop(workerId) {
	while (true) {
		const job = jobs[nextJob++];
		if (job === undefined) return;
		const resultPath = join(outputDirectory, "results", job.problem.id, `${job.track}.json`);
		if (resume && await exists(resultPath)) {
			const prior = await readJson(resultPath);
			if (!isRetryablePersisted(prior)) {
				totals.reused += 1;
				log({ event: "reused", workerId, problem: job.problem.id, track: job.track, progress: progress() });
				continue;
			}
		}
		await mkdir(join(outputDirectory, "results", job.problem.id), { recursive: true });
		const result = await executeJob(job, workerId);
		await writeJson(resultPath, result);
		if (result.status === "COMPLETED") totals.completed += 1;
		else totals.failed += 1;
		log({
			event: "completed",
			workerId,
			problem: job.problem.id,
			track: job.track,
			status: result.status,
			stopReason: result.trackResult?.stopReason ?? result.direct?.stopReason ?? null,
			outputChars: result.outputChars,
			durationMs: result.durationMs,
			progress: progress(),
		});
	}
}

await Promise.all(Array.from({ length: Math.min(concurrency, jobs.length) }, (_, index) => workerLoop(index + 1)));
await writeJson(join(outputDirectory, "summary-rough.json"), {
	schemaVersion: 1,
	runId,
	finishedAt: new Date().toISOString(),
	durationMs: Date.now() - startedAt,
	totals,
	results: await summarizeResults(),
});
log({ event: "finished", runId, outputDirectory, durationMs: Date.now() - startedAt, totals });
await config.close();

async function executeJob(job, workerId) {
	const started = Date.now();
	let last;
	for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
		try {
			last = job.track === "direct"
				? await runDirect(job.problem, workerId, attempt)
				: await runAgent(job.problem, workerId, attempt);
			if (!shouldRetry(last)) break;
		} catch (error) {
			last = { status: "FAILED", attempt, error: serializeError(error), retryable: isRetryableMessage(errorMessage(error)) };
			if (!last.retryable) break;
		}
		if (attempt < maxAttempts) await delay(1_500 * 2 ** (attempt - 1));
	}
	const normalized = last ?? { status: "FAILED", attempt: 1, error: { name: "Error", message: "No result" } };
	const output = normalized.output ?? "";
	return {
		schemaVersion: 1,
		runId,
		track: job.track,
		problem: { id: job.problem.id, field: job.problem.field, sourceUrl: job.problem.sourceUrl },
		status: normalized.status === "COMPLETED" ? "COMPLETED" : "FAILED",
		attempt: normalized.attempt ?? 1,
		durationMs: Date.now() - started,
		outputChars: output.length,
		output,
		...(job.track === "direct" ? { direct: normalized } : { trackResult: normalized }),
	};
}

async function runDirect(problem, workerId, attempt) {
	const attemptDirectory = await createAttemptDirectory(problem, "direct", workerId, attempt);
	const sessionId = `direct-${problem.id}-${randomUUID().slice(0, 8)}`;
	const session = await Session.create({ projectId: sessionId, sessionId, cwd: attemptDirectory, directory: attemptDirectory });
	const agent = new AgentCore({ session, model, provider, maxTurns: 1 });
	const prompt = solverPrompt(problem, "single-agent baseline");
	const started = Date.now();
	const result = await promptWithTimeout(agent, prompt, callTimeoutMs);
	const output = assistantText(result);
	return {
		status: result.error === undefined && !["model_error", "session_error"].includes(result.stopReason) ? "COMPLETED" : "FAILED",
		attempt,
		workerId,
		sessionDirectory: attemptDirectory,
		prompt,
		output,
		stopReason: result.stopReason,
		assistantStopReason: lastAssistantStopReason(result),
		error: result.error,
		durationMs: Date.now() - started,
	};
}

async function runAgent(problem, workerId, attempt) {
	const attemptDirectory = await createAttemptDirectory(problem, "agent", workerId, attempt);
	const planner = await makeRoleAgent(attemptDirectory, "planner", 1, true);
	const defaultWorker = await makeRoleAgent(attemptDirectory, "worker", 1, false);
	const dynamicAgents = new Map();
	const agentFactory = async (spec) => {
		const key = `${spec.role ?? "worker"}:${spec.agentId}`;
		const existing = dynamicAgents.get(key);
		if (existing !== undefined) return existing;
		const created = makeRoleAgent(attemptDirectory, `worker-${safeSegment(spec.agentId)}`, 1, false).then(createAgentProofResearcher);
		dynamicAgents.set(key, created);
		return created;
	};
	const verifierPool = createCandidateVerifierPool(async (candidateId) => {
		const verifier = await makeRoleAgent(attemptDirectory, `verifier-${safeSegment(candidateId)}`, 1, false);
		return createAgentProofVerifier(verifier);
	});
	const baseRoles = createAgentProofRoles({ planner, researcher: defaultWorker, verifier: defaultWorker, agentFactory });
	const sessionId = `agent-${problem.id}-${randomUUID().slice(0, 8)}`;
	const session = await Session.create({ projectId: sessionId, sessionId, cwd: attemptDirectory, directory: attemptDirectory });
	const workflow = new ProofWorkflow({
		session,
		obligation: {
			obligationId: `${problem.id}-long-horizon-proof`,
			theorem: problem.latex,
			context: solverContract(problem, "multi-agent proof workflow"),
		},
		planner: baseRoles.planner,
		researcher: createAgentProofResearcher(defaultWorker),
		verifier: verifierPool,
		agentFactory,
		literatureSearcher: {
			search: async (query) => ({
				content: `External literature is intentionally unavailable in this closed-book benchmark. Query not executed: ${query}. Continue from the problem statement and internally derived results.`,
			}),
		},
		mode: "prove",
		workflowMode: "dynamic",
		maxWorkers: agentWorkers,
		maxSteps: agentSteps,
		historyLimit,
		workspaceDirectory: attemptDirectory,
		runId: sessionId,
		budget: {
			maxPlannerCalls: agentSteps,
			maxWorkerCalls: agentSteps * agentWorkers * 2,
			maxVerifierCalls: agentSteps * agentWorkers * 2,
			maxWallTimeMs,
		},
	});
	const started = Date.now();
	const result = await workflowWithTimeout(workflow, callTimeoutMs, [planner, defaultWorker, ...await resolvedAgents(dynamicAgents)]);
	const state = workflow.state;
	const selected = result.candidateId === undefined
		? [...state.candidates].reverse().find((candidate) => state.verifications[candidate.candidateId]?.verdict === "CORRECT") ?? state.candidates.at(-1)
		: state.candidates.find((candidate) => candidate.candidateId === result.candidateId);
	let output = selected?.content ?? "";
	if (result.proofPath !== undefined) {
		try { output = await readFile(result.proofPath, "utf8"); } catch { /* preserve candidate output */ }
	}
	const accepted = result.status === "PROVED" || result.status === "CANDIDATE_READY";
	return {
		status: accepted ? "COMPLETED" : "FAILED",
		retryable: result.status === "BLOCKED_PROVIDER",
		attempt,
		workerId,
		sessionDirectory: attemptDirectory,
		output,
		stopReason: result.status,
		durationMs: Date.now() - started,
		result,
		workflow: {
			stateStatus: state.status,
			step: state.step,
			taskCount: state.tasks.length,
			candidateCount: state.candidates.length,
			verificationCount: Object.keys(state.verifications).length,
			candidateId: selected?.candidateId,
			candidateVerdict: selected === undefined ? undefined : state.verifications[selected.candidateId]?.verdict,
			events: workflow.events.length,
			budget: state.budget,
		},
	};
}

async function makeRoleAgent(directory, role, maxTurns, boundedContext) {
	const roleDirectory = join(directory, "roles", safeSegment(role));
	await mkdir(roleDirectory, { recursive: true });
	const sessionId = `${safeSegment(role)}-${randomUUID().slice(0, 8)}`;
	const session = await Session.create({ projectId: sessionId, sessionId, cwd: directory, directory: roleDirectory });
	return new AgentCore({ session, model, provider, maxTurns, ...(boundedContext ? { maxContextMessages: 1 } : {}) });
}

async function promptWithTimeout(agent, prompt, timeoutMs) {
	let timer;
	try {
		return await Promise.race([
			agent.prompt(prompt),
			new Promise((_, reject) => {
				timer = setTimeout(() => {
					try { agent.abort(); } catch { /* preserve timeout result */ }
					reject(new Error(`Model call timed out after ${timeoutMs}ms`));
				}, timeoutMs);
			}),
		]);
	} finally {
		if (timer !== undefined) clearTimeout(timer);
	}
}

async function workflowWithTimeout(workflow, timeoutMs, agents) {
	let timer;
	try {
		return await Promise.race([
			workflow.run(),
			new Promise((_, reject) => {
				timer = setTimeout(() => {
					for (const agent of agents) { try { agent.abort(); } catch { /* preserve timeout result */ } }
					reject(new Error(`Workflow model call timed out after ${timeoutMs}ms`));
			}, timeoutMs);
			}),
		]);
	} finally {
		if (timer !== undefined) clearTimeout(timer);
	}
}

async function resolvedAgents(map) {
	const agents = [];
	for (const value of map.values()) {
		try { agents.push(await value); } catch { /* preserve primary workflow result */ }
	}
	return agents;
}

function solverPrompt(problem, trackLabel) {
	return [
		"You are solving one research-level mathematics problem from First Proof Second Batch.",
		"The human solution and previous AI submissions are deliberately unavailable. Work independently and do not claim to have checked sources you cannot access.",
		solverContract(problem, trackLabel),
		"Problem statement:",
		problem.latex,
	].join("\n\n");
}

function solverContract(problem, trackLabel) {
	return [
		`Track: ${trackLabel}. Field: ${problem.field}.`,
		"This benchmark primarily measures long-horizon reasoning and proof planning.",
		"Begin with a concrete proof roadmap: decompose the theorem into critical lemmas, identify the hardest bottleneck, state dependencies, and name at least one fallback route or falsification check.",
		"Then execute the roadmap. Every non-routine implication at the mathematical bottleneck must be proved, derived, or explicitly isolated as unresolved. Do not replace the key step with phrases such as 'standard', 'routine', or 'it follows' unless you immediately supply the needed statement and verify its hypotheses.",
		"These are exploratory research questions: do not assume that a complete classification exists or that the answer lies in a suggested finite set. If the strongest rigorous result is conditional, partial, or open, state the exact proved range, the dependency on any conjecture, and the remaining obstruction instead of forcing a false complete theorem.",
		"If independent routes reach incompatible conclusions, the final synthesis must state the contradiction explicitly, identify the earliest disputed implication, and re-derive or falsify that implication before selecting either route. Fluency, length, and an earlier verifier label are not tie-breakers.",
		"End with a gap audit that walks from hypotheses to conclusion, checks edge cases and quantifiers, and clearly distinguishes a complete proof from rigorous partial progress.",
		"A transparent incomplete result with a precise obstruction is preferable to a polished but invalid proof. Produce one self-contained submission, not JSON or a protocol envelope.",
	].join("\n");
}

function normalizeProblem(value) {
	if (value === null || typeof value !== "object" || typeof value.id !== "string" || typeof value.latex !== "string") {
		throw new Error("Malformed First Proof problem entry");
	}
	const fields = {
		"prob-001": "computability theory",
		"prob-003": "discrete probability",
		"prob-004": "metric geometry",
		"prob-006": "lattice theory",
		"prob-009": "algebraic combinatorics",
	};
	return {
		id: value.id,
		field: fields[value.id] ?? "research mathematics",
		latex: expandCommonMacros(value.latex),
		sourceUrl: `https://github.com/1stproof/batch-2/blob/${FIRST_PROOF_COMMIT}/batch-2-human-solution/problem-${value.id.slice(-2)}/human-solution.tex`,
	};
}

function expandCommonMacros(text) {
	const replacements = [
		[/\\AUT(?![A-Za-z])/gu, "\\operatorname{AUT}"], [/\\Dil(?![A-Za-z])/gu, "\\operatorname{Dil}"], [/\\Vol(?![A-Za-z])/gu, "\\operatorname{Vol}"],
		[/\\RR(?![A-Za-z])/gu, "\\mathbb{R}"], [/\\bZ(?![A-Za-z])/gu, "\\mathbb{Z}"], [/\\A(?![A-Za-z])/gu, "\\mathcal{A}"], [/\\B(?![A-Za-z])/gu, "\\mathcal{B}"],
		[/\\N(?![A-Za-z])/gu, "\\mathbb{N}"], [/\\Z(?![A-Za-z])/gu, "\\mathbb{Z}"],
	];
	return replacements.reduce((result, [pattern, replacement]) => result.replace(pattern, replacement), text);
}

async function createAttemptDirectory(problem, track, workerId, attempt) {
	const directory = join(outputDirectory, "sessions", problem.id, track, `attempt-${String(attempt).padStart(2, "0")}-w${workerId}-${randomUUID().slice(0, 8)}`);
	await mkdir(directory, { recursive: true });
	return directory;
}

async function summarizeResults() {
	const result = {};
	for (const problem of problems) {
		result[problem.id] = {};
		for (const track of requestedTracks) {
			const value = await readJson(join(outputDirectory, "results", problem.id, `${track}.json`));
			result[problem.id][track] = value === null ? null : {
				status: value.status,
				durationMs: value.durationMs,
				outputChars: value.outputChars,
				stopReason: value.trackResult?.stopReason ?? value.direct?.stopReason ?? null,
				workflow: value.trackResult?.workflow ?? null,
			};
		}
	}
	return result;
}

function assistantText(result) {
	for (let index = result.messages.length - 1; index >= 0; index -= 1) {
		const message = result.messages[index];
		if (message?.role === "assistant") return message.content.filter((part) => part.kind === "text").map((part) => part.text).join("");
	}
	return "";
}

function lastAssistantStopReason(result) { return [...result.messages].reverse().find((message) => message.role === "assistant")?.stopReason; }
function progress() { return `${totals.reused + totals.completed + totals.failed}/${jobs.length}`; }
function shouldRetry(result) { return result?.retryable === true || (result?.status === "FAILED" && isRetryableMessage(result?.error?.message ?? "")); }
function isRetryablePersisted(result) {
	return result?.status !== "COMPLETED" && (
		result?.retryable === true
		|| result?.direct?.stopReason === "model_error"
		|| result?.trackResult?.stopReason === "BLOCKED_PROVIDER"
		|| isRetryableMessage(result?.error?.message ?? result?.direct?.error?.message ?? result?.trackResult?.result?.reason ?? "")
	);
}
function isRetryableMessage(message) { return /429|408|425|5\d\d|timeout|timed out|fetch failed|socket|econn|enotfound|temporarily unavailable|rate exceeded|quota/iu.test(String(message)); }
function errorMessage(error) { return error instanceof Error ? error.message : String(error); }
function serializeError(error) { return { name: error?.name ?? "Error", message: errorMessage(error), ...(error?.stack === undefined ? {} : { stack: error.stack }) }; }
function safeSegment(value) { return String(value).replace(/[^A-Za-z0-9_-]+/gu, "-").replace(/^-+|-+$/gu, "").slice(0, 70) || "item"; }
function csvValues(value) { return String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean); }
function positiveInt(value, fallback) { const number = Number(value); return Number.isInteger(number) && number > 0 ? number : fallback; }
function delay(ms) { return new Promise((resolvePromise) => setTimeout(resolvePromise, ms)); }
async function exists(path) { try { await readFile(path); return true; } catch { return false; } }
async function readJson(path) { try { return JSON.parse(await readFile(path, "utf8")); } catch { return null; } }
async function writeJson(path, value) { await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, "utf8"); }
function log(value) { console.log(JSON.stringify({ at: new Date().toISOString(), ...value })); }
function parseArgs(values) { const parsed = {}; for (let index = 0; index < values.length; index += 1) { const value = values[index]; if (!value.startsWith("--")) continue; const key = value.slice(2); const next = values[index + 1]; if (next === undefined || next.startsWith("--")) parsed[key] = "true"; else { parsed[key] = next; index += 1; } } return parsed; }
