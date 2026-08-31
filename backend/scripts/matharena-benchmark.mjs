#!/usr/bin/env node

import { randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { AgentCore, MathAgentConfigService, ProofWorkflow, Session, createAgentProofResearcher, createAgentProofVerifier, createAgentProofRoles, createCandidateVerifierPool, createProvider, modelConfigOf } from "../dist/src/index.js";

const DATASETS = [
  { competition: "aime_2026", name: "AIME 2026", dataset: "MathArena/aime_2026", count: 30 },
  { competition: "hmmt_feb_2026", name: "HMMT Feb 2026", dataset: "MathArena/hmmt_feb_2026", count: 33 },
  { competition: "apex_2025", name: "Apex 2025", dataset: "MathArena/apex_2025", count: 12 },
  { competition: "apex_shortlist", name: "Apex Shortlist 2025", dataset: "MathArena/apex-shortlist", count: 47 },
];

const DEFAULT_OUT = resolve(process.cwd(), "benchmarks", "matharena-20260831");

const args = parseArgs(process.argv.slice(2));
const concurrency = positiveInt(args.concurrency, 20);
const agentWorkers = positiveInt(args["agent-workers"], 2);
const agentSteps = positiveInt(args["agent-steps"], 3);
const maxAttempts = positiveInt(args.attempts, 3);
const maxTokens = positiveInt(args["max-tokens"], undefined);
const outputDirectory = resolve(args.out ?? DEFAULT_OUT);
const requestedLimit = positiveInt(args.limit, undefined);
const requestedProblemIds = csvValues(args["problem-ids"]);
const requestedTracks = csvValues(args.tracks ?? "direct,agent");
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
const baseModel = modelConfigOf(configuredProfile);
const model = maxTokens === undefined ? baseModel : { ...baseModel, maxTokens };
const provider = createProvider(model);

const problems = await loadProblems();
const requestedProblemIdSet = new Set(requestedProblemIds);
const filteredProblems = requestedProblemIds.length === 0 ? problems : problems.filter((problem) => requestedProblemIdSet.has(problem.id));
const missingProblemIds = requestedProblemIds.filter((problemId) => !filteredProblems.some((problem) => problem.id === problemId));
if (missingProblemIds.length > 0) throw new Error(`Unknown --problem-ids: ${missingProblemIds.join(", ")}`);
const selectedProblems = requestedLimit === undefined ? filteredProblems : filteredProblems.slice(0, requestedLimit);
const runId = args["run-id"] ?? `matharena-${new Date().toISOString().replace(/[-:.TZ]/gu, "").slice(0, 14)}`;
const manifestPath = join(outputDirectory, "manifest.json");
await writeJson(manifestPath, {
  schemaVersion: 1,
  runId,
  createdAt: new Date().toISOString(),
  model: { provider: model.provider, model: model.model, reasoningEffort: model.reasoningEffort, contextWindow: model.contextWindow, maxTokens: model.maxTokens },
  datasets: DATASETS,
  selectedCount: selectedProblems.length,
  tracks: requestedTracks,
  concurrency,
  agentWorkers,
  agentSteps,
  maxAttempts,
  outputDirectory,
  promptContract: "MathArena official final-answer instruction; only the last boxed answer is scored.",
  problems: selectedProblems,
});

const jobs = selectedProblems.flatMap((problem) => requestedTracks.map((track) => ({ problem, track })));
const totals = { completed: 0, reused: 0, failed: 0 };
let nextJob = 0;
const startedAt = Date.now();

async function workerLoop(workerId) {
  while (true) {
    const index = nextJob++;
    const job = jobs[index];
    if (job === undefined) return;
    const resultPath = join(outputDirectory, "results", job.problem.id, `${job.track}.json`);
    if (resume && await exists(resultPath)) {
      const prior = await readJson(resultPath);
      if (!isRetryablePersisted(prior)) {
        totals.reused += 1;
        log({ event: "reused", workerId, track: job.track, problem: job.problem.id, progress: `${totals.reused + totals.completed + totals.failed}/${jobs.length}` });
        continue;
      }
      log({ event: "retrying", workerId, track: job.track, problem: job.problem.id, reason: "previous provider-capacity failure" });
    }
    await mkdir(join(outputDirectory, "results", job.problem.id), { recursive: true });
    const result = await executeJob(job, workerId);
    await writeJson(resultPath, result);
    if (result.status === "COMPLETED") totals.completed += 1;
    else totals.failed += 1;
    log({
      event: "completed",
      workerId,
      track: job.track,
      problem: job.problem.id,
      status: result.status,
      answer: result.answer?.extracted ?? null,
      agentStatus: result.trackResult?.status ?? null,
      ms: result.durationMs,
      attempt: result.attempt,
      progress: `${totals.reused + totals.completed + totals.failed}/${jobs.length}`,
    });
  }
}

await Promise.all(Array.from({ length: Math.min(concurrency, jobs.length) }, (_, index) => workerLoop(index + 1)));
const summary = await aggregateResults(selectedProblems);
await writeJson(join(outputDirectory, "summary-rough.json"), {
  schemaVersion: 1,
  runId,
  finishedAt: new Date().toISOString(),
  durationMs: Date.now() - startedAt,
  totals,
  summary,
});
console.log(JSON.stringify({ event: "finished", runId, outputDirectory, durationMs: Date.now() - startedAt, totals, summary }, null, 2));
await config.close();

async function loadProblems() {
  const all = [];
  for (const dataset of DATASETS) {
    const url = new URL("https://datasets-server.huggingface.co/rows");
    url.searchParams.set("dataset", dataset.dataset);
    url.searchParams.set("config", "default");
    url.searchParams.set("split", "train");
    url.searchParams.set("offset", "0");
    url.searchParams.set("length", "100");
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Hugging Face dataset request failed for ${dataset.dataset}: HTTP ${response.status}`);
    const payload = await response.json();
    if (payload.partial === true) throw new Error(`Hugging Face returned a partial dataset for ${dataset.dataset}`);
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    if (rows.length !== dataset.count) throw new Error(`Unexpected row count for ${dataset.dataset}: ${rows.length}, expected ${dataset.count}`);
    for (const entry of rows) {
      const row = entry?.row;
      if (row === null || typeof row !== "object") throw new Error(`Malformed row in ${dataset.dataset}`);
      const problemIndex = Number(row.problem_idx);
      const problem = typeof row.problem === "string" ? row.problem : "";
      const answer = row.answer === undefined || row.answer === null ? "" : String(row.answer);
      if (!Number.isInteger(problemIndex) || problem.length === 0 || answer.length === 0) throw new Error(`Incomplete row in ${dataset.dataset}/${row.problem_idx}`);
      all.push({
        id: `${dataset.competition}-${String(problemIndex).padStart(3, "0")}`,
        competition: dataset.competition,
        competitionName: dataset.name,
        dataset: dataset.dataset,
        problemIndex,
        answer,
        problem,
        source: typeof row.source === "string" ? row.source : undefined,
        problemType: Array.isArray(row.problem_type) ? row.problem_type : undefined,
      });
    }
  }
  return all;
}

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
      last = { status: "FAILED", error: serializeError(error), retryable: isRetryableMessage(errorMessage(error)) };
      if (!last.retryable) break;
    }
    if (attempt < maxAttempts) await delay(750 * 2 ** (attempt - 1));
  }
  const normalized = last ?? { status: "FAILED", error: { name: "Error", message: "No result" } };
  return {
    schemaVersion: 1,
    runId,
    track: job.track,
    problem: { id: job.problem.id, competition: job.problem.competition, problemIndex: job.problem.problemIndex, dataset: job.problem.dataset, goldAnswer: job.problem.answer },
    status: normalized.status === "COMPLETED" ? "COMPLETED" : "FAILED",
    attempt: normalized.attempt ?? 1,
    durationMs: Date.now() - started,
    answer: { extracted: extractBoxedAnswer(normalized.output ?? ""), outputChars: (normalized.output ?? "").length },
    ...(job.track === "direct" ? { direct: normalized } : { trackResult: normalized }),
  };
}

async function runDirect(problem, workerId, attempt) {
  const attemptDirectory = await createAttemptDirectory(problem, "direct", workerId, attempt);
  const sessionId = `direct-${safeSegment(problem.id)}-${safeSegment(attemptDirectory.split("/").at(-1))}`.slice(0, 96);
  const session = await Session.create({ projectId: sessionId, sessionId, cwd: attemptDirectory, directory: attemptDirectory });
  const agent = new AgentCore({ session, model, provider, maxTurns: 1 });
  const prompt = directPrompt(problem);
  const started = Date.now();
  const result = await agent.prompt(prompt);
  const output = assistantText(result);
  return {
    status: result.error === undefined && result.stopReason !== "model_error" && result.stopReason !== "session_error" ? "COMPLETED" : "FAILED",
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
  const planner = await makeRoleAgent(attemptDirectory, "planner", model, provider, 1, true);
  const defaultWorker = await makeRoleAgent(attemptDirectory, "worker", model, provider, 1, false);
  const dynamicAgents = new Map();
  const agentFactory = async (spec) => {
    const key = `${spec.role ?? "worker"}:${spec.agentId}`;
    const existing = dynamicAgents.get(key);
    if (existing !== undefined) return existing;
    const created = makeRoleAgent(attemptDirectory, `worker-${safeSegment(spec.agentId)}`, model, provider, 1, false).then((agent) => createAgentProofResearcher(agent));
    dynamicAgents.set(key, created);
    return created;
  };
  const verifierPool = createCandidateVerifierPool(async (candidateId) => {
    const verifier = await makeRoleAgent(attemptDirectory, `verifier-${safeSegment(candidateId)}`, model, provider, 1, false);
    return createAgentProofVerifier(verifier);
  });
  const roles = {
    planner: createAgentProofRoles({ planner, researcher: defaultWorker, verifier: defaultWorker }).planner,
    researcher: createAgentProofResearcher(defaultWorker),
    verifier: verifierPool,
    agentFactory,
  };
  const sessionId = `agent-${safeSegment(problem.id)}-${safeSegment(attemptDirectory.split("/").at(-1))}`.slice(0, 96);
  const session = await Session.create({ projectId: sessionId, sessionId, cwd: attemptDirectory, directory: attemptDirectory });
  const obligation = {
    obligationId: `${problem.id}-obligation`,
    theorem: problem.problem,
    context: [
      "This is one official MathArena final-answer item.",
      "Solve the exact problem, do not use external lookup or answer keys, and include a rigorous derivation.",
      "The final numeric or exact expression must appear as the last and only final \\boxed{...} answer.",
      "The runtime will independently verify the proof candidate; do not return JSON or a protocol envelope.",
    ].join("\n"),
  };
  const workflow = new ProofWorkflow({
    session,
    obligation,
    ...roles,
    mode: "prove",
    workflowMode: "dynamic",
    maxWorkers: agentWorkers,
    maxSteps: agentSteps,
    historyLimit: 3,
    workspaceDirectory: attemptDirectory,
    runId: `agent-${safeSegment(problem.id)}-${safeSegment(attemptDirectory.split("/").at(-1))}`.slice(0, 96),
  });
  const started = Date.now();
  const result = await workflow.run();
  const state = workflow.state;
  const selected = result.candidateId === undefined
    ? [...state.candidates].reverse().find((candidate) => state.verifications[candidate.candidateId]?.verdict === "CORRECT") ?? state.candidates.at(-1)
    : state.candidates.find((candidate) => candidate.candidateId === result.candidateId);
  let output = selected?.content ?? "";
  if (result.proofPath !== undefined) {
    try { output = await readFile(result.proofPath, "utf8"); } catch { /* preserve candidate output */ }
  }
  const accepted = result.status === "PROVED" || result.status === "CANDIDATE_READY";
  const retryable = !accepted;
  return {
    status: accepted ? "COMPLETED" : "FAILED",
    retryable,
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
    },
  };
}

async function makeRoleAgent(directory, role, modelConfig, sharedProvider, maxTurns, boundedContext) {
  const segment = safeSegment(role);
  const roleDirectory = join(directory, "roles", segment);
  await mkdir(roleDirectory, { recursive: true });
  const sessionId = `${segment}-${randomUUID().slice(0, 8)}`.slice(0, 96);
  const session = await Session.create({ projectId: sessionId, sessionId, cwd: directory, directory: roleDirectory });
  return new AgentCore({ session, model: modelConfig, provider: sharedProvider, maxTurns, ...(boundedContext ? { maxContextMessages: 1 } : {}) });
}

async function createAttemptDirectory(problem, track, workerId, attempt) {
  const directory = join(outputDirectory, "sessions", problem.id, track, `attempt-${String(attempt).padStart(2, "0")}-w${workerId}-${randomUUID().slice(0, 8)}`);
  await mkdir(directory, { recursive: true });
  return directory;
}

function directPrompt(problem) {
  const instruction = problem.competition === "aime_2026"
    ? "Put your final answer within \\boxed{{}}. The answer is an integer between 0 and 999 inclusive."
    : "Put your final answer within \\boxed{{}}.";
  return [
    "You are the original Gemini 3.7 Flash solving one official MathArena final-answer problem.",
    "Reason carefully and independently. Do not use external lookup, tools, or prior answer keys.",
    "Give a rigorous but concise derivation. Put exactly one final answer in the last \\boxed{...}; do not put other boxed answers earlier.",
    instruction,
    `Competition: ${problem.competitionName}`,
    `Problem ${problem.problemIndex}:`,
    problem.problem,
  ].join("\n\n");
}

function assistantText(result) {
  for (let index = result.messages.length - 1; index >= 0; index -= 1) {
    const message = result.messages[index];
    if (message?.role === "assistant") return message.content.filter((part) => part.kind === "text").map((part) => part.text).join("");
  }
  return "";
}

function lastAssistantStopReason(result) {
  return [...result.messages].reverse().find((message) => message.role === "assistant")?.stopReason;
}

function extractBoxedAnswer(text) {
  const starts = [];
  for (const marker of ["\\boxed{", "\\fbox{"]) {
    let offset = 0;
    while (true) {
      const index = text.indexOf(marker, offset);
      if (index < 0) break;
      starts.push({ index, start: index + marker.length });
      offset = index + marker.length;
    }
  }
  starts.sort((left, right) => left.index - right.index);
  const last = starts.at(-1);
  if (last === undefined) return null;
  let depth = 1;
  for (let index = last.start; index < text.length; index += 1) {
    const character = text[index];
    if (character === "{" && text[index - 1] !== "\\") depth += 1;
    if (character === "}" && text[index - 1] !== "\\") {
      depth -= 1;
      if (depth === 0) return text.slice(last.start, index).trim();
    }
  }
  return null;
}

async function aggregateResults(items) {
  const summary = { direct: emptyTrackSummary(), agent: emptyTrackSummary(), paired: { bothCompleted: 0, sameExtracted: 0, directOnlyCorrectRough: 0, agentOnlyCorrectRough: 0 } };
  for (const item of items) {
    for (const track of ["direct", "agent"]) {
      const path = join(outputDirectory, "results", item.id, `${track}.json`);
      if (!await exists(path)) continue;
      const value = JSON.parse(await readFile(path, "utf8"));
      const target = summary[track];
      target.total += 1;
      if (value.status === "COMPLETED") target.completed += 1;
      if (value.answer.extracted !== null) target.extracted += 1;
      if (roughEqual(value.answer.extracted, item.answer)) target.roughCorrect += 1;
      if (value.answer.outputChars === 0) target.empty += 1;
    }
    const directPath = join(outputDirectory, "results", item.id, "direct.json");
    const agentPath = join(outputDirectory, "results", item.id, "agent.json");
    if (await exists(directPath) && await exists(agentPath)) {
      const direct = JSON.parse(await readFile(directPath, "utf8"));
      const agent = JSON.parse(await readFile(agentPath, "utf8"));
      summary.paired.bothCompleted += direct.status === "COMPLETED" && agent.status === "COMPLETED" ? 1 : 0;
      summary.paired.sameExtracted += direct.answer.extracted !== null && direct.answer.extracted === agent.answer.extracted ? 1 : 0;
      summary.paired.directOnlyCorrectRough += roughEqual(direct.answer.extracted, item.answer) && !roughEqual(agent.answer.extracted, item.answer) ? 1 : 0;
      summary.paired.agentOnlyCorrectRough += roughEqual(agent.answer.extracted, item.answer) && !roughEqual(direct.answer.extracted, item.answer) ? 1 : 0;
    }
  }
  return summary;
}

function emptyTrackSummary() { return { total: 0, completed: 0, extracted: 0, roughCorrect: 0, empty: 0 }; }

function roughEqual(left, right) {
  if (left === null || right === null || left === undefined || right === undefined) return false;
  return String(left).toLowerCase().replace(/\\(?:left|right|displaystyle|text)\b/gu, "").replace(/\s+/gu, "").replace(/[{}$]/gu, "")
    === String(right).toLowerCase().replace(/\\(?:left|right|displaystyle|text)\b/gu, "").replace(/\s+/gu, "").replace(/[{}$]/gu, "");
}

function shouldRetry(result) {
  if (result?.status === "FAILED") return result.retryable === true || isRetryableMessage(result.error?.message ?? result.direct?.error?.message ?? result.trackResult?.result?.reason ?? "");
  if (result?.direct?.error !== undefined) return isRetryableMessage(result.direct.error.message);
  if (result?.trackResult?.retryable === true || result?.trackResult?.result?.status === "BLOCKED_PROVIDER") return true;
  if (result?.trackResult?.status === "BLOCKED_PROVIDER") return true;
  return false;
}

function isRetryablePersisted(result) {
  if (result?.retryable === true) return true;
  if (result?.status === "BLOCKED_PROVIDER") return true;
  if (result?.track === "direct" && ["length", "tool_calls"].includes(result?.direct?.assistantStopReason)) return true;
  if (result?.direct?.error !== undefined) return isRetryableMessage(result.direct.error.message);
  if (result?.trackResult?.retryable === true) return true;
  if (result?.trackResult?.status === "BLOCKED_PROVIDER") return true;
  if (result?.trackResult?.result?.status === "BLOCKED_PROVIDER") return true;
  if (result?.track === "agent" && !["PROVED", "CANDIDATE_READY"].includes(result?.trackResult?.stopReason)) return true;
  return isRetryableMessage(result?.error?.message ?? result?.trackResult?.result?.reason ?? "");
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
