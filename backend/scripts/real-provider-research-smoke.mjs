import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MathAgentConfigService, ProofApiServer, createConfiguredProofRoleFactory } from "../dist/src/index.js";

if (!process.env.OPENAI_API_KEY) {
	console.log(JSON.stringify({ status: "NOT_RUN", reason: "OPENAI_API_KEY is not configured" }));
	process.exit(0);
}

const directory = await mkdtemp(join(tmpdir(), "mrr-real-provider-smoke-"));
const data = join(directory, "data");
const corpus = join(directory, "corpus");
const baseUrl = process.env.OPENAI_BASE_URL;
const model = process.env.OPENAI_MODEL ?? "gpt-5-mini";
const maxTokens = positiveInteger(process.env.REAL_SMOKE_MAX_TOKENS, 16384);
const maxTurns = positiveInteger(process.env.REAL_SMOKE_MAX_TURNS, 8);
const maxSteps = positiveInteger(process.env.REAL_SMOKE_MAX_STEPS, 8);
const requestParameters = baseUrl?.includes("generativelanguage.googleapis.com")
	? { max_tokens: maxTokens }
	: { max_completion_tokens: maxTokens };
await mkdir(corpus);
await writeFile(join(corpus, "identity.md"), "Lemma Identity: for every integer n, n + 0 = n. This follows from the additive identity axiom.\n", "utf8");
const config = new MathAgentConfigService(join(directory, "math-agent.toml"));
await config.load();
await config.update({
	proof: { maxWorkers: 1, maxSteps },
	research: { maxCycles: 1, checkpointInterval: 1, stallThreshold: 2, structuralProbeBudget: 0, maxActiveObligations: 2 },
	budgets: { plannerCalls: 8, workerCalls: 4, verifierCalls: 4, secondaryAuditorCalls: 2, literatureSearches: 0, toolCalls: 16, wallTimeSeconds: 120 },
	models: { openaiSmoke: { provider: "openai", model, apiKeyEnv: "OPENAI_API_KEY", ...(baseUrl === undefined ? {} : { baseUrl }), reasoningEffort: "low", requestParameters } },
	roles: {
		research_director: { model: "openaiSmoke", maxTurns: 2, timeoutSeconds: 60 },
		planner: { model: "openaiSmoke", maxTurns: 2, timeoutSeconds: 60 },
		worker: { model: "openaiSmoke", maxTurns, timeoutSeconds: 60 },
		verifier: { model: "openaiSmoke", maxTurns, timeoutSeconds: 60 },
		secondary_auditor: { model: "openaiSmoke", maxTurns, timeoutSeconds: 60 },
		synthesizer: { model: "openaiSmoke", enabled: false },
	},
});

const factory = createConfiguredProofRoleFactory({ config, rootDirectory: data });
const server = new ProofApiServer({ rootDirectory: data, configService: config, createRoles: factory, defaultMaxWorkers: 1, defaultMaxSteps: maxSteps });
try {
	const base = await server.start();
	if (process.env.RUN_DIRECT_PROOF_SMOKE === "1") {
		await json(base, "/v1/sessions", "POST", { sessionId: "direct-proof" });
		await json(base, "/v1/sessions/direct-proof/theorem", "POST", {
			obligationId: "direct-proof-obligation",
			theorem: "For every integer n, n + 0 = n.",
			context: "Use the exact additive identity law; return a complete informal proof.",
		});
		await json(base, "/v1/sessions/direct-proof/proof-runs", "POST", { runId: "dynamic-smoke", workflowMode: "dynamic", maxWorkers: 1, maxSteps });
		let proofView;
		for (let index = 0; index < 1200; index += 1) {
			proofView = await json(base, "/v1/sessions/direct-proof/proof-runs/dynamic-smoke/result");
			if (proofView.ready) break;
			await new Promise((resolve) => setTimeout(resolve, 100));
		}
		if (!proofView?.ready) throw new Error("direct proof smoke timed out");
		console.log(JSON.stringify({
			status: "DIRECT_PROOF_RAN",
			proofStatus: proofView.result.status,
			workflowMode: proofView.result.workflowMode,
			proofTasks: proofView.state.tasks.map(({ taskId, status, dependsOn, agent }) => ({ taskId, status, dependsOn, agentId: agent?.agentId })),
			candidateCount: proofView.state.candidates.length,
			verifiedCount: Object.values(proofView.state.verifications).filter(({ verdict }) => verdict === "CORRECT").length,
			stepCount: proofView.state.step,
			lastError: proofView.state.lastError,
		}, null, 2));
	}
	await json(base, "/v1/research/projects", "POST", { projectId: "smoke", name: "bounded-real-provider-smoke" });
	await json(base, "/v1/research/projects/smoke/root", "POST", { objective: "For every integer n, n + 0 = n." });
	await json(base, "/v1/research/projects/smoke/corpus", "POST", { roots: [corpus] });
	await json(base, "/v1/research/projects/smoke/corpus/ingest", "POST", {});
	await json(base, "/v1/research/projects/smoke/start", "POST", { maxCycles: 1 });
	let view;
	for (let index = 0; index < 1200; index += 1) { view = await json(base, "/v1/research/projects/smoke"); if (!view.active) break; await new Promise((resolve) => setTimeout(resolve, 100)); }
	if (view?.active) throw new Error("bounded real-provider smoke timed out");
	const state = view.state;
	console.log(JSON.stringify({
		status: "RAN",
		projectStatus: state.status,
		cycle: state.cycle,
		decisions: state.decisions.map(({ action, direction, protocolError }) => ({ action, direction, protocolError })),
		budget: state.budget,
		frontier: state.obligations ? Object.values(state.obligations).map(({ obligationId, status }) => ({ obligationId, status })) : [],
		attempts: Object.values(state.attempts).map(({ attemptId, status, artifactRefs }) => ({ attemptId, status, artifactCount: artifactRefs.length })),
		executionTaskStatuses: Object.values(state.executionTasks).map(({ kind, logicalTaskId, status }) => ({ kind, logicalTaskId, status })),
		toolReceiptCount: Object.keys(state.toolEvidenceReceipts).length,
		workerReadCount: Object.values(state.toolEvidenceReceipts).filter((item) => item.role === "worker" && item.operation === "READ").length,
		verifierReadCount: Object.values(state.toolEvidenceReceipts).filter((item) => item.role === "verifier" && item.operation === "READ").length,
		lastError: state.lastError,
	}, null, 2));
} finally {
	await server.stop();
	if (process.env.KEEP_SMOKE_ARTIFACTS === "1") console.log(JSON.stringify({ artifacts: directory }));
	else await rm(directory, { recursive: true, force: true });
}

async function json(base, path, method = "GET", body) {
	const response = await fetch(`${base}${path}`, { method, headers: { "content-type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
	const value = await response.json();
	if (!response.ok) throw new Error(`${method} ${path} failed: ${JSON.stringify(value)}`);
	return value;
}

function positiveInteger(value, fallback) {
	const parsed = Number.parseInt(value ?? "", 10);
	return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}
