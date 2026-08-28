import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { ProofRuntime, Session, type ArtifactRef, type ProofPlan, type ProofPlanner, type ProofResearcher, type ProofVerifier } from "../src/index.js";

test("maxWorkers bounds concurrency without silently dropping any of seven logical tasks", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "proof-queue-")); t.after(async () => rm(directory, { recursive: true, force: true })); const session = await Session.create({ projectId: "queue", sessionId: "queue", cwd: directory, directory });
	const planner: ProofPlanner = { async plan() { return { actions: [{ action: "spawn", tasks: Array.from({ length: 7 }, (_, index) => ({ taskId: `t${index}`, summary: `task ${index}`, description: `prove part ${index}` })) }] }; } };
	let active = 0, peak = 0, calls = 0; const researcher: ProofResearcher = { async research(context) { calls += 1; active += 1; peak = Math.max(peak, active); await new Promise((resolve) => setTimeout(resolve, 5)); active -= 1; return { kind: "candidate", candidate: { taskId: context.task.taskId, content: `proof ${context.task.taskId}`, strategy: "cases" } }; } };
	const verifier: ProofVerifier = { async verify() { return { verdict: "CORRECT", feedback: "correct" }; } }; const runtime = new ProofRuntime({ session, obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, maxWorkers: 3, maxSteps: 1, workspaceDirectory: join(directory, "run") }); await runtime.run();
	assert.equal(calls, 7); assert.equal(runtime.state.tasks.length, 7); assert.ok(peak <= 3); assert.equal(runtime.state.candidates.length, 7);
});

test("task-level crash resume reuses completed workers and runs only missing worker and verifiers", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "proof-task-resume-")); t.after(async () => rm(directory, { recursive: true, force: true })); const session = await Session.create({ projectId: "resume", sessionId: "resume", cwd: directory, directory });
	let plannerCalls = 0; const planner: ProofPlanner = { async plan() { plannerCalls += 1; if (plannerCalls > 1) return { actions: [{ action: "spawn", tasks: [{ taskId: "WRONG", summary: "wrong replan", description: "must never replace the unfinished step" }] }] }; return { actions: [{ action: "spawn", tasks: ["A", "B", "C"].map((id) => ({ taskId: id, summary: id, description: `prove ${id}` })) }] }; } };
	const workerCalls = new Map<string, number>(), verifierCalls = new Map<string, number>(); const researcher: ProofResearcher = { async research(context) { workerCalls.set(context.task.taskId, (workerCalls.get(context.task.taskId) ?? 0) + 1); return { kind: "candidate", candidate: { taskId: context.task.taskId, content: `proof ${context.task.taskId}`, strategy: "independent" } }; } }; const verifier: ProofVerifier = { async verify(candidate) { verifierCalls.set(candidate.taskId, (verifierCalls.get(candidate.taskId) ?? 0) + 1); return { verdict: "CORRECT", feedback: "correct" }; } };
	let runtime = new ProofRuntime({ session, obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, runId: "durable", maxWorkers: 1, maxSteps: 1, workspaceDirectory: join(directory, "run"), faultAfterWorkerResults: 2 }); const interrupted = await runtime.run(); assert.equal(interrupted.status, "FAILED"); assert.deepEqual(Object.fromEntries(workerCalls), { A: 1, B: 1 }); assert.equal(verifierCalls.size, 0);
	runtime = new ProofRuntime({ session, obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, runId: "durable", maxWorkers: 1, maxSteps: 1, workspaceDirectory: join(directory, "run") }); await runtime.run(); assert.deepEqual(Object.fromEntries(workerCalls), { A: 1, B: 1, C: 1 }); assert.deepEqual(Object.fromEntries(verifierCalls), { A: 1, B: 1, C: 1 }); assert.equal(runtime.state.candidates.length, 3); assert.equal(plannerCalls, 1, "the unfinished persisted step must resume without a Planner call"); assert.equal(runtime.state.executionPlans[0]?.status, "COMPLETED");
});

