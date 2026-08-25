import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	AgentCore,
	MockProvider,
	ProofRuntime,
	Session,
	createAgentProofRoles,
	type MockResponse,
} from "../src/index.js";

function textResponse(text: string): MockResponse {
	return {
		events: [
			{ type: "text_delta", text },
			{ type: "complete", stopReason: "end_turn" },
		],
	};
}

function model() {
	return { provider: "openai" as const, model: "mock-proof-model" };
}

test("real AgentCore API path produces and submits a simple proof", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-smoke-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));

	const proofSession = await Session.create({ projectId: "proof", cwd: directory, directory });
	const plannerSession = await Session.create({ projectId: "planner", cwd: directory, directory });
	const researcherSession = await Session.create({ projectId: "researcher", cwd: directory, directory });
	const verifierSession = await Session.create({ projectId: "verifier", cwd: directory, directory });

	const plannerProvider = new MockProvider([
		textResponse(
			'{"actions":[{"action":"write_whiteboard","content":"Use a direct induction proof."},{"action":"write_items","items":[{"slug":"notes/odd-sum","summary":"Induction plan","content":"Try the base case and then add 2n + 1."}]},{"action":"spawn","tasks":[{"taskId":"odd-sum-induction","summary":"Induction proof","description":"Prove the odd-number sum identity by induction using [[notes/odd-sum]]."}]}]}',
		),
		textResponse('{"actions":[{"action":"submit_proof","candidateId":"odd-sum-induction-candidate"}]}'),
	]);
	const researcherProvider = new MockProvider([
		textResponse(
			'{"kind":"candidate","candidate":{"strategy":"induction","content":"For n = 1, the sum is 1 = 1^2. Assume 1 + 3 + ... + (2n - 1) = n^2. Adding the next odd number gives n^2 + (2n + 1) = (n + 1)^2, so the identity holds for n + 1. Therefore the formula holds for every n >= 1."}}',
		),
	]);
	const verifierProvider = new MockProvider([
		textResponse("The base case and induction step are valid.\nVERDICT: CORRECT"),
	]);

	const roles = createAgentProofRoles({
		planner: new AgentCore({ session: plannerSession, model: model(), provider: plannerProvider }),
		researcher: new AgentCore({ session: researcherSession, model: model(), provider: researcherProvider }),
		verifier: new AgentCore({ session: verifierSession, model: model(), provider: verifierProvider }),
	});
	const runtime = new ProofRuntime({
		session: proofSession,
		obligation: {
			obligationId: "odd-sum",
			theorem: "For every integer n >= 1, 1 + 3 + ... + (2n - 1) = n^2.",
		},
		...roles,
		maxSteps: 2,
	});

	const result = await runtime.run();

	assert.equal(result.status, "PROVED");
	assert.equal(result.candidateId, "odd-sum-induction-candidate");
	assert.ok(result.proofPath);
	const proof = await readFile(result.proofPath ?? "", "utf8");
	assert.match(proof, /Adding the next odd number/);
	assert.match(proof, /verifier: CORRECT/);
	assert.equal((await proofSession.customEntries("proof")).some((entry) => entry.type === "proof/whiteboard_updated"), true);
	assert.equal((await runtime.repository.readItem("notes/odd-sum"))?.content, "Try the base case and then add 2n + 1.");
	assert.equal(plannerProvider.requests.length, 2);
	assert.equal(researcherProvider.requests.length, 1);
	assert.equal(verifierProvider.requests.length, 1);
	assert.ok(proofSession.customEntries("proof").some((entry) => entry.type === "proof/submitted"));
	assert.equal(runtime.state.status, "PROVED");
});
