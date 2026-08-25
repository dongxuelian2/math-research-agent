import { strict as assert } from "node:assert";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
	AgentCore,
	ProofApiServer,
	MockProvider,
	Session,
	createAgentProofRoles,
	type MockResponse,
	type ProofApiRoleFactory,
} from "../src/index.js";

type ApiResponse = {
	readonly status: number;
	readonly body: any;
};

function textResponse(text: string): MockResponse {
	return {
		events: [
			{ type: "text_delta", text },
			{ type: "complete", stopReason: "end_turn" },
		],
	};
}

function model() {
	return { provider: "openai" as const, model: "http-api-smoke-model" };
}

async function request(baseUrl: string, path: string, init: RequestInit = {}): Promise<ApiResponse> {
	const response = await fetch(`${baseUrl}${path}`, init);
	const text = await response.text();
	return {
		status: response.status,
		body: text.length === 0 ? undefined : JSON.parse(text),
	};
}

async function waitForResult(baseUrl: string, path: string): Promise<any> {
	for (let attempt = 0; attempt < 200; attempt += 1) {
		const response = await request(baseUrl, path);
		if (response.body?.ready === true) return response;
		assert.equal(response.status, 202);
		await new Promise((resolve) => setTimeout(resolve, 5));
	}
	throw new Error("Timed out waiting for proof API result");
}

test("completes a proof through the HTTP session, theorem, run, and result API", async (t) => {
	const directory = await mkdtemp(join(tmpdir(), "math-agent-proof-api-"));
	t.after(async () => rm(directory, { recursive: true, force: true }));

	const createRoles: ProofApiRoleFactory = async ({ sessionId, runId }) => {
		const roleDirectory = join(directory, "roles", `${sessionId}-${runId}`);
		const plannerSession = await Session.create({
			projectId: "planner",
			cwd: directory,
			directory: join(roleDirectory, "planner"),
		});
		const researcherSession = await Session.create({
			projectId: "researcher",
			cwd: directory,
			directory: join(roleDirectory, "researcher"),
		});
		const verifierSession = await Session.create({
			projectId: "verifier",
			cwd: directory,
			directory: join(roleDirectory, "verifier"),
		});

		return createAgentProofRoles({
			planner: new AgentCore({
				session: plannerSession,
				model: model(),
				provider: new MockProvider([
					textResponse(
						'{"actions":[{"action":"write_whiteboard","content":"Use induction on n."},{"action":"spawn","tasks":[{"taskId":"odd-sum-api","summary":"Prove the odd-number sum identity","description":"Give a complete induction proof of the theorem."}]}]}',
					),
					textResponse('{"actions":[{"action":"submit_proof","candidateId":"odd-sum-api-candidate"}]}'),
				]),
			}),
			researcher: new AgentCore({
				session: researcherSession,
				model: model(),
				provider: new MockProvider([
					textResponse(
						'{"kind":"candidate","candidate":{"strategy":"induction","content":"For n = 1, the sum is 1 = 1^2. Assume 1 + 3 + ... + (2n - 1) = n^2. Adding the next odd number gives n^2 + (2n + 1) = (n + 1)^2, so the identity holds for n + 1. Therefore the formula holds for every n >= 1."}}',
					),
				]),
			}),
			verifier: new AgentCore({
				session: verifierSession,
				model: model(),
				provider: new MockProvider([
					textResponse("The base case and induction step are valid.\nVERDICT: CORRECT"),
				]),
			}),
		});
	};

	const api = new ProofApiServer({
		rootDirectory: directory,
		createRoles,
		defaultMaxWorkers: 1,
		defaultMaxSteps: 2,
	});
	const baseUrl = await api.start({ port: 0 });
	t.after(async () => api.stop());

	const health = await request(baseUrl, "/healthz");
	assert.equal(health.status, 200);
	assert.equal(health.body.ok, true);

	const created = await request(baseUrl, "/v1/sessions", {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({ sessionId: "http-proof-session" }),
	});
	assert.equal(created.status, 201);
	assert.equal(created.body.status, "OPEN");
	const sessionId = created.body.sessionId as string;

	const theorem = "For every integer n >= 1, 1 + 3 + ... + (2n - 1) = n^2.";
	const submitted = await request(baseUrl, `/v1/sessions/${sessionId}/theorem`, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({
			obligationId: "odd-sum-api",
			theorem,
			context: "Use ordinary mathematical induction.",
		}),
	});
	assert.equal(submitted.status, 200);
	assert.equal(submitted.body.status, "THEOREM_ACCEPTED");
	assert.equal(submitted.body.obligation.theorem, theorem);
	assert.match(await readFile(created.body.filePath, "utf8"), /theorem_submitted/);
	assert.match(await readFile(created.body.filePath, "utf8"), /odd-sum-api/);

	const sessionView = await request(baseUrl, `/v1/sessions/${sessionId}`);
	assert.equal(sessionView.status, 200);
	assert.equal(sessionView.body.obligation.theorem, theorem);
	assert.equal(sessionView.body.customEntryCount, 1);

	const started = await request(baseUrl, `/v1/sessions/${sessionId}/proof-runs`, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({ runId: "odd-sum-api-run", maxWorkers: 1, maxSteps: 2 }),
	});
	assert.equal(started.status, 202);
	assert.equal(started.body.status, "RUNNING");
	const runId = started.body.runId as string;

	const resultPath = `/v1/sessions/${sessionId}/proof-runs/${runId}/result`;
	const completed = await waitForResult(baseUrl, resultPath);
	assert.equal(completed.status, 200);
	assert.equal(completed.body.status, "PROVED");
	assert.equal(completed.body.result.status, "PROVED");
	assert.match(completed.body.answer.proof, /Adding the next odd number/);
	assert.match(completed.body.answer.proof, /verifier: CORRECT/);
	assert.equal(completed.body.state.status, "PROVED");

	const status = await request(baseUrl, `/v1/sessions/${sessionId}/proof-runs/${runId}`);
	assert.equal(status.status, 200);
	assert.equal(status.body.ready, true);
	assert.equal(status.body.status, "PROVED");
	assert.ok(status.body.result.proofPath);
	assert.match(await readFile(status.body.result.proofPath, "utf8"), /For every integer n/);

	const runs = await request(baseUrl, `/v1/sessions/${sessionId}/proof-runs`);
	assert.equal(runs.status, 200);
	assert.equal(runs.body.runs.length, 1);
	assert.equal(runs.body.runs[0].runId, runId);
	assert.equal(runs.body.runs[0].status, "PROVED");
});