test("session receipts ahead of state snapshot prevent duplicate dynamic Worker and Verifier calls", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "proof-session-ahead-")); t.after(async () => rm(directory, { recursive: true, force: true }));
	let plannerCalls = 0, workerCalls = 0, verifierCalls = 0;
	const planner: ProofPlanner = { async plan() { plannerCalls += 1; return { actions: [{ action: "spawn", tasks: [{ taskId: "stable-task", summary: "stable task", description: "produce one durable candidate" }] }] }; } };
	const researcher: ProofResearcher = { async research(context) { workerCalls += 1; return { kind: "candidate", candidate: { taskId: context.task.taskId, content: "durable proof", strategy: "stable" } }; } };
	const verifier: ProofVerifier = { async verify() { verifierCalls += 1; return { verdict: "CORRECT", feedback: "durably verified" }; } };
	const workspaceDirectory = join(directory, "run"), session = await Session.create({ projectId: "session-ahead", sessionId: "session-ahead", cwd: directory, directory });
	const base = { obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, runId: "session-ahead", maxWorkers: 1, maxSteps: 1, workspaceDirectory } as const;
	let runtime = new ProofRuntime({ ...base, session }); await runtime.run();
	assert.deepEqual({ plannerCalls, workerCalls, verifierCalls }, { plannerCalls: 1, workerCalls: 1, verifierCalls: 1 });

	// Model a crash after the append-only verification receipt was flushed but
	// before the newer state snapshot replaced its predecessor.
	const statePath = join(workspaceDirectory, "state.json"), stale = JSON.parse(await readFile(statePath, "utf8"));
	stale.status = "RUNNING"; stale.candidates = []; stale.verifications = {};
	stale.executionPlans = stale.executionPlans.map((plan: any) => ({ ...plan, status: "RUNNING", completedAt: undefined, actionExecutions: plan.actionExecutions.map((action: any) => ({ ...action, status: "RUNNING", completedAt: undefined })) }));
	await writeFile(statePath, `${JSON.stringify(stale, null, 2)}\n`);

	runtime = new ProofRuntime({ ...base, session: await Session.resume(session.filePath) }); await runtime.run();
	assert.deepEqual({ plannerCalls, workerCalls, verifierCalls }, { plannerCalls: 1, workerCalls: 1, verifierCalls: 1 });
	assert.equal(runtime.state.verifications["stable-task-candidate"]?.verdict, "CORRECT");
	assert.equal(runtime.state.executionPlans[0]?.actionExecutions[0]?.status, "COMPLETED");
});

test("full plan action resume skips completed literature and computation actions and resumes only missing spawn work", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "proof-action-resume-")); t.after(async () => rm(directory, { recursive: true, force: true })); const session = await Session.create({ projectId: "action-resume", sessionId: "action-resume", cwd: directory, directory });
	let plannerCalls = 0, literatureCalls = 0, computationCalls = 0; const workerCalls = new Map<string, number>(), verifierCalls = new Map<string, number>();
	const planner: ProofPlanner = { async plan() { plannerCalls += 1; return { actions: [{ action: "literature_search", query: "exact Q" }, { action: "use_tool", toolName: "compute", input: { expression: "6*7" } }, { action: "spawn", tasks: ["A", "B", "C"].map((id) => ({ taskId: id, summary: id, description: `prove ${id}` })) }] }; } }, literatureSearcher = { async search() { literatureCalls += 1; return { content: "persisted literature result", sources: ["source-Q"] }; } }, tools = [{ name: "compute", description: "instrumented computation", async execute() { computationCalls += 1; return { value: 42 }; } }];
	const researcher: ProofResearcher = { async research(context) { workerCalls.set(context.task.taskId, (workerCalls.get(context.task.taskId) ?? 0) + 1); return { kind: "candidate", candidate: { taskId: context.task.taskId, content: `proof ${context.task.taskId}`, strategy: "independent" } }; } }, verifier: ProofVerifier = { async verify(candidate) { verifierCalls.set(candidate.taskId, (verifierCalls.get(candidate.taskId) ?? 0) + 1); return { verdict: "CORRECT", feedback: "correct" }; } }, base = { session, obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, literatureSearcher, tools, runId: "action-durable", maxWorkers: 1, maxSteps: 1, workspaceDirectory: join(directory, "run") } as const;
	let runtime = new ProofRuntime({ ...base, faultAfterWorkerResults: 2 }); const interrupted = await runtime.run(); assert.equal(interrupted.status, "FAILED"); assert.equal(plannerCalls, 1); assert.equal(literatureCalls, 1); assert.equal(computationCalls, 1); assert.deepEqual(Object.fromEntries(workerCalls), { A: 1, B: 1 }); assert.deepEqual(runtime.state.executionPlans[0]?.actionExecutions.map((item) => item.status), ["COMPLETED", "COMPLETED", "INTERRUPTED"]);
	runtime = new ProofRuntime(base); await runtime.run(); assert.equal(plannerCalls, 1, "the persisted Planner plan must be reused"); assert.equal(literatureCalls, 1, "completed literature action must not run again"); assert.equal(computationCalls, 1, "completed computation action must not run again"); assert.deepEqual(Object.fromEntries(workerCalls), { A: 1, B: 1, C: 1 }); assert.deepEqual(Object.fromEntries(verifierCalls), { A: 1, B: 1, C: 1 }); assert.deepEqual(runtime.state.executionPlans[0]?.actionExecutions.map((item) => item.status), ["COMPLETED", "COMPLETED", "COMPLETED"]); assert.ok(runtime.state.executionPlans[0]?.actionExecutions.slice(0, 2).every((item) => item.completedAt !== undefined && item.result !== undefined));
});

