import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	ProofRuntime,
	Session,
	createAgentProofResearcher,
	parsePlannerPlan,
	type AgentRunResult,
	type ProofPlan,
	type ProofResearchContext,
	type ProofResearcher,
	type ProofTask,
	type ProofVerifierContext,
} from "../src/index.js";
import type { Agent } from "../src/agent/types.js";

function task(taskId: string, summary: string, extra: Partial<ProofTask> = {}): ProofTask {
	return {
		taskId,
		summary,
		description: summary,
		routeFingerprint: `${taskId}-route`,
		scope: "CONTRIBUTION",
		dependsOn: [],
		status: "RUNNING",
		attempt: 1,
		updatedAt: new Date(0).toISOString(),
		kind: "MATHEMATICAL",
		...extra,
	};
}

function assistantResult(text: string, stopReason: "end_turn" | "length" = "end_turn"): AgentRunResult {
	return {
		runId: "fake-agent-run",
		messages: [{
			role: "assistant",
			id: "assistant-message",
			content: [{ kind: "text", text }],
			stopReason,
			provider: "mock",
			model: "mock",
			timestamp: Date.now(),
		}],
		stopReason: "completed",
	};
}

function fakeAgent(result: AgentRunResult): Agent {
	return {
		state: { status: "idle", messages: [] },
		async prompt() { return result; },
		steer() {},
		followUp() {},
		async abort() {},
		subscribe() { return () => {}; },
	};
}

test("dynamic workflow executes a ready frontier and lets the controller continue it", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-dynamic-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "dynamic-workflow", cwd: directory, directory });
	const plannerSteps: Array<{ step: number; tasks: ProofTask[] }> = [];
	const factoryAgents: string[] = [];
	let plannerCalls = 0;
	const planner = {
		async plan(context: { readonly step: number; readonly tasks: readonly ProofTask[] }): Promise<ProofPlan> {
			plannerCalls += 1;
			plannerSteps.push({ step: context.step, tasks: [...context.tasks] });
			if (context.step === 1) {
				return {
					workflow: { strategy: "derive a local fact, then synthesize the target", successCriteria: ["all prerequisites completed", "independent verification"] },
					actions: [{
						action: "spawn",
						tasks: [
							{ taskId: "foundation", summary: "Derive foundation", description: "Derive the local foundation fact." },
							{
								taskId: "synthesis",
								summary: "Synthesize proof",
								description: "Use the completed foundation fact to give the final proof.",
								dependsOn: ["foundation"],
								agent: { agentId: "synthesizer", purpose: "Assemble the final argument", capabilities: ["reason", "synthesize"] },
							},
						],
					}],
				};
			}
			if (context.step === 2) {
				assert.equal(context.tasks.find((item) => item.taskId === "foundation")?.status, "COMPLETED");
				assert.equal(context.tasks.find((item) => item.taskId === "synthesis")?.status, "PENDING");
				return {
					actions: [{
						action: "spawn",
						tasks: [{
							taskId: "synthesis",
							summary: "Synthesize proof",
							description: "Use the completed foundation fact to give the final proof.",
							dependsOn: ["foundation"],
							agent: { agentId: "synthesizer", purpose: "Assemble the final argument", capabilities: ["reason", "synthesize"] },
						}],
					}],
				};
			}
			return { actions: [{ action: "submit_proof", candidateId: "synthesis-candidate" }] };
		},
	};
	const researcher: ProofResearcher = {
		async research(context) {
			assert.equal(context.task.taskId, "foundation");
			return { kind: "observation", content: "The foundation identity is established." };
		},
	};
	const synthesisResearcher: ProofResearcher = {
		async research(context) {
			assert.equal(context.task.agent?.agentId, "synthesizer");
			return { kind: "candidate", candidate: { taskId: context.task.taskId, strategy: "synthesis", content: "The foundation identity implies the target theorem." } };
		},
	};
	const verifier = {
		async verify(candidate: { readonly content: string }, _context: ProofVerifierContext) {
			assert.match(candidate.content, /foundation/iu);
			return { verdict: "CORRECT" as const, feedback: "The synthesized argument is complete." };
		},
	};

	const runtime = new ProofRuntime({
		session,
		obligation: { obligationId: "dynamic", theorem: "The foundation identity implies the target theorem." },
		planner,
		researcher,
		verifier,
		agentFactory: async (spec) => {
			factoryAgents.push(`${spec.agentId}:${spec.purpose}`);
			return spec.agentId === "synthesizer" ? synthesisResearcher : researcher;
		},
		maxSteps: 3,
		maxWorkers: 2,
	});
	const result = await runtime.run();

	assert.equal(result.status, "PROVED");
	assert.equal(plannerCalls, 3);
	assert.deepEqual(factoryAgents, ["foundation:Derive foundation", "synthesizer:Assemble the final argument"]);
	assert.equal(runtime.state.tasks.find((item) => item.taskId === "foundation")?.status, "COMPLETED");
	assert.equal(runtime.state.tasks.find((item) => item.taskId === "synthesis")?.status, "COMPLETED");
	assert.equal(runtime.state.executionPlans[0]?.plan.workflow?.strategy, "derive a local fact, then synthesize the target");
	assert.ok(runtime.events.some((event) => event.type === "proof/task_status_changed" && event.status === "PARTIAL") === false);
});

