import { strict as assert } from "node:assert";
import { mkdtemp, rm } from "node:fs/promises";
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
			return synthesisResearcher;
		},
		maxSteps: 3,
		maxWorkers: 2,
	});
	const result = await runtime.run();

	assert.equal(result.status, "PROVED");
	assert.equal(plannerCalls, 3);
	assert.deepEqual(factoryAgents, ["synthesizer:Assemble the final argument"]);
	assert.equal(runtime.state.tasks.find((item) => item.taskId === "foundation")?.status, "COMPLETED");
	assert.equal(runtime.state.tasks.find((item) => item.taskId === "synthesis")?.status, "COMPLETED");
	assert.equal(runtime.state.executionPlans[0]?.plan.workflow?.strategy, "derive a local fact, then synthesize the target");
	assert.ok(runtime.events.some((event) => event.type === "proof/task_status_changed" && event.status === "PARTIAL") === false);
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