test("an invalidated plan dependency marks the old plan stale and replans the same step", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "proof-plan-invalidation-")); t.after(async () => rm(directory, { recursive: true, force: true })); const session = await Session.create({ projectId: "invalidate", sessionId: "invalidate", cwd: directory, directory }), dependency: ArtifactRef = { artifactId: "X", contentHash: "a".repeat(64) };
	let plannerCalls = 0, invalid = false; const planner: ProofPlanner = { async plan(): Promise<ProofPlan> { plannerCalls += 1; const id = plannerCalls === 1 ? "A" : "B"; return { actions: [{ action: "spawn", tasks: [{ taskId: id, summary: id, description: `prove ${id}` }] }] }; } };
	const calls: string[] = [], researcher: ProofResearcher = { async research(context) { calls.push(context.task.taskId); return { kind: "candidate", candidate: { taskId: context.task.taskId, content: `proof ${context.task.taskId}`, strategy: "direct" } }; } }, verifier: ProofVerifier = { async verify() { return { verdict: "CORRECT", feedback: "correct" }; } };
	let runtime = new ProofRuntime({ session, obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, runId: "invalidated", maxWorkers: 1, maxSteps: 1, workspaceDirectory: join(directory, "run"), planDependencies: [dependency], planDependencyValidator: async () => invalid ? "artifact X was invalidated" : undefined, faultAfterWorkerResults: 1 }); await runtime.run(); assert.equal(plannerCalls, 1); invalid = true;
	runtime = new ProofRuntime({ session, obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, runId: "invalidated", maxWorkers: 1, maxSteps: 1, workspaceDirectory: join(directory, "run"), planDependencies: [dependency], planDependencyValidator: async () => invalid ? "artifact X was invalidated" : undefined }); await runtime.run(); assert.equal(plannerCalls, 2); assert.deepEqual(calls, ["A", "B"]); assert.equal(runtime.state.executionPlans[0]?.status, "STALE"); assert.match(runtime.state.executionPlans[0]?.staleReason ?? "", /artifact X/); assert.equal(runtime.state.executionPlans[1]?.status, "COMPLETED");
});

test("completed STOP rehydrates terminal state and finalizes its plan after a crash", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "proof-stop-resume-")); t.after(async () => rm(directory, { recursive: true, force: true })); const session = await Session.create({ projectId: "stop-resume", sessionId: "stop-resume", cwd: directory, directory }); let plannerCalls = 0;
	const planner: ProofPlanner = { async plan() { plannerCalls += 1; return { actions: [{ action: "stop", reason: "Terminal mathematical partial result" }] }; } }, researcher: ProofResearcher = { async research() { throw new Error("STOP must not dispatch work"); } }, verifier: ProofVerifier = { async verify() { throw new Error("STOP must not verify work"); } }, base = { session, obligation: { obligationId: "o", theorem: "T" }, planner, researcher, verifier, runId: "durable-stop", maxSteps: 1, workspaceDirectory: join(directory, "run") } as const;
	let runtime = new ProofRuntime({ ...base, faultAfterCompletedActionReceipts: 1 }); const interrupted = await runtime.run(); assert.equal(interrupted.status, "FAILED"); assert.equal(plannerCalls, 1); assert.equal(runtime.state.executionPlans[0]?.status, "RUNNING"); assert.equal(runtime.state.executionPlans[0]?.actionExecutions[0]?.status, "COMPLETED"); assert.equal(runtime.state.executionPlans[0]?.actionExecutions[0]?.result?.terminalStatus, "PARTIAL");
	runtime = new ProofRuntime(base); const resumed = await runtime.run(); assert.equal(resumed.status, "PARTIAL"); assert.equal(resumed.reason, "Terminal mathematical partial result"); assert.equal(plannerCalls, 1, "completed STOP must not rerun Planner"); assert.equal(runtime.state.executionPlans[0]?.status, "COMPLETED"); assert.equal(runtime.state.executionPlans[0]?.actionExecutions[0]?.status, "COMPLETED"); assert.equal(runtime.state.stepHistory[0]?.status, "completed");
});