test("dynamic runtime preserves and resumes a partial task when the controller omits continuationOf", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-continuation-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "dynamic-continuation", cwd: directory, directory });
	let plannerCalls = 0;
	const workerTaskIds: string[] = [];
	const factoryAgentIds: string[] = [];
	const planner = {
		async plan(context: { readonly step: number; readonly tasks: readonly ProofTask[] }): Promise<ProofPlan> {
			plannerCalls += 1;
			if (context.step === 1) {
				return {
					actions: [{
						action: "spawn",
						tasks: [{
							taskId: "long-task",
							summary: "Complete the proof obligation",
							description: "Produce a complete, self-contained proof of the active obligation.",
							successCriteria: "Every stated obligation is discharged and the result can be independently verified.",
						}],
					}],
				};
			}
			if (context.step === 2) {
				assert.equal(context.tasks.find((item) => item.taskId === "long-task")?.status, "PARTIAL");
				assert.equal(context.tasks.some((item) => item.continuationOf === "long-task"), false);
				return { actions: [{ action: "stop", reason: "The controller did not add another task." }] };
			}
			const continuation = context.tasks.find((item) => item.continuationOf === "long-task");
			assert.equal(continuation?.status, "COMPLETED");
			return { actions: [{ action: "submit_proof", candidateId: `${continuation?.taskId}-candidate` }] };
		},
	};
	const researcher: ProofResearcher = {
		async research(context) {
			workerTaskIds.push(context.task.taskId);
			if (context.task.continuationOf === undefined) {
				return {
					kind: "partial",
					content: "The first part of the argument is complete; the final justification is still missing.",
					reason: "The worker turn ended before the complete result was returned.",
					suggestedNext: "Finish the final justification using the preserved argument.",
				};
			}
			return {
				kind: "candidate",
				candidate: {
					taskId: context.task.taskId,
					strategy: "continue-preserved-argument",
					content: "The preserved argument is completed by the missing final justification.",
				},
			};
		},
	};
	const verifier = {
		async verify(_candidate: { readonly content: string }, _context: ProofVerifierContext) {
			return { verdict: "CORRECT" as const, feedback: "The continued proof is complete." };
		},
	};
	const runtime = new ProofRuntime({
		session,
		obligation: { obligationId: "continuation", theorem: "A complete proof must be returned." },
		planner,
		researcher,
		verifier,
		agentFactory: async (spec) => {
			factoryAgentIds.push(spec.agentId);
			return researcher;
		},
		maxSteps: 3,
		maxWorkers: 1,
	});

	const result = await runtime.run();

	assert.equal(result.status, "PROVED");
	assert.equal(plannerCalls, 3);
	assert.deepEqual(workerTaskIds, ["long-task", "long-task:continuation-1"]);
	assert.deepEqual(factoryAgentIds, ["long-task", "long-task"]);
	assert.equal(runtime.state.tasks.find((item) => item.taskId === "long-task")?.status, "PARTIAL");
	assert.equal(runtime.state.tasks.find((item) => item.continuationOf === "long-task")?.status, "COMPLETED");
	const continuationPlan = runtime.state.executionPlans[1]?.plan.actions[0];
	assert.equal(continuationPlan?.action, "spawn");
	if (continuationPlan?.action === "spawn") assert.equal(continuationPlan.tasks[0]?.continuationOf, "long-task");
});

