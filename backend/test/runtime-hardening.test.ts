import assert from "node:assert/strict";
import { cp, readFile, readdir, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtemp } from "node:fs/promises";
import test from "node:test";
import { BOOTSTRAP_ANALYSIS_SCHEMA, BootstrapSchemaValidationError, BootstrapStructuredOutputParseError, parseBootstrapAnalysis, parseBootstrapAnalysisText } from "../src/research/bootstrap-schema.js";
import { CodexCliProvider, codexCliArguments } from "../src/providers/codex.js";
import { CorpusService } from "../src/research/corpus.js";
import { ResearchRuntime } from "../src/research/runtime.js";
import { ResearchStore } from "../src/research/store.js";
import { stableId } from "../src/research/ids.js";
import type { BootstrapProposal, DecisionBasis, ResearchProjectState, TacticalResearchResult } from "../src/research/types.js";

test("canonical bootstrap schema and parser agree on dependencies and structural kinds", () => {
	const response = {
		proposals: [
			fullProposal({ entityKey: "base", kind: "CLAIM", statement: "Base lemma", dependencyHints: [] }),
			fullProposal({ entityKey: "reduction", kind: "REDUCTION", statement: "Root reduces to base", dependencyHints: ["base"], targetHint: "root" }),
			fullProposal({ entityKey: "cases", kind: "CASE_SPLIT", statement: "Root has two cases", dependencyHints: [], targetHint: "root", cases: ["even", "odd"] }),
			fullProposal({ entityKey: "dead", kind: "FAILED_ROUTE", statement: "Descent fails", dependencyHints: [], targetHint: "root", routeFamily: "descent", mechanism: "minimal-counterexample", failureMechanism: "measure is not decreasing" }),
		],
		dependencies: [{ fromEntity: "reduction", toEntity: "base", confidence: "EXPLICIT", confidenceScore: 0.99 }], warnings: [],
	};
	const parsed = parseBootstrapAnalysis(response);
	assert.equal(parsed.proposals.length, 4);
	assert.equal(parsed.dependencies[0]?.confidence, "EXPLICIT");
	const proposalSchema = ((BOOTSTRAP_ANALYSIS_SCHEMA.properties as Record<string, any>).proposals.items as Record<string, any>);
	assert.ok(proposalSchema.required.includes("dependencyHints"));
	assert.deepEqual(((BOOTSTRAP_ANALYSIS_SCHEMA.properties as Record<string, any>).dependencies.items.properties.confidence.enum), ["EXPLICIT", "INFERRED"]);
	assert.equal(proposalSchema.allOf, undefined, "provider-compatible schema must avoid unsupported conditionals");
	assert.ok(["targetHint", "routeFamily", "mechanism", "failureMechanism", "cases"].every((key) => proposalSchema.required.includes(key)));
});

test("real Luna numeric-confidence fixture fails clearly while its canonical correction parses", async () => {
	const fixture = JSON.parse(await readFile(fixturePath("luna-bootstrap-numeric-confidence.json"), "utf8")) as { response: Record<string, any> };
	assert.throws(() => parseBootstrapAnalysis(fixture.response), (error) => error instanceof BootstrapSchemaValidationError && /confidence must be EXPLICIT or INFERRED/u.test(error.message));
	const corrected = structuredClone(fixture.response); corrected.proposals = corrected.proposals.map(fullProposal); corrected.dependencies[0].confidenceScore = corrected.dependencies[0].confidence; corrected.dependencies[0].confidence = "EXPLICIT";
	assert.equal(parseBootstrapAnalysis(corrected).dependencies[0]?.confidenceScore, 1);
	const missing = structuredClone(corrected); delete missing.proposals[0].dependencyHints;
	assert.throws(() => parseBootstrapAnalysis(missing), /dependencyHints is required and must be string\[\]/u);
});

