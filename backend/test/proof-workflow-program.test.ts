import { strict as assert } from "node:assert";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	ProofRuntime,
	Session,
	compileWorkflowPlan,
	createCandidateVerifierPool,
	type ProofCandidate,
	type ProofPlan,
	type ProofResearcher,
	type ProofVerifier,
	type ProofVerifierContext,
} from "../src/index.js";

function candidate(candidateId: string): ProofCandidate {
	return {
		candidateId,
		taskId: candidateId,
		content: `proof ${candidateId}`,
		strategy: "test",
		routeFingerprint: `route-${candidateId}`,
		claimFingerprint: `claim-${candidateId}`,
		candidateFingerprint: `candidate-${candidateId}`,
		evidence: [],
		discoveredEvidence: [],
		bodyReadEvidence: [],
		declaredEvidence: [],
		reliedOnArtifactIds: [],
		scope: "CONTRIBUTION",
		assumptions: [],
		dependencyClaims: [],
	};
}

function verifierContext(taskId: string): ProofVerifierContext {
	return {
		runId: "run",
		step: 1,
		obligation: { obligationId: "o", theorem: "T" },
		task: {
			taskId,
			summary: taskId,
			description: taskId,
			routeFingerprint: `route-${taskId}`,
			scope: "CONTRIBUTION",
			dependsOn: [],
			status: "COMPLETED",
			attempt: 1,
			updatedAt: new Date(0).toISOString(),
		},
	};
}

test("workflow compiler lowers a spawn DAG into dependency frontiers", () => {
	const plan: ProofPlan = {
		actions: [{
			action: "spawn",
			tasks: [
				{ taskId: "a", summary: "a", description: "a" },
				{ taskId: "b", summary: "b", description: "b" },
				{ taskId: "c", summary: "c", description: "c", dependsOn: ["a", "b"] },
				{ taskId: "d", summary: "d", description: "d", dependsOn: ["c"] },
			],
		}],
	};
	const compiled = compileWorkflowPlan(plan);
	assert.deepEqual(compiled.actions.map((action) => action.action === "spawn" ? action.tasks.map((task) => task.taskId) : []), [
		["a", "b"],
		["c"],
		["d"],
	]);
});

test("one controller spawn autonomously advances dependencies and injects predecessor outputs", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "proof-workflow-program-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "workflow-program", sessionId: "workflow-program", cwd: directory, directory });
	let plannerCalls = 0;
	const planner = {
		async plan(): Promise<ProofPlan> {
			plannerCalls += 1;
			if (plannerCalls === 1) {
				return {
					workflow: { strategy: "parallel foundations then synthesize" },
					actions: [{
						action: "spawn",
						tasks: [
							{ taskId: "left", summary: "left foundation", description: "derive left foundation" },
							{ taskId: "right", summary: "right foundation", description: "derive right foundation" },
							{ taskId: "synthesis", summary: "synthesize", description: "combine the two foundations", dependsOn: ["left", "right"] },
						],
					}],
				};
			}
			return { actions: [{ action: "submit_proof", candidateId: "synthesis-candidate" }] };
		},
	};
	let active = 0;
	let peak = 0;
	const researcher: ProofResearcher = {
		async research(context) {
			if (context.task.taskId === "synthesis") {
				assert.match(context.referencedMaterials, /Dependency output: left/u);
				assert.match(context.referencedMaterials, /left identity/u);
				assert.match(context.referencedMaterials, /Dependency output: right/u);
				assert.match(context.referencedMaterials, /right identity/u);
				return { kind: "candidate", candidate: { taskId: "synthesis", strategy: "merge", content: "The left and right identities imply the target theorem." } };
			}
			active += 1;
			peak = Math.max(peak, active);
			await new Promise((resolve) => setTimeout(resolve, 10));
			active -= 1;
			return { kind: "observation", content: `${context.task.taskId} identity` };
		},
	};
	const verifier: ProofVerifier = {
		async verify(_candidate, context) {
			if (context.task.taskId === "synthesis") {
				assert.match(context.referencedMaterials ?? "", /left identity/u);
				assert.match(context.referencedMaterials ?? "", /right identity/u);
			}
			return { verdict: "CORRECT", feedback: "complete" };
		},
	};
	const runtime = new ProofRuntime({
		session,
		obligation: { obligationId: "o", theorem: "The two foundation identities imply the target theorem." },
		planner,
		researcher,
		verifier,
		maxWorkers: 2,
		maxSteps: 2,
		workspaceDirectory: join(directory, "run"),
	});
	const result = await runtime.run();
	assert.equal(result.status, "PROVED");
	assert.equal(plannerCalls, 2, "dependency barriers inside one spawn must not consume extra Planner turns");
	assert.equal(peak, 2, "independent first-frontier workers should still fan out in parallel");
	assert.equal(runtime.state.tasks.find((task) => task.taskId === "left")?.status, "COMPLETED");
	assert.equal(runtime.state.tasks.find((task) => task.taskId === "right")?.status, "COMPLETED");
	assert.equal(runtime.state.tasks.find((task) => task.taskId === "synthesis")?.status, "COMPLETED");
	assert.equal(runtime.state.executionPlans[0]?.actionExecutions.length, 2, "compiled frontiers must be durable action receipts");
});

test("candidate verifier pool isolates concurrent verifier sessions and reuses the same candidate identity", async () => {
	const created = new Map<string, number>();
	const active = new Set<string>();
	const pool = createCandidateVerifierPool(async (candidateId) => {
		created.set(candidateId, (created.get(candidateId) ?? 0) + 1);
		return {
			async verify() {
				assert.equal(active.has(candidateId), false, `candidate ${candidateId} verifier was re-entered concurrently`);
				active.add(candidateId);
				await new Promise((resolve) => setTimeout(resolve, 5));
				active.delete(candidateId);
				return { verdict: "CORRECT" as const, feedback: "ok" };
			},
		};
	});

	await Promise.all([
		pool.verify(candidate("a"), verifierContext("a")),
		pool.verify(candidate("b"), verifierContext("b")),
	]);
	await Promise.all([
		pool.verify(candidate("a"), verifierContext("a")),
		pool.verify(candidate("a"), verifierContext("a")),
	]);
	assert.deepEqual(Object.fromEntries(created), { a: 1, b: 1 });
});