test("dynamic formalization retries compiler failures until the Lean process gate passes", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-formal-repair-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "dynamic-formal-repair", cwd: directory, directory });
	const planner = {
		async plan(context: { readonly step: number }): Promise<ProofPlan> {
			if (context.step === 1) {
				return { actions: [{ action: "spawn", tasks: [{ taskId: "informal", summary: "Prove the target", description: "Give the complete informal proof." }] }] };
			}
			if (context.step === 2) return { actions: [{ action: "submit_proof", candidateId: "informal-candidate" }, { action: "stop", reason: "The controller mistakenly tried to stop before formalization." }] };
			return { actions: [{ action: "stop", reason: "The controller has no more work." }] };
		},
	};
	const researcher: ProofResearcher = {
		async research(context) {
			if (context.task.kind === "FORMALIZATION") {
				assert.match(context.task.description, /translate the original mathematical theorem/iu);
				return {
					kind: "candidate",
					candidate: {
						taskId: context.task.taskId,
						strategy: "lean-repair",
						content: context.task.continuationOf === undefined
							? "theorem sample : True := by\n  exact False.elim (by contradiction)"
							: "theorem sample : True := by\n  trivial",
					},
				};
			}
			return { kind: "candidate", candidate: { taskId: context.task.taskId, strategy: "direct", content: "True is inhabited by its constructor." } };
		},
	};
	const verifier = { async verify() { return { verdict: "CORRECT" as const, feedback: "The informal proof is correct." }; } };
	const checkedSources: string[] = [];
	const formalVerifier = {
		async verify(content: string) {
			checkedSources.push(content);
			return content.includes("trivial")
				? { ok: true, feedback: "Lean verification passed." }
				: { ok: false, feedback: "unknown tactic contradiction", failureKind: "REJECTED" as const };
		},
	};
	const runtime = new ProofRuntime({
		session,
		obligation: { obligationId: "formal-repair", theorem: "True." },
		mode: "prove_and_formalize",
		planner,
		researcher,
		verifier,
		formalVerifier,
		agentFactory: async () => researcher,
		maxSteps: 4,
		maxWorkers: 1,
	});

	const result = await runtime.run();

	assert.equal(result.status, "PROVED");
	assert.equal(checkedSources.length, 2);
	assert.equal(runtime.state.formalAttempts.length, 2);
	assert.equal(runtime.state.formalAttempts[0]?.result.ok, false);
	assert.equal(runtime.state.formalAttempts[1]?.result.ok, true);
	assert.equal(runtime.state.tasks.filter((item) => item.kind === "FORMALIZATION").length, 2);
	assert.equal(runtime.state.tasks.find((item) => item.taskId === "formal-proof-attempt-1")?.status, "FAILED_RETRYABLE");
	assert.equal(runtime.state.tasks.find((item) => item.continuationOf === "formal-proof-attempt-1")?.status, "COMPLETED");
	assert.match(await readFile(result.proofLeanPath ?? "", "utf8"), /trivial/u);
});

test("model output truncation becomes a resumable partial result instead of a candidate", async () => {
	const researcher = createAgentProofResearcher(fakeAgent(assistantResult('{"kind":"candidate","candidate":{"content":"unfinished proof', "length")));
	const context: ProofResearchContext = {
		runId: "run",
		step: 1,
		obligation: { obligationId: "o", theorem: "T" },
		whiteboard: "",
		task: task("truncated", "Write a complete proof", { status: "RUNNING" }),
		referencedMaterials: "",
	};
	const result = await researcher.research(context);

	assert.equal(result.kind, "partial");
	if (result.kind === "partial") {
		assert.match(result.reason, /output limit/iu);
		assert.match(result.content, /unfinished proof/iu);
	}

	const turnLimited = createAgentProofResearcher(fakeAgent({ ...assistantResult("partial proof"), stopReason: "max_turns" }));
	const turnLimitedResult = await turnLimited.research(context);
	assert.equal(turnLimitedResult.kind, "partial");
	if (turnLimitedResult.kind === "partial") assert.match(turnLimitedResult.reason, /turn budget/iu);
});

test("worker returns plain mathematical text and the runtime owns the result envelope", async () => {
	let capturedPrompt = "";
	const proof = String.raw`Let \(S = \{1,2,\ldots,n\}\). Since \(\pi\) is a permutation, the two sides are equal.`;
	const agent: Agent = {
		state: { status: "idle", messages: [] },
		async prompt(input) {
			capturedPrompt = input as string;
			return assistantResult(proof);
		},
		steer() {},
		followUp() {},
		async abort() {},
		subscribe() { return () => {}; },
	};
	const researcher = createAgentProofResearcher(agent);
	const result = await researcher.research({
		runId: "run",
		step: 1,
		obligation: { obligationId: "o", theorem: "A theorem with a LaTeX proof." },
		whiteboard: "",
		task: task("plain-text", "Write the proof"),
		referencedMaterials: "",
	});

	assert.equal(result.kind, "candidate");
	if (result.kind === "candidate") {
		assert.equal(result.candidate.taskId, "plain-text");
		assert.equal(result.candidate.content, proof);
		assert.equal(result.candidate.strategy, "agent-reasoning");
	}
	assert.match(capturedPrompt, /Return only the generated result body as plain text/u);
	assert.doesNotMatch(capturedPrompt, /Return JSON when possible/u);
});