test("malformed structured output remains fail-closed and durably preserves exact model evidence", async (t) => {
	const raw = '{"proposals":[],"dependencies":[],"warnings":[]} trailing'; assert.throws(() => parseBootstrapAnalysisText(raw), (error) => error instanceof BootstrapStructuredOutputParseError && error.rawResponse === raw);
	const directory = await mkdtemp(join(tmpdir(), "mrr-bootstrap-raw-failure-")), corpusDirectory = join(directory, "corpus"), data = join(directory, "data"); await mkdir(corpusDirectory); await writeFile(join(corpusDirectory, "source.md"), "Lemma Durable: statement"); t.after(() => rm(directory, { recursive: true, force: true }));
	const store = new ResearchStore(data), runtime = new ResearchRuntime({ store, proofRunner: async () => { throw new Error("unused"); } }); await runtime.createProject("p", "p"); await runtime.setRootObjective("p", "Root theorem"); const corpus = new CorpusService(store); await corpus.attach("p", [corpusDirectory]); await corpus.ingest("p");
	await corpus.bootstrap("p", { async analyzeFile() { throw new BootstrapStructuredOutputParseError("malformed", raw).attachModelMetadata({ sessionId: "session-exact", provider: "openai-codex", model: "gpt-5.6-luna" }); } }); const state = await store.read("p"), work = Object.values(Object.values(state.bootstrapRuns)[0]!.rangeWork)[0]!; assert.equal(work.failure?.type, "STRUCTURED_OUTPUT_PARSE_FAILURE"); assert.equal(work.rawResponse, raw); assert.equal(work.modelSessionId, "session-exact"); assert.equal(work.provider, "openai-codex"); assert.equal(work.model, "gpt-5.6-luna");
});

test("Codex long structured input uses stdin and output-schema instead of argv", async () => {
	const fixture = fixturePath("fake-codex-cli.mjs"), provider = new CodexCliProvider({ command: process.execPath, commandArgs: [fixture] });
	const longPrompt = "x".repeat(100_000), events = [];
	for await (const event of provider.stream({ model: { provider: "openai-codex", model: "gpt-test" }, messages: [{ role: "user", id: "u", content: longPrompt, timestamp: Date.now(), responseSchema: BOOTSTRAP_ANALYSIS_SCHEMA }], tools: [], responseSchema: BOOTSTRAP_ANALYSIS_SCHEMA })) events.push(event);
	assert.ok(events.some((event) => event.type === "text_delta"));
	assert.ok(events.some((event) => event.type === "complete"));
	assert.ok(!codexCliArguments("gpt-test", "schema.json").some((argument) => argument.includes(longPrompt)));
	assert.equal(codexCliArguments("gpt-test").at(-1), "-");
	assert.ok(codexCliArguments("gpt-5.6-luna", "schema.json", "max").includes("model_reasoning_effort=\"max\""));
});

test("incremental bootstrap persists completed ranges, reclaims interruption, and stales changed corpus identity", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "mrr-bootstrap-resume-")), corpusDirectory = join(directory, "corpus"), data = join(directory, "data"); await mkdir(corpusDirectory); const sourcePath = join(corpusDirectory, "large.md"); await writeFile(sourcePath, Array.from({ length: 2400 }, (_, index) => `Lemma L${index}: statement ${"z".repeat(28)}`).join("\n")); t.after(() => rm(directory, { recursive: true, force: true }));
	const store = new ResearchStore(data), runtime = new ResearchRuntime({ store, proofRunner: async () => { throw new Error("unused"); } }); await runtime.createProject("p", "p"); await runtime.setRootObjective("p", "Root theorem"); const corpus = new CorpusService(store); await corpus.attach("p", [corpusDirectory]); await corpus.ingest("p");
	let firstCalls = 0; const controller = new AbortController();
	await assert.rejects(corpus.bootstrap("p", { async analyzeFile() { firstCalls += 1; if (firstCalls === 2) { controller.abort(); throw new Error("supervisor interruption"); } return analysis(`first-${firstCalls}`); } }, controller.signal), /supervisor interruption/u);
	let state = await store.read("p"), run = Object.values(state.bootstrapRuns)[0]; assert.ok(run); const completedBefore = Object.values(run.rangeWork).filter((item) => item.status === "COMPLETED"); assert.equal(completedBefore.length, 1); const runId = run.bootstrapRunId;
	let resumedCalls = 0; const resumedReport = await new CorpusService(store).bootstrap("p", { async analyzeFile() { resumedCalls += 1; return analysis(`resume-${resumedCalls}`); } }); state = await store.read("p"); run = state.bootstrapRuns[runId]; assert.equal(run?.status, "COMPLETED"); assert.equal(Object.values(run?.rangeWork ?? {}).every((item) => item.status === "COMPLETED"), true); assert.equal(Object.values(run?.rangeWork ?? {}).every((item) => item.failure === undefined), true, "a successful retry must not inherit its orphan/interruption failure"); assert.equal(resumedReport.bootstrapRunId, runId); assert.equal(resumedCalls, Object.keys(run?.rangeWork ?? {}).length - 1, "the completed range must not rerun");
	await writeFile(sourcePath, `${await readFile(sourcePath, "utf8")}\nLemma changed: new statement\n`); await corpus.ingest("p"); await new CorpusService(store).bootstrap("p", { async analyzeFile() { return analysis("changed"); } }); state = await store.read("p"); assert.equal(state.bootstrapRuns[runId]?.status, "STALE"); assert.ok(Object.values(state.bootstrapRuns[runId]?.rangeWork ?? {}).every((item) => item.status === "STALE"));
});

