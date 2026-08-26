import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	ProofRuntime,
	Session,
	type ProofPlan,
	type ProofResearchContext,
	type ProofVerifierContext,
} from "../src/index.js";

const theorem = "For every n >= 1, 1 + 3 + ... + (2n - 1) = n^2.";

test("full proof actions persist repository, whiteboard, parallel workers, failed routes, and submit gate", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-workflow-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "workflow", cwd: directory, directory });
	let activeResearchers = 0;
	let peakResearchers = 0;
	const researched: string[] = [];
	let releaseFirstWave: (() => void) | undefined;
	const firstWaveReady = new Promise<void>((resolve) => { releaseFirstWave = resolve; });
	const planner = {
		async plan(context: { readonly step: number }): Promise<ProofPlan> {
			if (context.step === 1) {
				return {
					actions: [
						{
							action: "write_items",
						items: [{ slug: "lemmas/odd-sum-base", summary: "Base case", content: "The case n = 1 is immediate." }],
						},
						{ action: "write_whiteboard", content: "Explore induction and preserve rejected routes." },
						{
							action: "spawn",
							tasks: [
								{ taskId: "route-a", summary: "Try a flawed shortcut", description: "Try the shortcut proof route A." },
								{ taskId: "route-b", summary: "Try induction", description: "Try an induction proof using [[lemmas/odd-sum-base]]." },
							],
						},
					],
				};
			}
			if (context.step === 2) {
				return {
					actions: [
						{
							action: "spawn",
							tasks: [
								{ taskId: "route-a-retry", summary: "Repeat route A", description: "Try the shortcut proof route A." },
								{ taskId: "route-c", summary: "Repair by induction", description: "Give a complete induction proof with an explicit algebraic step." },
							],
						},
					],
				};
			}
			return { actions: [{ action: "submit_proof", candidateId: "route-c-candidate" }] };
		},
	};
	const researcher = {
		async research(context: ProofResearchContext) {
			researched.push(context.task.taskId);
			const invocation = researched.length;
			activeResearchers += 1;
			peakResearchers = Math.max(peakResearchers, activeResearchers);
			if (invocation === 2) releaseFirstWave?.();
			if (invocation <= 2) await Promise.race([firstWaveReady, new Promise<void>((resolve) => setTimeout(resolve, 500))]);
			await new Promise((resolve) => setTimeout(resolve, 15));
			activeResearchers -= 1;
			if (context.task.taskId === "route-a") {
				return {
					kind: "candidate" as const,
					candidate: { taskId: context.task.taskId, strategy: "shortcut", content: "wrong proof" },
				};
			}
			if (context.task.taskId === "route-b") {
				return { kind: "observation" as const, content: "The base case is available; this route needs a stronger induction step." };
			}
			return {
				kind: "candidate" as const,
				candidate: {
					taskId: context.task.taskId,
					strategy: "induction-repair",
					content: "Base case: 1 = 1^2. If the sum through n is n^2, adding 2n + 1 gives n^2 + 2n + 1 = (n + 1)^2, completing induction.",
				},
			};
		},
	};
	const verifier = {
		async verify(candidate: { readonly content: string }, _context: ProofVerifierContext) {
			return candidate.content === "wrong proof"
				? { verdict: "INCORRECT" as const, feedback: "The shortcut is circular." }
				: { verdict: "CORRECT" as const, feedback: "The induction proof checks out." };
		},
	};

	const runtime = new ProofRuntime({
		session,
		obligation: { obligationId: "odd-sum", theorem },
		planner,
		researcher,
		verifier,
		maxSteps: 3,
		maxWorkers: 2,
	});
	const result = await runtime.run();

	assert.equal(result.status, "PROVED");
	assert.equal(result.candidateId, "route-c-candidate");
	assert.equal(peakResearchers, 2);
	assert.deepEqual(researched.sort(), ["route-a", "route-b", "route-c"]);
	assert.equal(runtime.state.failedRoutes.length, 2);
	assert.ok(runtime.events.some((event) => event.type === "proof/route_failed"));
	assert.equal((await runtime.repository.readItem("lemmas/odd-sum-base"))?.content, "The case n = 1 is immediate.");
	assert.equal(await readFile(join(runtime.runDirectoryPath, "WHITEBOARD.md"), "utf8"), "Explore induction and preserve rejected routes.");
	assert.match(await readFile(result.proofPath ?? "", "utf8"), /completing induction/);

	const resumedSession = await Session.resume(session.filePath);
	const resumed = new ProofRuntime({
		session: resumedSession,
		obligation: { obligationId: "odd-sum", theorem },
		planner,
		researcher,
		verifier,
		maxSteps: 3,
	});
	assert.equal(resumed.state.status, "PROVED");
	const resumedResult = await resumed.run();
	assert.equal(resumedResult.status, "PROVED");
});