test("invalid JSON-looking worker text is preserved as proof text instead of downgraded to partial", async () => {
	const malformedJson = String.raw`{"kind":"candidate","candidate":{"content":"Proof with LaTeX \{x\}"}}`;
	const result = await createAgentProofResearcher(fakeAgent(assistantResult(malformedJson))).research({
		runId: "run",
		step: 1,
		obligation: { obligationId: "o", theorem: "T" },
		whiteboard: "",
		task: task("malformed-json", "Preserve the proof"),
		referencedMaterials: "",
	});

	assert.equal(result.kind, "candidate");
	if (result.kind === "candidate") assert.equal(result.candidate.content, malformedJson);
});

test("dynamic worker accepts the dedicated formalizer lean response shape", async () => {
	const researcher = createAgentProofResearcher(fakeAgent(assistantResult('{"lean":"theorem sample : True := by\\n  trivial","notes":"checked by the process gate next"}')));
	const result = await researcher.research({
		runId: "run",
		step: 1,
		obligation: { obligationId: "o", theorem: "True" },
		whiteboard: "",
		task: task("formal", "Formalize the target", { kind: "FORMALIZATION", scope: "TARGET" }),
		referencedMaterials: "",
	});

	assert.equal(result.kind, "candidate");
	if (result.kind === "candidate") {
		assert.equal(result.candidate.strategy, "lean-formalization");
		assert.match(result.candidate.content, /theorem sample : True/u);
	}
});

test("proof roles retry transient provider failures before surfacing a block", async () => {
	let calls = 0;
	const transientAgent: Agent = {
		state: { status: "idle", messages: [] },
		async prompt() {
			calls += 1;
			if (calls === 1) return { ...assistantResult(""), stopReason: "model_error", error: { name: "TypeError", message: "fetch failed" } };
			return assistantResult('{"kind":"candidate","candidate":{"content":"True is inhabited.","strategy":"direct"}}');
		},
		steer() {},
		followUp() {},
		async abort() {},
		subscribe() { return () => {}; },
	};
	const researcher = createAgentProofResearcher(transientAgent);
	const result = await researcher.research({
		runId: "run",
		step: 1,
		obligation: { obligationId: "o", theorem: "True" },
		whiteboard: "",
		task: task("retry", "Prove the target"),
		referencedMaterials: "",
	});

	assert.equal(calls, 2);
	assert.equal(result.kind, "candidate");
});

test("planner parser tolerates legacy singular task responses while exposing a normalized task", () => {
	const plan = parsePlannerPlan(`{"actions":[{"action":"spawn","task":"derive the local identity"}]}`);
	const action = plan.actions[0];
	assert.equal(action?.action, "spawn");
	if (action?.action === "spawn") {
		assert.equal(action.tasks.length, 1);
		assert.equal(action.tasks[0]?.description, "derive the local identity");
		assert.equal(action.tasks[0]?.summary, "derive the local identity");
	}
});

test("planner parser accepts a single JSON action emitted without the actions wrapper", () => {
	const plan = parsePlannerPlan(`{"action":"spawn","tasks":[{"taskId":"direct","description":"derive the local identity"}]}`);
	assert.equal(plan.actions.length, 1);
	assert.equal(plan.actions[0]?.action, "spawn");
});

test("planner parser normalizes model-friendly lowercase contribution kinds and scopes", () => {
	const plan = parsePlannerPlan(`{"actions":[{"action":"spawn","tasks":[{"taskId":"target","summary":"close target","description":"prove target","scope":"target","targetClaimId":"claim","contributionKind":"lemma"}]}]}`);
	const action = plan.actions[0];
	assert.equal(action?.action, "spawn");
	if (action?.action === "spawn") {
		assert.equal(action.tasks[0]?.scope, "TARGET");
		assert.equal(action.tasks[0]?.contributionKind, "LEMMA");
	}
});

test("unknown optional contribution metadata does not discard a complete target candidate", async () => {
	const researcher = createAgentProofResearcher(fakeAgent(assistantResult(JSON.stringify({
		kind: "candidate",
		candidate: {
			content: "The additive identity axiom gives n + 0 = n for every integer n.",
			scope: "TARGET",
			contribution: { kind: "THEORETICAL_RESULT", statement: "n + 0 = n", relationshipToTarget: "target" },
		},
	}))));
	const result = await researcher.research({
		runId: "run",
		step: 1,
		obligation: { obligationId: "o", theorem: "For every integer n, n + 0 = n." },
		whiteboard: "",
		task: task("target", "close target", { scope: "TARGET" }),
		referencedMaterials: "",
	});
	assert.equal(result.kind, "candidate");
	if (result.kind === "candidate") assert.equal(result.candidate.contribution, undefined);
});