test("state replacement retries transient locks and preserves canonical state on persistent failure", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "mrr-store-fault-")); t.after(() => rm(directory, { recursive: true, force: true })); let transient = 0;
	const store = new ResearchStore(directory, { stateWriteFaultInjector(phase) { if (phase === "BEFORE_REPLACE" && transient++ < 2) throw errno("EPERM"); } }); await store.create("p", "p"); await store.transaction("p", (draft) => { (draft as MutableState).cycle = 1; }); assert.equal((await store.read("p")).cycle, 1); assert.ok(transient >= 3);
	const persistent = new ResearchStore(directory, { stateWriteFaultInjector(phase) { if (phase === "BEFORE_REPLACE") throw errno("EPERM"); } }); await assert.rejects(persistent.transaction("p", (draft) => { (draft as MutableState).cycle = 2; }), (error: any) => error.code === "EPERM"); assert.equal((await new ResearchStore(directory).read("p")).cycle, 1); const names = await readdir(join(directory, "projects", "p")); assert.ok(names.some((name) => name.endsWith(".tmp")), "failed tmp remains identifiable and non-authoritative");
});

test("state replacement crash points never authorize a tmp file or corrupt canonical JSON", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "mrr-store-crash-")); t.after(() => rm(directory, { recursive: true, force: true })); const base = new ResearchStore(directory); await base.create("p", "p");
	for (const phase of ["AFTER_TMP_WRITE", "BEFORE_REPLACE"] as const) { const crashing = new ResearchStore(directory, { stateWriteFaultInjector(at) { if (at === phase) throw new Error(`crash:${phase}`); } }); await assert.rejects(crashing.transaction("p", (draft) => { (draft as MutableState).cycle = 10; }), new RegExp(`crash:${phase}`)); assert.equal((await base.read("p")).cycle, 0); }
	const afterReplace = new ResearchStore(directory, { stateWriteFaultInjector(at) { if (at === "AFTER_REPLACE") throw new Error("crash:AFTER_REPLACE"); } }); await assert.rejects(afterReplace.transaction("p", (draft) => { (draft as MutableState).cycle = 11; }), /crash:AFTER_REPLACE/u); assert.equal((await base.read("p")).cycle, 11, "a completed replace remains the sole canonical state even if acknowledgement crashes");
});

test("separate store instances serialize concurrent per-project mutations", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "mrr-store-serial-")); t.after(() => rm(directory, { recursive: true, force: true })); const left = new ResearchStore(directory), right = new ResearchStore(directory); await left.create("p", "p");
	await Promise.all(Array.from({ length: 100 }, (_, index) => (index % 2 === 0 ? left : right).transaction("p", async (draft) => { const before = draft.cycle; await new Promise((resolve) => setTimeout(resolve, index % 3)); (draft as MutableState).cycle = before + 1; })));
	assert.equal((await left.read("p")).cycle, 100);
});

test("Windows production store stress survives hundreds of sequential writes", { skip: process.platform !== "win32" }, async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "mrr-windows-store-stress-")); t.after(() => rm(directory, { recursive: true, force: true })); const store = new ResearchStore(directory); await store.create("p", "p"); for (let index = 0; index < 200; index += 1) await store.transaction("p", (draft) => { (draft as MutableState).cycle = draft.cycle + 1; }); const state = await store.read("p"); assert.equal(state.cycle, 200); assert.equal(JSON.parse(await readFile(join(directory, "projects", "p", "state.json"), "utf8")).cycle, 200);
});

