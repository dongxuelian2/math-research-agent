import { strict as assert } from "node:assert";
import { access, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	ProofWorkflow,
	Session,
	checkTheoremPreserved,
	checkUnsafeLeanSource,
	parsePlannerPlan,
	type ProofPlan,
	type ProofResearchContext,
	type ProofVerifierContext,
} from "../src/index.js";

test("formal gate rejects changed targets and Lean trust escape hatches", () => {
	const target = "theorem sample : True := by\n  sorry";
	assert.equal(checkTheoremPreserved(target, "theorem sample : True := by\n  trivial"), undefined);
	assert.match(checkTheoremPreserved(target, "theorem unrelated : True := by\n  trivial") ?? "", /preserve theorem declaration/iu);
	assert.match(checkUnsafeLeanSource("axiom unsound : False\ntheorem sample : True := by trivial") ?? "", /unverified assumptions/iu);
	assert.match(checkUnsafeLeanSource("theorem sample : True := by sorry") ?? "", /unresolved/iu);
});

test("parses OpenProver-style tagged TOML action blocks", () => {
	const plan = parsePlannerPlan(`
<OPENPROVER_ACTION>
action = "write_whiteboard"
summary = "Record the next route"
whiteboard = """
# Plan
- [ ] Try induction
"""
</OPENPROVER_ACTION>

<OPENPROVER_ACTION>
action = "spawn"

[[tasks]]
summary = "Check the base case"
description = """
Prove the base case for the theorem.
"""
routeKey = "base-case"
</OPENPROVER_ACTION>

<OPENPROVER_ACTION>
action = "submit_proof"
proof_slug = "candidates/final"
</OPENPROVER_ACTION>
`);

	assert.equal(plan.actions.length, 3);
	assert.equal(plan.actions[0]?.action, "write_whiteboard");
	assert.equal(plan.actions[1]?.action, "spawn");
	assert.equal(plan.actions[2]?.action, "submit_proof");
	if (plan.actions[1]?.action !== "spawn") throw new Error("expected spawn action");
	assert.equal(plan.actions[1].tasks[0]?.routeKey, "base-case");
	if (plan.actions[2]?.action !== "submit_proof") throw new Error("expected submit action");
	assert.equal(plan.actions[2].proofSlug, "candidates/final");
});

test("formal mode requires both accepted artifacts and persists OpenProver-shaped step evidence", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-formal-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));
	const session = await Session.create({ projectId: "formal", cwd: directory, directory });
	const planner = {
		async plan(context: { readonly step: number }): Promise<ProofPlan> {
			if (context.step === 1) {
				return {
					actions: [
						{
							action: "spawn",
							tasks: [{ taskId: "formal-worker", summary: "Produce the informal proof", description: "Give a complete proof of the theorem." }],
						},
						{
							action: "write_items",
							items: [
								{ slug: "informal/final", content: "The proposition is true by the constructor of True." },
								{ slug: "formal/final", format: "lean", content: "theorem sample : True := by\n  trivial" },
							],
						},
					],
				};
			}
			return {
				actions: [
					{ action: "submit_proof", proofSlug: "informal/final" },
					{ action: "submit_lean_proof", proofSlug: "formal/final" },
				],
			};
		},
	};
	const researcher = {
		async research(context: ProofResearchContext) {
			return {
				kind: "candidate" as const,
				candidate: {
					taskId: context.task.taskId,
					strategy: "direct",
					content: "The proposition is true by the constructor of True.",
				},
			};
		},
	};
	const verifier = {
		async verify(_candidate: unknown, _context: ProofVerifierContext) {
			return { verdict: "CORRECT" as const, feedback: "The informal argument is complete." };
		},
	};
	let formalCalls = 0;
	const formalVerifier = {
		async verify(content: string) {
			formalCalls += 1;
			return { ok: content.includes("theorem sample"), feedback: "Formal gate passed." };
		},
	};

	const runtime = new ProofWorkflow({
		session,
		obligation: { obligationId: "sample", theorem: "Prove True." },
		leanTheorem: "theorem sample : True := by\n  sorry",
		mode: "prove_and_formalize",
		planner,
		researcher,
		verifier,
		formalVerifier,
		maxSteps: 2,
	});
	const result = await runtime.run();

	assert.equal(result.status, "PROVED");
	assert.equal(result.mode, "prove_and_formalize");
	assert.equal(formalCalls, 1);
	assert.ok(result.proofPath);
	assert.ok(result.proofLeanPath);
	assert.match(await readFile(result.proofPath ?? "", "utf8"), /proposition is true/);
	assert.match(await readFile(result.proofLeanPath ?? "", "utf8"), /theorem sample/);
	await access(join(runtime.runDirectoryPath, "THEOREM.md"));
	await access(join(runtime.runDirectoryPath, "WHITEBOARD.md"));
	await access(join(runtime.runDirectoryPath, "run_config.json"));
	await access(join(runtime.runDirectoryPath, "steps", "step_001", "planner_plan.json"));
	await access(join(runtime.runDirectoryPath, "steps", "step_001", "worker_formal-worker_result.json"));
	await access(join(runtime.runDirectoryPath, "steps", "step_001", "verifier_formal-worker-candidate.json"));
	await access(join(runtime.runDirectoryPath, "steps", "step_002", "lean", "proof_result.json"));
	assert.equal(runtime.state.stepHistory.length, 2);
});
