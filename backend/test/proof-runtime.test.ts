import { strict as assert } from "node:assert";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { ProofRepository, ProofRuntime, Session, type ProofPlan, type ProofTaskInput } from "../src/index.js";

test("minimal proof runtime closes planner -> researcher -> verifier and persists typed events", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-minimal-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "proof-project", cwd: directory, directory });
	let verifierSawCandidate = "";
	const planner = {
		async plan(): Promise<ProofPlan> {
			const task: ProofTaskInput = {
				taskId: "induction-1",
				summary: "Try induction",
				description: "Give a complete induction proof of the theorem.",
			};
			return {
				actions: [
					{ action: "write_whiteboard", content: "Try induction first." },
					{ action: "spawn", tasks: [task] },
				],
			};
		},
	};
	const actualResearcher = {
		async research(context: { readonly task: { readonly taskId: string } }) {
			return {
				kind: "candidate" as const,
				candidate: {
					taskId: context.task.taskId,
					content: "For n = 1 the identity holds. If it holds for n, adding the next term gives the formula for n + 1.",
					strategy: "induction",
				},
			};
		},
	};
	const verifier = {
		async verify(candidate: { readonly content: string }) {
			verifierSawCandidate = candidate.content;
			return { verdict: "CORRECT" as const, feedback: "The induction step is complete." };
		},
	};

	const runtime = new ProofRuntime({
		session,
		obligation: { obligationId: "odd-sum", theorem: "1 + 3 + ... + (2n - 1) = n²" },
		planner,
		researcher: actualResearcher,
		verifier,
		maxSteps: 1,
	});
	const result = await runtime.run();

	assert.equal(result.status, "CANDIDATE_READY");
	assert.equal(result.candidateId, "induction-1-candidate");
	assert.equal(runtime.state.tasks.length, 1, "a simple obligation should remain a single focused worker task");
	assert.match(verifierSawCandidate, /adding the next term/);
	assert.deepEqual(
		runtime.events.map((event) => event.type),
		[
			"proof/obligation_created",
			"proof/status_changed",
			"proof/step_started",
			"proof/whiteboard_updated",
			"proof/task_dispatched",
			"proof/task_status_changed",
			"proof/research_result",
			"proof/task_status_changed",
			"proof/verification_result",
			"proof/candidate_ready",
			"proof/status_changed",
			"proof/step_finished",
		],
	);

	const proofEntries = session.customEntries("proof");
	assert.equal(proofEntries.length, runtime.events.length);
	assert.equal(proofEntries.at(-1)?.type, "proof/step_finished");
	const resumed = await Session.resume(session.filePath);
	assert.equal(resumed.customEntries("proof").length, proofEntries.length);
});

test("materializes candidates from long continuation ids under the repository slug limit", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-long-candidate-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "long-candidate", cwd: directory, directory });
	const taskId = `${"part2-ekr-bound:continuation-".repeat(6)}final`;
	const planner = {
		async plan(): Promise<ProofPlan> {
			return {
				actions: [{ action: "spawn", tasks: [{ taskId, summary: "Complete the continued proof", description: "Return a complete proof." }] }],
			};
		},
	};
	const researcher = {
		async research(context: { readonly task: { readonly taskId: string } }) {
			return { kind: "candidate" as const, candidate: { taskId: context.task.taskId, strategy: "long-id", content: "The continued proof is complete." } };
		},
	};
	const verifier = {
		async verify() {
			return { verdict: "CORRECT" as const, feedback: "The candidate is complete." };
		},
	};
	const repository = new ProofRepository(join(directory, "proof-repository"));
	const runtime = new ProofRuntime({
		session,
		obligation: { obligationId: "long-candidate", theorem: "A complete proof must be returned." },
		planner,
		researcher,
		verifier,
		repository,
		maxSteps: 1,
	});

	const result = await runtime.run();

	assert.equal(result.status, "CANDIDATE_READY");
	const items = await repository.listItems();
	assert.equal(items.length, 1);
	assert.match(items[0]?.slug ?? "", /^candidates\/[a-z0-9-]+$/u);
	assert.ok((items[0]?.slug.length ?? 101) <= 100);
});