test("restart reclaims orphan RUNNING work, calls provider path, and never replays COMPLETED tasks", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "mrr-orphan-")); t.after(() => rm(directory, { recursive: true, force: true })); const store = new ResearchStore(directory), seed = new ResearchRuntime({ store, proofRunner: async () => { throw new Error("unused"); } }); await seed.createProject("p", "p"); let state = await seed.setRootObjective("p", "Root theorem"), rootObligation = Object.values(state.obligations)[0]!; const cycleId = stableId("cycle", "p", "1"), logicalJobId = stableId("job", "p", cycleId, rootObligation.obligationId), attemptId = stableId("attempt", logicalJobId, "1"), scratchPath = await store.createScratch("p", attemptId), now = new Date().toISOString();
	const decision: DecisionBasis = { decisionId: stableId("decision", cycleId), cycleId, action: "ATTACK_OBLIGATION", direction: "MODEL_DIRECTED", targetObligationId: rootObligation.obligationId, targetClaimId: rootObligation.claimId, frontier: [rootObligation.obligationId], relevantClaims: [rootObligation.claimId], failedRoutes: [], reason: "resume", budgetAllocated: 1 };
	await store.transaction("p", (draft) => { const mutable = draft as MutableState; mutable.cycle = 1; mutable.activeCycleId = cycleId; mutable.decisions = [decision]; mutable.jobs = { [logicalJobId]: { logicalJobId, projectId: "p", cycleId, obligationId: rootObligation.obligationId, status: "RUNNING", createdAt: now } }; mutable.attempts = { [attemptId]: { attemptId, logicalJobId, ordinal: 1, scratchPath, status: "RUNNING", artifactRefs: [], startedAt: now, executorInstanceId: "dead-executor", processInstanceId: 999999 } }; mutable.executionTasks = { completed: { executionTaskId: "completed", logicalJobId, attemptId, kind: "WORKER", logicalTaskId: "done", status: "COMPLETED", inputHash: "done", startedAt: now, completedAt: now, executorInstanceId: "dead-executor" }, orphan: { executionTaskId: "orphan", logicalJobId, attemptId, kind: "WORKER", logicalTaskId: "unfinished", status: "RUNNING", inputHash: "unfinished", startedAt: now, executorInstanceId: "dead-executor" } }; });
	let calls = 0; const resumed = new ResearchRuntime({ store, maxCycles: 1, proofRunner: async (request) => { calls += 1; return unresolved(request.obligation.obligationId, request.targetClaimId, request.logicalJobId, request.attemptId); } }); await resumed.run("p", 1); state = await store.read("p"); assert.equal(calls, 1); assert.equal(state.executionTasks.completed?.status, "COMPLETED"); assert.equal(state.executionTasks.orphan?.status, "FAILED_RETRYABLE"); assert.ok(state.events.some((event) => event.type === "research/orphan_execution_reclaimed"));
});

