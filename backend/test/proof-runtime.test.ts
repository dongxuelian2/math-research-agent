import { strict as assert } from "node:assert";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { ProofRuntime, Session, type ProofPlan, type ProofTaskInput } from "../src/index.js";

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