test("copied real broken Gemini cycle-5 state reclaims its one stale RUNNING task", async (t) => {
	const root = process.cwd().endsWith(`${join("", "backend")}`) ? join(process.cwd(), "..") : process.cwd(), original = join(root, "AUDIT_METADATA", "critical-layer-proof-as-test", "20260826-174219-b1dc2cb"), evidence = join(original, "evidence", "75-pre-kill-project.json"), originalProject = join(root, ".math-agent", "research", "projects", "critical-layer-proof-as-test-20260826");
	try { await readFile(evidence); await readdir(originalProject); } catch { t.skip("REAL_BROKEN_STATE_RECOVERY_TEST=NOT_AVAILABLE"); return; }
	const directory = await mkdtemp(join(tmpdir(), "mrr-real-gemini-copy-")), copiedProject = join(directory, "projects", "critical-layer-proof-as-test-20260826"); t.after(() => rm(directory, { recursive: true, force: true })); await mkdir(join(directory, "projects"), { recursive: true }); await cp(originalProject, copiedProject, { recursive: true, force: false });
	const statePath = join(copiedProject, "state.json"), copiedState = JSON.parse(await readFile(statePath, "utf8")) as ResearchProjectState, originalPrefix = originalProject.toLocaleLowerCase(); for (const artifact of Object.values(copiedState.artifacts) as any[]) if (typeof artifact.bodyPath === "string" && artifact.bodyPath.toLocaleLowerCase().startsWith(originalPrefix)) artifact.bodyPath = join(copiedProject, artifact.bodyPath.slice(originalProject.length)); for (const attempt of Object.values(copiedState.attempts) as any[]) if (typeof attempt.scratchPath === "string" && attempt.scratchPath.toLocaleLowerCase().startsWith(originalPrefix)) attempt.scratchPath = join(copiedProject, attempt.scratchPath.slice(originalProject.length)); await writeFile(statePath, `${JSON.stringify(copiedState, null, 2)}\n`);
	const beforeTasks = Object.values(copiedState.executionTasks); assert.deepEqual({ cycle: copiedState.cycle, plans: Object.keys(copiedState.executionPlans).length, tasks: beforeTasks.length, completed: beforeTasks.filter((item) => item.status === "COMPLETED").length, retryable: beforeTasks.filter((item) => item.status === "FAILED_RETRYABLE").length, running: beforeTasks.filter((item) => item.status === "RUNNING").length }, { cycle: 5, plans: 6, tasks: 36, completed: 23, retryable: 12, running: 1 }); const acceptedBefore = Object.keys(copiedState.acceptedEffects);
	let calls = 0; const store = new ResearchStore(directory), runtime = new ResearchRuntime({ store, maxCycles: 1, proofRunner: async (request) => { calls += 1; return unresolved(request.obligation.obligationId, request.targetClaimId, request.logicalJobId, request.attemptId); } }); await runtime.run(copiedState.projectId, 1); const after = await store.read(copiedState.projectId), recoveredStatus = after.executionTasks[beforeTasks.find((item) => item.status === "RUNNING")!.executionTaskId]?.status; assert.equal(calls, 1); assert.equal(Object.values(after.executionTasks).filter((item) => item.status === "COMPLETED").length >= 23, true); assert.notEqual(recoveredStatus, "RUNNING", "the stale task must be reclaimed and then advance or fail normally"); assert.ok(recoveredStatus === "COMPLETED" || recoveredStatus === "FAILED_RETRYABLE" || recoveredStatus === "INTERRUPTED"); assert.ok(acceptedBefore.every((effectId) => after.acceptedEffects[effectId] !== undefined), "all pre-crash canonical effects remain exactly once"); assert.ok(Object.keys(after.acceptedEffects).length <= acceptedBefore.length + 1, "one resumed tactical outcome may add one stable effect but cannot duplicate history"); assert.ok(after.events.some((event) => event.type === "research/orphan_execution_reclaimed"));
});

function analysis(key: string) { return { proposals: [{ entityKey: key, kind: "CLAIM" as const, statement: `Lemma ${key}`, dependencyHints: [] }], dependencies: [], warnings: [] }; }
function fullProposal<T extends Record<string, any>>(value: T): T & { targetHint: string; routeFamily: string; mechanism: string; failureMechanism: string; cases: string[] } { return { targetHint: "", routeFamily: "", mechanism: "", failureMechanism: "", cases: [], ...value }; }
function errno(code: string): NodeJS.ErrnoException { return Object.assign(new Error(code), { code }); }
function unresolved(obligationId: string, targetClaimId: string, logicalJobId: string, attemptId: string): TacticalResearchResult { const now = new Date().toISOString(); return { obligationId, targetClaimId, targetStatus: "TARGET_UNRESOLVED", contributions: [], routeObservations: [], executionReceipt: { executionReceiptId: stableId("receipt", attemptId), logicalJobId, attemptId, taskIds: [], evidenceReceiptIds: [], startedAt: now, completedAt: now }, feedback: "no result" }; }
type MutableState = { -readonly [K in keyof ResearchProjectState]: ResearchProjectState[K] };
function fixturePath(name: string): string { return join(process.cwd(), process.cwd().endsWith(`${join("", "backend")}`) ? "test" : join("backend", "test"), "fixtures", name); }
