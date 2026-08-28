import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import { access, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createUserMessage } from "../models/index.js";
import { ConfigConflictError, type ConfigUpdate, type MathAgentConfig, type MathAgentConfigService, type ProofRole } from "../config.js";
import type { Agent } from "../agent/types.js";
import type { JsonObject } from "../models/json.js";
import type { RuntimeTool } from "../models/tools.js";
import { Session } from "../session/index.js";
import { ProofWorkflow } from "../proof/runtime.js";
import { CommandProofFormalVerifier } from "../proof/formal.js";
import { activeAuthorityForClaim, AgentCorpusBootstrapper, AgentFinalAuditRole, AgentFormalizerRole, AgentLiteratureApplicability, AgentResearchDirector, AgentSynthesisRole, CorpusService, LiteratureService, OpenAlexLiteratureProvider, ResearchEvidenceRecorder, ResearchInvariantValidator, ResearchRetrievalService, ResearchRuntime, ResearchStore, RootClosureService, createResearchTools, researchFrontier, rootSynthesisReadiness, stableId, type LiteratureProvider, type TacticalProofRequest, type TacticalResearchResult, type TrustReceipt, type VerifiedResearchContribution } from "../research/index.js";
import { createAgentProofVerifier, type AgentProofRoles } from "../proof/agent-role.js";
import type {
	ProofMode,
	ProofObligation,
	ProofRunResult,
	ProofState,
	ProofStatus,
	ProofWorkflowMode,
} from "../proof/types.js";

export interface ProofApiRoleFactory {
(context: {
	readonly session: Session;
	readonly sessionId: string;
	readonly runId: string;
	readonly obligation: ProofObligation;
	readonly mode: ProofMode;
	readonly tools?: readonly RuntimeTool[];
	readonly targetGate?: { readonly targetObligationId: string; readonly targetClaimId: string };
	/** Immutable configuration selected when this run was created. */
	readonly config?: MathAgentConfig;
}): AgentProofRoles | Promise<AgentProofRoles>;
	createAgent?(context: { readonly role: ProofRole; readonly sessionId: string; readonly runId: string; readonly tools?: readonly RuntimeTool[]; readonly config?: MathAgentConfig }): Promise<Agent>;
}

export type ProofApiServerOptions = {
	readonly rootDirectory: string;
	readonly createRoles: ProofApiRoleFactory;
	readonly configService?: MathAgentConfigService;
	readonly defaultMode?: ProofMode;
	readonly defaultMaxWorkers?: number;
	readonly defaultMaxSteps?: number;
	readonly literatureProvider?: LiteratureProvider;
	/** Deterministic crash seam used by restart/fault E2E; never configured by the public API. */
	readonly researchProofFaultAfterWorkerResults?: number;
};

type SessionRecord = {
	readonly session: Session;
	pendingObligation?: ProofObligation;
	pendingLeanTheorem?: string;
	mode?: ProofMode;
	readonly runs: Map<string, RunRecord>;
};

type RunRecord = {
	readonly runId: string;
	readonly workflow: ProofWorkflow;
	controller: AbortController;
	promise?: Promise<ProofRunResult>;
	readonly startedAt: number;
	result?: ProofRunResult;
	finishedAt?: number;
};

const TERMINAL_STATUSES: readonly ProofStatus[] = [
	"PROVED", "PARTIAL", "FAILED", "BLOCKED_FORMAL", "BLOCKED_PROVIDER", "CANCELLED",
];

export class ProofApiServer {
	private readonly rootDirectory: string;
	private readonly createRoles: ProofApiRoleFactory;
	private readonly configService?: MathAgentConfigService;
	private readonly defaultMode: ProofMode;
	private readonly defaultMaxWorkers: number;
	private readonly defaultMaxSteps: number;
	private readonly researchProofFaultAfterWorkerResults?: number;
	private readonly sessions = new Map<string, SessionRecord>();
	private readonly researchStore: ResearchStore;
	private readonly researchCorpus: CorpusService;
	private researchRuntime: ResearchRuntime;
	private readonly literatureProvider: LiteratureProvider;
	private readonly activeResearch = new Map<string, { readonly controller: AbortController; readonly promise: Promise<void> }>();
	private readonly sseResponses = new Set<ServerResponse>();
	private readonly sseRunResponses = new Map<RunRecord, Set<ServerResponse>>();
	private server: Server | undefined;
	private baseUrlValue: string | undefined;

	constructor(options: ProofApiServerOptions) {
		this.rootDirectory = resolve(options.rootDirectory);
		this.createRoles = options.createRoles;
		this.configService = options.configService;
		this.defaultMode = options.defaultMode ?? "prove";
		this.defaultMaxWorkers = Math.max(1, options.defaultMaxWorkers ?? 3);
		this.defaultMaxSteps = Math.max(1, options.defaultMaxSteps ?? 32);
		this.researchProofFaultAfterWorkerResults = options.researchProofFaultAfterWorkerResults;
		this.researchStore = new ResearchStore(join(this.rootDirectory, "research"));
		this.researchCorpus = new CorpusService(this.researchStore);
		this.literatureProvider = options.literatureProvider ?? new OpenAlexLiteratureProvider();
		this.researchRuntime = this.buildResearchRuntime();
	}

	private buildResearchRuntime(): ResearchRuntime {
		return new ResearchRuntime({
			store: this.researchStore,
			proofRunner: (request, signal) => this.runResearchProof(request, signal),
			literatureRunner: (request, signal) => this.runLiteratureProof(request, signal),
			synthesisRunner: (projectId, signal) => this.runRootSynthesis(projectId, signal),
			...(this.createRoles.createAgent === undefined ? {} : { modelDirector: new AgentResearchDirector((snapshot) => this.createResearchAgent("research_director", snapshot.projectId, `cycle-${Date.now()}`)) }),
			secondaryAuditor: async ({ projectId, claim, candidate, workerReadEvidence }, signal) => {
				const auditId = stableResearchAuditId(projectId, candidate.contentHash), directory = join(this.researchStore.projectDirectory(projectId), "audits", auditId); await mkdir(directory, { recursive: true });
				const config = this.projectConfigSnapshot(await this.researchStore.read(projectId)), recorder = new ResearchEvidenceRecorder(this.researchStore, projectId, auditId), tools = this.researchTools(projectId, directory, recorder, "secondary_auditor", config);
				const agent = await this.createResearchAgent("secondary_auditor", projectId, auditId, tools), verifier = createAgentProofVerifier(agent), obligation: ProofObligation = { obligationId: auditId, theorem: claim.statement }, body = (await this.researchStore.resolveArtifact(projectId, candidate)).body;
				const task = { taskId: auditId, summary: "Fresh independent root audit", description: `Audit the exact target proof. Retrieve every relied-on artifact before accepting.`, routeFingerprint: auditId, scope: "TARGET" as const, targetClaimId: claim.claimId, dependsOn: [], status: "COMPLETED" as const, attempt: 1, updatedAt: new Date().toISOString(), kind: "MATHEMATICAL" as const }, proofCandidate = { candidateId: candidate.artifactId, taskId: auditId, content: body, strategy: "fresh-root-audit", routeFingerprint: auditId, claimFingerprint: candidate.contentHash, candidateFingerprint: candidate.contentHash, claim: claim.statement, evidence: workerReadEvidence, discoveredEvidence: [], bodyReadEvidence: workerReadEvidence, declaredEvidence: workerReadEvidence, reliedOnArtifactIds: workerReadEvidence.map((item) => item.artifactId), assumptions: claim.assumptions, dependencyClaims: claim.dependencies, scope: "TARGET" as const, targetClaimId: claim.claimId };
				const result = await verifier.verify(proofCandidate, { runId: auditId, step: 1, obligation, task }, signal); const inspected = (await recorder.list("secondary_auditor")).filter((item) => item.operation === "READ").map((item) => item.artifact);
				await this.researchStore.transaction(projectId, (draft) => { const mutable = draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }; mutable.budget = { ...draft.budget, secondaryAuditorCalls: draft.budget.secondaryAuditorCalls + 1 }; });
				return { verdict: result.verdict === "NEEDS_MINOR_FIXES" ? "MINOR_FIX" : result.verdict, feedback: result.feedback, profile: "configured-secondary-auditor", evidenceInspected: inspected, toolReceiptIds: (await recorder.list("secondary_auditor")).map((item) => item.receiptId) };
			},
			// These are compatibility defaults only. Every v1.1 project with a valid
			// snapshot overrides them from its own durable effectiveConfig.
			checkpointInterval: 1,
			stallThreshold: 3,
			maxCycles: 100,
			maxActiveObligations: 8,
			structuralProbeBudget: 2,
		});
	}

	get baseUrl(): string | undefined {
		return this.baseUrlValue;
	}

	/** Start the standalone HTTP adapter used by tests and the local launcher. */
	async start(options: { readonly host?: string; readonly port?: number } = {}): Promise<string> {
		if (this.server !== undefined && this.baseUrlValue !== undefined) return this.baseUrlValue;
		await mkdir(join(this.rootDirectory, "sessions"), { recursive: true });
		await mkdir(join(this.rootDirectory, "proof-runs"), { recursive: true });
		await this.researchStore.initialize();
		if (this.configService !== undefined) {
			await this.configService.load();
			await this.configService.startWatching();
			this.researchRuntime = this.buildResearchRuntime();
		}
		await this.discoverPersistedSessions();
		const host = options.host ?? "127.0.0.1";
		const port = options.port ?? 0;
		this.server = createServer((request, response) => {
			void this.handleRequest(request, response);
		});
		await new Promise<void>((resolvePromise, reject) => {
			const server = this.server;
			if (server === undefined) {
				reject(new Error("HTTP server was not initialized"));
				return;
			}
			server.once("error", reject);
			server.listen(port, host, () => {
				server.off("error", reject);
				resolvePromise();
			});
		});
		const address = this.server.address();
		if (address === null || typeof address === "string") throw new Error("HTTP server did not expose an address");
		this.baseUrlValue = `http://${host}:${address.port}`;
		return this.baseUrlValue;
	}

	async stop(): Promise<void> {
		for (const run of this.allRuns()) run.controller.abort();
		for (const run of this.activeResearch.values()) run.controller.abort();
		await Promise.allSettled(this.allRuns().map((run) => run.promise ?? Promise.resolve()));
		await Promise.allSettled([...this.activeResearch.values()].map((run) => run.promise));
		for (const response of this.sseResponses) response.end();
		this.sseResponses.clear();
		this.sseRunResponses.clear();
		await this.configService?.close();
		const server = this.server;
		this.server = undefined;
		this.baseUrlValue = undefined;
		if (server === undefined) return;
		await new Promise<void>((resolvePromise, reject) => {
			server.close((error) => error === undefined ? resolvePromise() : reject(error));
		});
	}

	/** Public route handler so the Harness Host adapter can mount the same API. */
	async handleRequest(request: IncomingMessage, response: ServerResponse): Promise<void> {
		try {
			if (request.method === "OPTIONS") {
				this.send(response, 204, undefined);
				return;
			}
			const url = new URL(request.url ?? "/", "http://127.0.0.1");
			const parts = url.pathname.split("/").filter((part) => part.length > 0).map((part) => decodeURIComponent(part));
			if (request.method === "GET" && url.pathname === "/healthz") {
				this.send(response, 200, { ok: true, service: "math-agent-proof-api" });
				return;
			}
			if (parts[0] === "v1" && parts[1] === "config") {
				await this.handleConfigRoute(parts, request, response);
				return;
			}
			if (parts[0] === "v1" && parts[1] === "research") {
				await this.handleResearchRoute(parts, url, request, response);
				return;
			}
			if (parts.length === 2 && parts[0] === "v1" && parts[1] === "sessions" && request.method === "POST") {
				await this.createSession(request, response);
				return;
			}
			if (parts.length === 2 && parts[0] === "v1" && parts[1] === "sessions" && request.method === "GET") {
				this.send(response, 200, { sessions: [...this.sessions.entries()].map(([id, record]) => this.sessionSummary(id, record)) });
				return;
			}
			if (parts.length < 3 || parts[0] !== "v1" || parts[1] !== "sessions") {
				throw new ApiHttpError(404, "API route not found");
			}
			const sessionId = parts[2] ?? "";
			const record = this.sessions.get(sessionId);
			if (record === undefined) throw new ApiHttpError(404, `Session not found: ${sessionId}`);

			if (parts.length === 3 && request.method === "GET") {
				this.send(response, 200, this.sessionView(sessionId, record));
				return;
			}
			if (parts.length === 4 && parts[3] === "theorem" && request.method === "POST") {
				await this.submitTheorem(sessionId, record, request, response);
				return;
			}
			if (parts.length === 4 && parts[3] === "proof-runs" && request.method === "POST") {
				await this.startProofRun(sessionId, record, request, response);
				return;
			}
			if (parts.length === 4 && parts[3] === "proof-runs" && request.method === "GET") {
				this.send(response, 200, {
					sessionId,
					runs: [...record.runs.values()].map((run) => this.runView(sessionId, run)),
				});
				return;
			}
			if (parts.length >= 5 && parts[3] === "proof-runs") {
				const runId = parts[4] ?? "";
				const run = record.runs.get(runId);
				if (run === undefined) throw new ApiHttpError(404, `Proof run not found: ${runId}`);
				if (parts.length === 6 && parts[5] === "result" && request.method === "GET") {
					await this.getRunResult(sessionId, run, response);
					return;
				}
				if (parts.length === 6 && parts[5] === "events" && request.method === "GET") {
					await this.streamRunEvents(run, request, response);
					return;
				}
				if (parts.length === 6 && parts[5] === "cancel" && request.method === "POST") {
					this.cancelRun(sessionId, run, response);
					return;
				}
				if (parts.length === 5 && request.method === "GET") {
					this.send(response, 200, this.runView(sessionId, run));
					return;
				}
			}
			throw new ApiHttpError(404, "API route not found");
		} catch (error) {
			this.sendError(response, error);
		}
	}

	private async handleResearchRoute(parts: readonly string[], url: URL, request: IncomingMessage, response: ServerResponse): Promise<void> {
		if (parts.length === 3 && parts[2] === "projects" && request.method === "GET") {
			const projects = await this.researchStore.listProjects();
			this.send(response, 200, { projects: projects.map((state) => ({ projectId: state.projectId, name: state.name, status: state.status, rootObjective: state.rootObjective, cycle: state.cycle, frontierSize: researchFrontier(state).length })) }); return;
		}
		if (parts.length === 3 && parts[2] === "projects" && request.method === "POST") {
			const body = await readJsonObject(request); const projectId = optionalString(body.projectId) ?? randomUUID(); const name = optionalString(body.name) ?? projectId;
			let state = await this.researchRuntime.createProject(projectId, name, this.configService === undefined ? {} : jsonObjectOf(this.configService.config)); const projectConfig = mathAgentConfigOf(state.effectiveConfig), configuredRoots = projectConfig?.corpus.enabled === true ? projectConfig.corpus.roots : [];
			if (configuredRoots.length > 0) state = await this.researchCorpus.attach(projectId, configuredRoots);
			state = (await this.researchStore.transaction(projectId, (draft) => { const mutable = draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }; mutable.events = [...draft.events, { eventId: `project-${projectId}`, type: "research/project_created", projectId, timestamp: new Date().toISOString(), detail: { name, configRevision: draft.configRevision } }]; })).state;
			this.send(response, 201, { state, links: researchLinks(projectId) }); return;
		}
		if (parts.length < 4 || parts[2] !== "projects") throw new ApiHttpError(404, "Research API route not found");
		const projectId = parts[3] ?? ""; let state;
		try { state = await this.researchStore.read(projectId); } catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") throw new ApiHttpError(404, `Research project not found: ${projectId}`); throw error; }
		if (parts.length === 4 && request.method === "GET") { this.send(response, 200, { state, active: this.activeResearch.has(projectId), links: researchLinks(projectId) }); return; }
		const operation = parts[4] ?? "", projectConfig = mathAgentConfigOf(state.effectiveConfig);
		if (operation === "root" && request.method === "POST") { const body = await readJsonObject(request); const objective = requiredString(body.objective ?? body.statement, "objective"); if (body.allowedAssumptions !== undefined && (!Array.isArray(body.allowedAssumptions) || !body.allowedAssumptions.every((item) => typeof item === "string"))) throw new ApiHttpError(400, "allowedAssumptions must be an array of exact assumption statements"); this.send(response, 200, { state: await this.researchRuntime.setRootObjective(projectId, objective, (body.allowedAssumptions as string[] | undefined) ?? []) }); return; }
		if (operation === "corpus" && parts.length === 5 && request.method === "POST") { if (projectConfig?.corpus.enabled === false) throw new ApiHttpError(409, "Corpus access is disabled by the project configuration snapshot"); const body = await readJsonObject(request); if (!Array.isArray(body.roots) || !body.roots.every((item) => typeof item === "string")) throw new ApiHttpError(400, "roots must be an array of directory paths"); this.send(response, 200, { state: await this.researchCorpus.attach(projectId, body.roots) }); return; }
		if (operation === "corpus" && (parts[5] === "ingest" || parts[5] === "reindex") && request.method === "POST") { if (projectConfig?.corpus.enabled === false) throw new ApiHttpError(409, "Corpus ingestion is disabled by the project configuration snapshot"); const result = await this.researchCorpus.ingest(projectId, configuredImportAuthority(projectConfig?.corpus.importAuthorityPolicy)); this.send(response, 200, result); return; }
		if (operation === "corpus" && parts[5] === "search" && request.method === "GET") { const query = url.searchParams.get("q") ?? ""; this.send(response, 200, { matches: await this.researchCorpus.search(projectId, query, url.searchParams.get("exact") === "true") }); return; }
		if (operation === "corpus" && parts[5] !== undefined && request.method === "GET") { this.send(response, 200, await this.researchCorpus.read(projectId, parts[5], Number(url.searchParams.get("offset") ?? 0), url.searchParams.has("limit") ? Number(url.searchParams.get("limit")) : undefined)); return; }
		if (operation === "corpus" && request.method === "GET") { this.send(response, 200, { corpus: await this.researchCorpus.list(projectId) }); return; }
		if (operation === "bootstrap" && request.method === "POST") { if (projectConfig?.corpus.enabled === false) throw new ApiHttpError(409, "Corpus bootstrap is disabled by the project configuration snapshot"); const body = await readJsonObject(request), rangeIds = Array.isArray(body.rangeIds) && body.rangeIds.every((item) => typeof item === "string") ? body.rangeIds as string[] : undefined, maxRanges = typeof body.maxRanges === "number" && Number.isInteger(body.maxRanges) && body.maxRanges > 0 ? body.maxRanges : undefined; const bootstrapper = this.createRoles.createAgent === undefined || projectConfig?.roles.corpus_bootstrapper.enabled === false ? undefined : new AgentCorpusBootstrapper((artifactId) => this.createResearchAgent("corpus_bootstrapper", projectId, artifactId)); this.send(response, 200, { report: await this.researchCorpus.bootstrap(projectId, bootstrapper, undefined, { ...(rangeIds === undefined ? {} : { rangeIds }), ...(maxRanges === undefined ? {} : { maxRanges }) }), state: await this.researchStore.read(projectId) }); return; }
		if ((operation === "start" || operation === "resume") && request.method === "POST") {
			if (this.activeResearch.has(projectId)) throw new ApiHttpError(409, "Research project is already running"); const body = await readJsonObject(request); const maxCycles = positiveInteger(body.maxCycles) ?? projectConfig?.research.maxCycles ?? 100; const controller = new AbortController();
			const promise = this.researchRuntime.run(projectId, maxCycles, controller.signal).then(() => undefined).catch(async (error: unknown) => { await this.researchStore.transaction(projectId, (draft) => { const mutable = draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }; mutable.status = "BLOCKED"; mutable.lastError = errorMessage(error); }); }).finally(() => { this.activeResearch.delete(projectId); });
			this.activeResearch.set(projectId, { controller, promise }); this.send(response, 202, { projectId, status: operation === "resume" ? "RESUMING" : "RUNNING", links: researchLinks(projectId) }); return;
		}
		if (operation === "cancel" && request.method === "POST") { const active = this.activeResearch.get(projectId); if (active === undefined) throw new ApiHttpError(409, "Research project is not active"); active.controller.abort(); this.send(response, 202, { projectId, status: "CANCELLATION_REQUESTED" }); return; }
		if (operation === "checkpoint" && request.method === "POST") { this.send(response, 201, { state: await this.researchRuntime.createCheckpoint(projectId) }); return; }
		if (operation === "root-readiness" && request.method === "GET") { this.send(response, 200, await this.rootClosureService(projectId).then((service) => service.readiness(projectId))); return; }
		if (operation === "synthesis" && request.method === "POST") { this.send(response, 200, { state: await this.runRootSynthesis(projectId) }); return; }
		if (operation === "bootstrap-report" && request.method === "GET") { this.send(response, 200, { reports: state.bootstrapReports, latest: state.bootstrapReports.at(-1) }); return; }
		if (operation === "audit" && request.method === "GET") { const invariants = await new ResearchInvariantValidator(this.researchStore).check(projectId); this.send(response, 200, { projectId, schemaVersion: state.schemaVersion, rootObjectiveContract: state.rootObjectiveContract, effectiveConfig: state.effectiveConfig, configRevision: state.configRevision, migrationReports: state.migrationReports, executionPlans: state.executionPlans, executionTasks: state.executionTasks, evidenceReceipts: state.toolEvidenceReceipts, trustReceipts: state.trustReceipts, authorityReceipts: state.authorityReceipts, authorityValidation: state.authorityValidation, acceptedEffects: state.acceptedEffects, finalProofHistory: state.finalProofHistory, currentFinalProofAuthority: state.currentFinalProofAuthority, bootstrapRuns: state.bootstrapRuns, bootstrapReports: state.bootstrapReports, invariants, rootReadiness: rootSynthesisReadiness(state) }); return; }
		if (operation === "invalidate" && request.method === "POST") { const body = await readJsonObject(request); this.send(response, 200, { state: await this.researchRuntime.reducer.invalidate(projectId, requiredString(body.claimId, "claimId"), requiredString(body.reason, "reason")) }); return; }
		if (operation === "frontier" && request.method === "GET") { this.send(response, 200, { frontier: researchFrontier(state) }); return; }
		if (operation === "claims" && parts[5] !== undefined && request.method === "GET") { const revisions = state.claims[parts[5]]; if (revisions === undefined) throw new ApiHttpError(404, "Claim not found"); this.send(response, 200, { claimId: parts[5], revisions, latest: revisions.at(-1), outgoingSupport: state.supportEdges.filter((edge) => edge.fromClaimId === parts[5]), incomingSupport: state.supportEdges.filter((edge) => edge.toClaimId === parts[5]) }); return; }
		if (operation === "claims" && request.method === "GET") { this.send(response, 200, { claims: state.claims, supportEdges: state.supportEdges }); return; }
		if (operation === "dependencies" && request.method === "GET") { this.send(response, 200, { dependencies: Object.fromEntries(Object.entries(state.claims).map(([id, revisions]) => [id, revisions.at(-1)?.dependencies ?? []])) }); return; }
		if (operation === "coverage" && request.method === "GET") { this.send(response, 200, { coverage: state.coverage }); return; }
		if (operation === "routes" && parts[5] !== undefined && parts[6] === "reopen" && request.method === "POST") { const body = await readJsonObject(request); this.send(response, 200, { state: await this.researchRuntime.reopenRouteAsOperator(projectId, parts[5], requiredString(body.reason, "reason")) }); return; }
		if (operation === "routes" && parts[5] !== undefined && request.method === "GET") { const route = state.routes[parts[5]]; if (route === undefined) throw new ApiHttpError(404, "Route not found"); this.send(response, 200, { route }); return; }
		if (operation === "routes" && request.method === "GET") { this.send(response, 200, { routes: state.routes }); return; }
		if (operation === "checkpoints" && request.method === "GET") { this.send(response, 200, { checkpoints: state.checkpoints }); return; }
		if (operation === "events" && request.method === "GET") { this.send(response, 200, { events: state.events }); return; }
		if (operation === "artifacts" && parts[5] !== undefined && parts[6] === "metadata" && request.method === "GET") { const artifact = state.artifacts[parts[5]]; if (artifact === undefined) throw new ApiHttpError(404, "Artifact not found"); this.send(response, 200, { artifact }); return; }
		if (operation === "artifacts" && parts[5] !== undefined && request.method === "GET") { const artifact = state.artifacts[parts[5]]; if (artifact === undefined) throw new ApiHttpError(404, "Artifact not found"); this.send(response, 200, await this.researchStore.resolveArtifact(projectId, artifact)); return; }
		if (operation === "artifacts" && request.method === "GET") { this.send(response, 200, { artifacts: state.artifacts }); return; }
		if (operation === "formalization" && request.method === "GET") { this.send(response, 200, { status: state.formalizationStatus ?? "NOT_REQUESTED", enabled: projectConfig?.formalization.enabled ?? false, artifacts: Object.values(state.artifacts).filter((item) => item.artifactType === "LEAN_SOURCE" || item.artifactType === "LEAN_CERTIFICATE" || item.artifactType === "FORMAL_PROOF") }); return; }
		if (operation === "formalization" && request.method === "POST") { const body = await readJsonObject(request); const mode = optionalString(body.mode) ?? "formalize_existing"; if (mode === "informal_only") { this.send(response, 200, { state, status: state.formalizationStatus ?? "NOT_REQUESTED" }); return; } if (mode !== "formalize_existing" && mode !== "prove_and_formalize") throw new ApiHttpError(400, "formalization mode must be informal_only, formalize_existing, or prove_and_formalize"); this.send(response, 200, await this.runResearchFormalization(projectId, mode, optionalString(body.existingLean))); return; }
		if (operation === "literature" && parts[5] === "search" && request.method === "POST") { const body = await readJsonObject(request), query = requiredString(body.query, "query"), targetObligationId = requiredString(body.targetObligationId, "targetObligationId"), obligation = state.obligations[targetObligationId]; if (obligation === undefined) throw new ApiHttpError(404, "Target obligation not found"); this.send(response, 200, { results: await this.discoverLiterature(projectId, query, obligation) }); return; }
		if (operation === "literature" && request.method === "GET") { this.send(response, 200, { artifacts: Object.values(state.artifacts).filter((item) => item.artifactType === "LITERATURE_SOURCE") }); return; }
		if (operation === "result" && request.method === "GET") { const root = state.rootClaimId === undefined ? undefined : state.claims[state.rootClaimId]?.at(-1), currentFinal = state.currentFinalProofAuthority?.status === "ACTIVE" ? state.currentFinalProofAuthority.artifact : undefined; this.send(response, 200, { projectId, status: state.status, root, finalProofArtifact: currentFinal, currentFinalProofAuthority: state.currentFinalProofAuthority, historicalFinalProof: state.finalProofArtifact === undefined ? undefined : { artifact: state.finalProofArtifact, status: state.currentFinalProofAuthority?.artifact.artifactId === state.finalProofArtifact.artifactId ? state.currentFinalProofAuthority.status : "HISTORICAL" }, frontier: researchFrontier(state), latestCheckpoint: state.checkpoints.at(-1), lastError: state.lastError }); return; }
		throw new ApiHttpError(404, "Research API route not found");
	}

	private async runResearchProof(request: TacticalProofRequest, signal?: AbortSignal): Promise<TacticalResearchResult> {
		const sessionDirectory = join(request.scratchDirectory, "session"); await mkdir(sessionDirectory, { recursive: true }); const sessionPath = join(sessionDirectory, `${request.attemptId}.jsonl`);
		let session: Session; try { await access(sessionPath); session = await Session.resume(sessionPath); } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; session = await Session.create({ projectId: request.attemptId, sessionId: request.attemptId, cwd: request.scratchDirectory, directory: sessionDirectory }); }
		const proofObligation: ProofObligation = { obligationId: request.obligation.obligationId, theorem: request.obligation.statement, context: [`Research project ${request.projectId}.`, `Exact target obligation id: ${request.obligation.obligationId}.`, `Exact target claim id: ${request.targetClaimId}.`, `Context manifest ${request.contextManifest.manifestId} lists available evidence only; search is not read and read is not reliance. Use artifact_read and declare reliedOnArtifactIds.`, `Tactical directive (authoritative strategy intent): ${JSON.stringify(request.directive)}`, `Failed/exhausted route mechanisms: ${JSON.stringify(request.directive.relevantFailedRoutes)}`].join("\n") };
		const evidenceRecorder = new ResearchEvidenceRecorder(this.researchStore, request.projectId, request.attemptId);
		const projectState = await this.researchStore.read(request.projectId), config = this.projectConfigSnapshot(projectState), projectBudget = projectState.budget, tools = this.researchTools(request.projectId, request.scratchDirectory, evidenceRecorder, undefined, config);
		const targetGate = { targetObligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId };
		const roles = await this.createRoles({ session, sessionId: request.attemptId, runId: request.logicalJobId, obligation: proofObligation, mode: "prove", tools, targetGate, ...(config === undefined ? {} : { config }) });
		const workflow = new ProofWorkflow({ session, obligation: proofObligation, ...roles, mode: "prove", workflowMode: config?.proof.workflowMode, maxWorkers: config?.proof.maxWorkers ?? this.defaultMaxWorkers, maxSteps: config?.proof.maxSteps ?? this.defaultMaxSteps, historyLimit: config?.proof.historyLimit, workspaceDirectory: join(request.scratchDirectory, "proof-runtime"), runId: request.logicalJobId, tools: [], targetGate, tacticalDirective: request.directive as unknown as Readonly<Record<string, unknown>>, planDependencies: request.contextManifest.artifactRefs, planDependencyValidator: async (plan) => { const current = await this.researchStore.read(request.projectId); for (const ref of plan.dependencyRefs) try { const resolved = await this.researchStore.resolveArtifact(request.projectId, ref); if (resolved.artifact.artifactType === "PROMOTED_PROOF") { const currentAuthority = Object.values(current.authorityReceipts).some((authority) => authority.artifact.artifactId === ref.artifactId && authority.artifact.contentHash === ref.contentHash && activeAuthorityForClaim(current, authority.claimId, authority.claimRevision)?.authorityReceiptId === authority.authorityReceiptId); if (!currentAuthority) return `Plan dependency invalidated: promoted authority is no longer current for ${ref.artifactId}`; } } catch (error) { return `Plan dependency invalidated: ${ref.artifactId}: ${errorMessage(error)}`; } return undefined; }, evidenceProvider: (role, taskId, classification) => evidenceRecorder.refs(role, taskId, classification), ...(this.researchProofFaultAfterWorkerResults === undefined ? {} : { faultAfterWorkerResults: this.researchProofFaultAfterWorkerResults }), budget: config === undefined ? undefined : { maxPlannerCalls: Math.max(0, config.budgets.plannerCalls - projectBudget.plannerCalls), maxWorkerCalls: Math.max(0, config.budgets.workerCalls - projectBudget.workerCalls), maxVerifierCalls: Math.max(0, config.budgets.verifierCalls - projectBudget.verifierCalls), maxLiteratureSearches: Math.max(0, config.budgets.literatureSearches - projectBudget.literatureCalls), maxToolCalls: Math.max(0, config.budgets.toolCalls - projectBudget.toolCalls), maxWallTimeMs: Math.max(0, config.budgets.wallTimeSeconds * 1000 - (Date.now() - projectBudget.startedAt)) } });
		const result = await workflow.run(signal), state = workflow.state, candidateArtifacts = new Map<string, import("../research/index.js").ResearchArtifact>();
		if (this.researchProofFaultAfterWorkerResults !== undefined && result.status === "FAILED" && /Injected fault after worker result/u.test(result.reason ?? "")) { await this.syncExecutionTasks(request, workflow, candidateArtifacts); throw new Error(result.reason); }
		for (const candidate of state.candidates) candidateArtifacts.set(candidate.candidateId, await this.researchStore.putArtifact(request.projectId, { artifactType: "WORKER_CANDIDATE", body: candidate.content, provenance: `ProofRuntime-worker:${candidate.taskId}`, creationAttemptId: request.attemptId, references: candidate.evidence, metadata: { candidateId: candidate.candidateId, taskId: candidate.taskId, plannerScope: candidate.scope, strategy: candidate.strategy, discoveredEvidence: candidate.discoveredEvidence, bodyReadEvidence: candidate.bodyReadEvidence, reliedOnEvidence: candidate.evidence } }));
		const contributions: VerifiedResearchContribution[] = [];
		for (const candidate of state.candidates) { const verification = state.verifications[candidate.candidateId], draft = candidate.contribution, artifact = candidateArtifacts.get(candidate.candidateId); if (verification?.verdict !== "CORRECT" || draft === undefined || artifact === undefined || !verifierCoveredEvidence(candidate.evidence, verification.evidence ?? [])) continue; const receipt = primaryReceipt(request, candidate, artifact, verification, request.contextManifest.artifactRefs, await evidenceRecorder.list()); contributions.push({ contributionId: stableId("contribution", request.logicalJobId, candidate.candidateId, draft.kind), kind: draft.kind, statement: draft.statement, relationshipToTarget: draft.relationshipToTarget, targetObligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, ...(draft.claimId === undefined ? {} : { claimId: draft.claimId }), assumptions: draft.assumptions ?? [], dependencyClaims: draft.dependencyClaims ?? [], evidenceArtifacts: candidate.evidence, candidate: artifact, producer: { role: "worker", identity: "ProofRuntime-worker", taskId: candidate.taskId }, verification: receipt, ...(draft.childClaims === undefined ? {} : { childClaims: draft.childClaims }), ...(draft.coverageScope === undefined ? {} : { coverageScope: draft.coverageScope }), ...(draft.coverageAssertion === undefined ? {} : { coverageAssertion: draft.coverageAssertion }), ...(draft.closedCaseClaimId === undefined ? {} : { closedCaseClaimId: draft.closedCaseClaimId }), ...(draft.closureReason === undefined ? {} : { closureReason: draft.closureReason }), ...(draft.targetScope === undefined ? {} : { targetScope: draft.targetScope }), ...(draft.counterexampleScope === undefined ? {} : { counterexampleScope: draft.counterexampleScope }) }); }
		let targetSubmission: TacticalResearchResult["targetSubmission"]; const submitted = state.targetSubmission; if (submitted !== undefined) { const candidate = state.candidates.find((item) => item.candidateId === submitted.candidateId), verification = candidate === undefined ? undefined : state.verifications[candidate.candidateId], artifact = candidate === undefined ? undefined : candidateArtifacts.get(candidate.candidateId); if (candidate !== undefined && verification?.verdict === "CORRECT" && artifact !== undefined && candidate.scope === "TARGET" && candidate.targetClaimId === request.targetClaimId && verifierCoveredEvidence(candidate.evidence, verification.evidence ?? [])) targetSubmission = { submissionId: stableId("target-submission", request.logicalJobId, candidate.candidateId), targetObligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, scope: "TARGET", statement: request.obligation.statement, candidate: artifact, assumptions: candidate.assumptions, dependencies: candidate.dependencyClaims, evidenceArtifacts: candidate.evidence, primaryReceipt: primaryReceipt(request, candidate, artifact, verification, request.contextManifest.artifactRefs, await evidenceRecorder.list()) }; }
		const routeObservations = state.failedRoutes.map((failure) => { const task = state.tasks.find((item) => item.taskId === failure.taskId), candidate = state.candidates.find((item) => item.candidateFingerprint === failure.candidateFingerprint), artifact = candidate === undefined ? undefined : candidateArtifacts.get(candidate.candidateId); return { observationId: stableId("route-observation", request.logicalJobId, failure.routeFingerprint, String(failure.step)), targetObligationId: request.obligation.obligationId, routeFamily: task?.routeKey ?? task?.contributionKind?.toLocaleLowerCase() ?? "tactical", mechanism: task?.routeKey ?? candidate?.strategy ?? "tactical", strategy: candidate?.strategy ?? task?.description ?? "unspecified", status: "FAILED" as const, failureMechanism: failure.reason, evidence: artifact === undefined ? [] : [artifact] }; });
		await this.syncExecutionTasks(request, workflow, candidateArtifacts); const allEvidence = await evidenceRecorder.list(), attempt = (await this.researchStore.read(request.projectId)).attempts[request.attemptId], failureKind = classifyProofFailure(result);
		return { obligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, targetStatus: targetSubmission !== undefined ? "TARGET_PROVED" : failureKind === undefined ? "TARGET_UNRESOLVED" : "EXECUTION_FAILED", ...(targetSubmission === undefined ? {} : { targetSubmission }), contributions, routeObservations, executionReceipt: { executionReceiptId: stableId("execution-receipt", request.logicalJobId, request.attemptId), logicalJobId: request.logicalJobId, attemptId: request.attemptId, taskIds: Object.values((await this.researchStore.read(request.projectId)).executionTasks).filter((task) => task.logicalJobId === request.logicalJobId).map((task) => task.executionTaskId), evidenceReceiptIds: allEvidence.map((receipt) => receipt.receiptId), ...(failureKind === undefined ? {} : { failureKind }), startedAt: attempt?.startedAt ?? new Date().toISOString(), completedAt: new Date().toISOString() }, feedback: result.reason ?? `ProofRuntime ended ${result.status}` };
	}

	private async runLiteratureProof(request: TacticalProofRequest, signal?: AbortSignal): Promise<TacticalResearchResult> {
		const config = mathAgentConfigOf((await this.researchStore.read(request.projectId)).effectiveConfig);
		if (config?.literature.enabled === false || config?.roles.literature_researcher.enabled === false) { const now = new Date().toISOString(); return { obligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, targetStatus: "EXECUTION_FAILED", contributions: [], routeObservations: [], executionReceipt: { executionReceiptId: stableId("execution-receipt", request.logicalJobId, request.attemptId, "literature-disabled"), logicalJobId: request.logicalJobId, attemptId: request.attemptId, taskIds: [], evidenceReceiptIds: [], failureKind: "LITERATURE_ERROR", startedAt: now, completedAt: now }, feedback: "Literature production path is disabled by the project configuration snapshot" }; }
		let results; try { results = await this.discoverLiterature(request.projectId, request.decision.literatureQuery ?? request.obligation.statement, request.obligation, signal); } catch (error) { const now = new Date().toISOString(); return { obligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, targetStatus: "EXECUTION_FAILED", contributions: [], routeObservations: [], executionReceipt: { executionReceiptId: stableId("execution-receipt", request.logicalJobId, request.attemptId, "literature-error"), logicalJobId: request.logicalJobId, attemptId: request.attemptId, taskIds: [], evidenceReceiptIds: [], failureKind: signal?.aborted ? "CANCELLED" : "LITERATURE_ERROR", startedAt: now, completedAt: now }, feedback: `Literature production failure: ${errorMessage(error)}` }; } if (!results.some((item) => item.stage === "ACCEPTED_FOR_USE")) { const now = new Date().toISOString(); return { obligationId: request.obligation.obligationId, targetClaimId: request.targetClaimId, targetStatus: "EXECUTION_FAILED", contributions: [], routeObservations: [], executionReceipt: { executionReceiptId: stableId("execution-receipt", request.logicalJobId, request.attemptId, "literature-empty"), logicalJobId: request.logicalJobId, attemptId: request.attemptId, taskIds: [], evidenceReceiptIds: [], failureKind: "LITERATURE_ERROR", startedAt: now, completedAt: now }, feedback: "No acquired literature source passed applicability verification" }; }
		return this.runResearchProof(request, signal);
	}

	private async discoverLiterature(projectId: string, query: string, obligation: import("../research/index.js").ResearchObligation, signal?: AbortSignal) {
		const project = await this.researchStore.read(projectId), config = mathAgentConfigOf(project.effectiveConfig); if (config?.literature.enabled === false || config?.roles.literature_researcher.enabled === false) throw new ApiHttpError(409, "Literature is disabled by the project configuration snapshot"); if (config !== undefined && project.budget.literatureCalls >= config.budgets.literatureSearches) throw new ApiHttpError(409, "Literature search budget exhausted");
		const assessor = new AgentLiteratureApplicability(() => this.createResearchAgent("literature_researcher", projectId, stableId("literature", projectId, obligation.obligationId, query)));
		const results = await new LiteratureService(this.researchStore, this.literatureProvider).discover(projectId, query, obligation.obligationId, (candidate, body) => assessor.assess(candidate, body, obligation.statement), signal);
		await this.researchStore.transaction(projectId, (draft) => { const mutable = draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }; mutable.budget = { ...draft.budget, literatureCalls: draft.budget.literatureCalls + 1 }; mutable.events = [...draft.events, { eventId: stableId("event", projectId, "literature", obligation.obligationId, query), type: "research/literature_completed", projectId, timestamp: new Date().toISOString(), detail: { query, targetObligationId: obligation.obligationId, acquired: results.filter((item) => item.artifact !== undefined).length, accepted: results.filter((item) => item.stage === "ACCEPTED_FOR_USE").length } }]; }); return results;
	}

	private async createResearchAgent(role: ProofRole, sessionId: string, runId: string, tools?: readonly RuntimeTool[]): Promise<Agent> {
		const factory = this.createRoles.createAgent; if (factory === undefined) throw new Error(`Production research role factory is unavailable for ${role}`); let projectState: import("../research/index.js").ResearchProjectState | undefined; try { projectState = await this.researchStore.read(sessionId); } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; } const config = projectState === undefined ? this.configService?.config : this.projectConfigSnapshot(projectState); if (config?.roles[role].enabled === false) throw new Error(`Research role is disabled: ${role}`); const agent = await factory({ role, sessionId, runId, ...(tools === undefined ? {} : { tools }), ...(config === undefined ? {} : { config }) }); if (projectState !== undefined && config !== undefined) await this.researchStore.transaction(sessionId, (draft) => { const profile = config.roles[role], model = config.models[profile.model], eventId = stableId("event", sessionId, "role-config", runId, role, draft.configRevision); if (draft.events.some((item) => item.eventId === eventId)) return; (draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }).events = [...draft.events, { eventId, type: "research/role_constructed", projectId: sessionId, timestamp: new Date().toISOString(), detail: { role, provider: model?.provider, model: model?.model, reasoningEffort: model?.reasoningEffort, configRevision: draft.configRevision } }]; }); return agent;
	}

	private researchTools(projectId: string, scratchDirectory: string, evidenceRecorder: ResearchEvidenceRecorder, defaultRole?: import("../research/index.js").EvidenceRole, projectConfig?: MathAgentConfig): readonly RuntimeTool[] {
		const config = projectConfig;
		if (config?.tools.enabled === false) return [];
		return createResearchTools({ projectId, store: this.researchStore, corpus: this.researchCorpus, retrieval: new ResearchRetrievalService(this.researchStore), evidenceRecorder, ...(defaultRole === undefined ? {} : { defaultRole }), scratchDirectory, ...(config === undefined ? {} : { allowedCapabilities: config.tools.allowedCapabilities, allowedExecutables: configuredExecutables(config.tools.allowedExecutables), executionBoundary: config.tools.executionBoundary }) });
	}

	private async runResearchFormalization(projectId: string, mode: "formalize_existing" | "prove_and_formalize", existingLean?: string): Promise<Record<string, unknown>> {
		const project = await this.researchStore.read(projectId), config = mathAgentConfigOf(project.effectiveConfig);
		if (config?.formalization.enabled !== true || config.roles.formalizer.enabled === false) throw new ApiHttpError(409, "Formalization and the formalizer role must both be enabled");
		let state = await this.researchStore.read(projectId);
		if (mode === "prove_and_formalize" && state.currentFinalProofAuthority?.status !== "ACTIVE") state = await this.runRootSynthesis(projectId);
		const root = state.rootClaimId === undefined ? undefined : state.claims[state.rootClaimId]?.at(-1), finalRef = state.currentFinalProofAuthority?.status === "ACTIVE" ? state.currentFinalProofAuthority.artifact : undefined;
		if (root === undefined || finalRef === undefined) throw new ApiHttpError(409, "A fresh-audited final proof is required before formalization");
		if (!/\b(?:theorem|lemma|def)\s/iu.test(root.statement)) {
			state = (await this.researchStore.transaction(projectId, (draft) => { const mutable = draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }; mutable.formalizationStatus = "BLOCKED_FORMAL"; mutable.lastError = "FORMAL_ERROR: root objective is not an exact Lean declaration"; })).state;
			return { state, status: "BLOCKED_FORMAL", feedback: "The root objective must contain the exact Lean theorem/lemma declaration; refusing to certify an unrelated formal theorem." };
		}
		await this.researchStore.transaction(projectId, (draft) => { (draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }).formalizationStatus = "PENDING"; });
		const runId = stableId("formalization", projectId, finalRef.contentHash), agent = new AgentFormalizerRole(() => this.createResearchAgent("formalizer", projectId, runId));
		let draft;
		try { draft = await agent.formalize({ rootStatement: root.statement, informalProof: (await this.researchStore.resolveArtifact(projectId, finalRef)).body, ...(existingLean === undefined ? {} : { existingLean }) }); }
		catch (error) { state = (await this.researchStore.transaction(projectId, (project) => { const mutable = project as { -readonly [K in keyof typeof project]: typeof project[K] }; mutable.formalizationStatus = "BLOCKED_FORMAL"; mutable.lastError = `FORMAL_ERROR: ${errorMessage(error)}`; })).state; return { state, status: "BLOCKED_FORMAL", feedback: errorMessage(error) }; }
		const source = await this.researchStore.putArtifact(projectId, { artifactType: "LEAN_SOURCE", body: draft.lean, provenance: "configured-formalizer-untrusted-draft", references: [finalRef], metadata: { notes: draft.notes, mode } }), formalDirectory = resolve(config.formalization.projectDir ?? join(this.researchStore.projectDirectory(projectId), "formalization")); await mkdir(formalDirectory, { recursive: true });
		const command = config.formalization.command ?? "lake", verifier = new CommandProofFormalVerifier({ projectDirectory: formalDirectory, command, args: command.toLocaleLowerCase().endsWith("lean") ? [] : ["env", "lean"], timeoutMs: Math.max(1, config.roles.formalizer.timeoutSeconds ?? 300) * 1000 });
		const checked = await verifier.verify(draft.lean, { runId, step: 1, obligation: { obligationId: stableId("formal-obligation", projectId, root.claimId), theorem: root.statement }, theoremText: root.statement, workDirectory: formalDirectory });
		const certificate = await this.researchStore.putArtifact(projectId, { artifactType: "LEAN_CERTIFICATE", body: `${JSON.stringify(checked, null, 2)}\n`, provenance: "CommandProofFormalVerifier", references: [source] });
		let formalProof: import("../research/index.js").ResearchArtifact | undefined;
		if (checked.ok) formalProof = await this.researchStore.putArtifact(projectId, { artifactType: "FORMAL_PROOF", body: draft.lean, provenance: "process-verified-Lean", authority: "FORMAL_CERTIFICATE", references: [source, certificate] });
		state = (await this.researchStore.transaction(projectId, (project) => { const mutable = project as { -readonly [K in keyof typeof project]: typeof project[K] }; mutable.formalizationStatus = checked.ok ? "VERIFIED" : "BLOCKED_FORMAL"; mutable.lastError = checked.ok ? undefined : `FORMAL_ERROR: ${checked.feedback}`; mutable.events = [...project.events, { eventId: stableId("event", projectId, "formalization", source.contentHash, certificate.contentHash), type: "research/formalization_checked", projectId, timestamp: new Date().toISOString(), detail: { ok: checked.ok, sourceArtifactId: source.artifactId, certificateArtifactId: certificate.artifactId, ...(formalProof === undefined ? {} : { formalProofArtifactId: formalProof.artifactId }) } }]; })).state;
		return { state, status: state.formalizationStatus, source, certificate, ...(formalProof === undefined ? {} : { formalProof }), feedback: checked.feedback };
	}

	private async rootClosureService(projectId: string): Promise<RootClosureService> {
		const project = await this.researchStore.read(projectId), config = this.projectConfigSnapshot(project); if (config?.roles.synthesizer.enabled === false) throw new ApiHttpError(409, "Synthesizer role is disabled");
		const synthesisId = stableId("synthesis-run", projectId, String(project.cycle + 1));
		const synthesizer = new AgentSynthesisRole(() => this.createResearchAgent("synthesizer", projectId, synthesisId));
		const primaryAttempt = `${synthesisId}-primary`, finalAttempt = `${synthesisId}-final`, primaryDirectory = join(this.researchStore.projectDirectory(projectId), "audits", primaryAttempt), finalDirectory = join(this.researchStore.projectDirectory(projectId), "audits", finalAttempt); await mkdir(primaryDirectory, { recursive: true }); await mkdir(finalDirectory, { recursive: true }); const primaryRecorder = new ResearchEvidenceRecorder(this.researchStore, projectId, primaryAttempt), finalRecorder = new ResearchEvidenceRecorder(this.researchStore, projectId, finalAttempt), primaryTools = this.researchTools(projectId, primaryDirectory, primaryRecorder, "verifier", config), finalTools = this.researchTools(projectId, finalDirectory, finalRecorder, "secondary_auditor", config);
		const primaryRole = new AgentFinalAuditRole(() => this.createResearchAgent("verifier", projectId, primaryAttempt, primaryTools), "configured-synthesis-primary-auditor"), finalRole = new AgentFinalAuditRole(() => this.createResearchAgent("secondary_auditor", projectId, finalAttempt, finalTools), "configured-fresh-final-auditor");
		const primary = { audit: async (request: Parameters<AgentFinalAuditRole["audit"]>[0], signal?: AbortSignal) => { await this.reserveResearchBudget(projectId, "verifierCalls", config?.budgets.verifierCalls); const result = await primaryRole.audit(request, signal), reads = (await primaryRecorder.list("verifier")).filter((item) => item.operation === "READ"); return { ...result, evidenceInspected: reads.map((item) => item.artifact), toolReceiptIds: reads.map((item) => item.receiptId) }; } }, final = { audit: async (request: Parameters<AgentFinalAuditRole["audit"]>[0], signal?: AbortSignal) => { await this.reserveResearchBudget(projectId, "secondaryAuditorCalls", config?.budgets.secondaryAuditorCalls); const result = await finalRole.audit(request, signal), reads = (await finalRecorder.list("secondary_auditor")).filter((item) => item.operation === "READ"); return { ...result, evidenceInspected: reads.map((item) => item.artifact), toolReceiptIds: reads.map((item) => item.receiptId) }; } };
		return new RootClosureService(this.researchStore, synthesizer, primary, final);
	}
	private async reserveResearchBudget(projectId: string, counter: "verifierCalls" | "secondaryAuditorCalls", limit?: number): Promise<void> { await this.researchStore.transaction(projectId, (draft) => { if (limit !== undefined && draft.budget[counter] >= limit) throw new ApiHttpError(409, `${counter} budget exhausted`); (draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }).budget = { ...draft.budget, [counter]: draft.budget[counter] + 1 }; }); }
	private projectConfigSnapshot(project: import("../research/index.js").ResearchProjectState): MathAgentConfig | undefined { const config = mathAgentConfigOf(project.effectiveConfig); if (config === undefined && this.configService !== undefined) throw new Error(`Project ${project.projectId} has no valid effective-config snapshot; refusing to mix the current live configuration into an existing project`); return config; }
	private async runRootSynthesis(projectId: string, signal?: AbortSignal): Promise<import("../research/index.js").ResearchProjectState> { return (await this.rootClosureService(projectId)).synthesizeAndAudit(projectId, signal); }

	private async syncExecutionTasks(request: TacticalProofRequest, workflow: ProofWorkflow, artifacts: ReadonlyMap<string, import("../research/index.js").ResearchArtifact>): Promise<void> {
		const now = new Date().toISOString(), proofState = workflow.state;
		await this.researchStore.transaction(request.projectId, (draft) => { const mutable = draft as { -readonly [K in keyof typeof draft]: typeof draft[K] }, tasks = { ...draft.executionTasks }; let newPlanners = 0, newWorkers = 0, newVerifiers = 0;
			const executionPlans = { ...draft.executionPlans }; for (const plan of proofState.executionPlans) executionPlans[plan.planId] = { planId: plan.planId, logicalJobId: request.logicalJobId, attemptId: request.attemptId, step: plan.step, inputHash: plan.inputHash, taskIds: plan.taskIds, dependencyRefs: plan.dependencyRefs, actionExecutions: plan.actionExecutions.map((action) => ({ actionId: action.actionId, planId: action.planId, ordinal: action.ordinal, action: action.action as unknown as Readonly<Record<string, unknown>>, status: action.status, resultArtifactIds: action.resultArtifactIds.map((id) => artifacts.get(id)?.artifactId ?? id), effectIds: action.effectIds, ...(action.result === undefined ? {} : { result: action.result }), ...(action.startedAt === undefined ? {} : { startedAt: action.startedAt }), ...(action.completedAt === undefined ? {} : { completedAt: action.completedAt }), ...(action.error === undefined ? {} : { error: action.error }) })), status: plan.status, ...(plan.staleReason === undefined ? {} : { staleReason: plan.staleReason }), createdAt: plan.createdAt, ...(plan.completedAt === undefined ? {} : { completedAt: plan.completedAt }) };
			for (const step of proofState.stepHistory) { const plannerId = stableId("execution-task", request.logicalJobId, "PLANNER", `step-${step.step}`); if (tasks[plannerId] === undefined) newPlanners += 1; tasks[plannerId] = { executionTaskId: plannerId, logicalJobId: request.logicalJobId, attemptId: request.attemptId, kind: "PLANNER", logicalTaskId: `step-${step.step}`, status: step.status === "interrupted" ? "INTERRUPTED" : step.status === "failed" ? "FAILED_RETRYABLE" : "COMPLETED", inputHash: stableId("input", request.logicalJobId, "planner", String(step.step)), startedAt: now, ...(step.status === "completed" ? { completedAt: now } : {}) }; }
			for (const task of proofState.tasks) { const workerId = stableId("execution-task", request.logicalJobId, "WORKER", task.taskId), candidate = proofState.candidates.find((item) => item.taskId === task.taskId), workerCompleted = task.status === "COMPLETED", workerStatus = executionTaskStatusForProofTask(task.status); if (tasks[workerId] === undefined && workerCompleted) newWorkers += 1; tasks[workerId] = { executionTaskId: workerId, logicalJobId: request.logicalJobId, attemptId: request.attemptId, kind: "WORKER", logicalTaskId: task.taskId, status: workerStatus, inputHash: stableId("input", task.taskId, task.description), ...(candidate === undefined || artifacts.get(candidate.candidateId) === undefined ? {} : { resultArtifact: artifacts.get(candidate.candidateId) }), startedAt: now, ...(workerCompleted ? { completedAt: now } : {}) }; if (candidate !== undefined) { const verifierId = stableId("execution-task", request.logicalJobId, "VERIFIER", candidate.candidateId), completed = proofState.verifications[candidate.candidateId] !== undefined; if (tasks[verifierId] === undefined && completed) newVerifiers += 1; tasks[verifierId] = { executionTaskId: verifierId, logicalJobId: request.logicalJobId, attemptId: request.attemptId, kind: "VERIFIER", logicalTaskId: candidate.candidateId, status: completed ? "COMPLETED" : "INTERRUPTED", inputHash: stableId("input", candidate.candidateFingerprint), ...(artifacts.get(candidate.candidateId) === undefined ? {} : { resultArtifact: artifacts.get(candidate.candidateId) }), startedAt: now, ...(completed ? { completedAt: now } : {}) }; } }
			const mergeId = stableId("execution-task", request.logicalJobId, "MERGE", "merge"), mergeCompleted = proofState.executionPlans.length === 0 || proofState.executionPlans.every((plan) => plan.status === "COMPLETED"); tasks[mergeId] = { executionTaskId: mergeId, logicalJobId: request.logicalJobId, attemptId: request.attemptId, kind: "MERGE", logicalTaskId: "merge", status: mergeCompleted ? "COMPLETED" : "INTERRUPTED", inputHash: stableId("input", request.logicalJobId, "merge"), startedAt: tasks[mergeId]?.startedAt ?? now, ...(mergeCompleted ? { completedAt: now } : {}) }; if (proofState.targetSubmission !== undefined) { const targetId = stableId("execution-task", request.logicalJobId, "TARGET_SUBMISSION", proofState.targetSubmission.candidateId); tasks[targetId] = { executionTaskId: targetId, logicalJobId: request.logicalJobId, attemptId: request.attemptId, kind: "TARGET_SUBMISSION", logicalTaskId: proofState.targetSubmission.candidateId, status: "COMPLETED", inputHash: stableId("input", proofState.targetSubmission.candidateId), ...(artifacts.get(proofState.targetSubmission.candidateId) === undefined ? {} : { resultArtifact: artifacts.get(proofState.targetSubmission.candidateId) }), startedAt: now, completedAt: now }; }
			mutable.executionTasks = tasks; mutable.executionPlans = executionPlans; mutable.budget = { ...draft.budget, plannerCalls: draft.budget.plannerCalls + newPlanners, workerCalls: draft.budget.workerCalls + newWorkers, verifierCalls: draft.budget.verifierCalls + newVerifiers };
		});
	}

	private async handleConfigRoute(parts: readonly string[], request: IncomingMessage, response: ServerResponse): Promise<void> {
		const service = this.configService;
		if (service === undefined) throw new ApiHttpError(404, "Configuration API is not enabled");
		if (request.method === "GET" && parts.length === 2) {
			this.send(response, 200, service.publicSnapshot());
			return;
		}
		if (request.method === "GET" && parts.length === 3 && parts[2] === "document") {
			this.send(response, 200, { revision: service.revision, toml: service.tomlText });
			return;
		}
		if (request.method === "GET" && parts.length === 3 && parts[2] === "models") {
			const snapshot = service.publicSnapshot();
			const models = Object.fromEntries(Object.entries(snapshot.models).map(([name, model]) => [name, {
				provider: model.provider,
				model: model.model,
				enabled: model.enabled,
				credentialConfigured: model.credentialConfigured,
			}]));
			this.send(response, 200, {
				revision: snapshot.revision,
				models,
				roles: snapshot.roles,
				providers: ["mock", "openai", "openai-codex", "anthropic", "google", "google-vertex", "openrouter", "deepseek"],
			});
			return;
		}
		if (request.method === "PUT" && parts.length === 2) {
			const body = await readJsonObject(request);
			const expectedRevision = optionalString(body.expectedRevision);
			const value = typeof body.toml === "string"
				? await service.replaceToml(body.toml, expectedRevision)
				: await service.update((body.update ?? body.config ?? {}) as ConfigUpdate, expectedRevision);
			this.send(response, 200, value);
			return;
		}
		throw new ApiHttpError(404, "Configuration route not found");
	}

	private async createSession(request: IncomingMessage, response: ServerResponse): Promise<void> {
		const body = await readJsonObject(request);
		const requestedId = optionalString(body.sessionId);
		const sessionId = requestedId ?? randomUUID();
		if (!/^[A-Za-z0-9_-]{1,100}$/u.test(sessionId)) throw new ApiHttpError(400, "sessionId must contain only letters, numbers, '_' or '-'");
		if (this.sessions.has(sessionId)) throw new ApiHttpError(409, `Session already exists: ${sessionId}`);
		const session = await Session.create({
			projectId: sessionId,
			sessionId,
			cwd: this.rootDirectory,
			directory: join(this.rootDirectory, "sessions"),
		});
		this.sessions.set(sessionId, { session, runs: new Map() });
		this.send(response, 201, {
			sessionId,
			projectId: session.projectId,
			filePath: session.filePath,
			status: "OPEN",
		});
	}

	private async submitTheorem(sessionId: string, record: SessionRecord, request: IncomingMessage, response: ServerResponse): Promise<void> {
		const body = await readJsonObject(request);
		const theorem = requiredString(body.theorem, "theorem");
		const obligationId = optionalString(body.obligationId) ?? `${sessionId}-obligation`;
		const config = this.configService?.config;
		const requestedMode = parseMode(body.mode) ?? config?.proof.defaultMode ?? this.defaultMode;
		const mode = effectiveProofMode(requestedMode, config);
		const context = optionalString(body.context);
		const leanTheorem = optionalString(body.leanTheorem ?? body.lean_theorem);
		const obligation: ProofObligation = {
			obligationId,
			theorem,
			...(context === undefined ? {} : { context }),
		};
		record.pendingObligation = obligation;
		record.pendingLeanTheorem = leanTheorem;
		record.mode = mode;
		await record.session.appendMessage(createUserMessage(`Prove the following theorem:\n\n${theorem}`));
		await record.session.appendCustom({
			namespace: "proof-api",
			type: "theorem_submitted",
			payload: { type: "theorem_submitted", sessionId, obligationId, theorem, mode, ...(context === undefined ? {} : { context }), ...(leanTheorem === undefined ? {} : { leanTheorem }) },
		});
		this.send(response, 200, { sessionId, obligation, mode, ...(leanTheorem === undefined ? {} : { leanTheorem }), status: "THEOREM_ACCEPTED" });
	}

	private async startProofRun(sessionId: string, record: SessionRecord, request: IncomingMessage, response: ServerResponse): Promise<void> {
		if (record.pendingObligation === undefined) throw new ApiHttpError(400, "Submit a theorem before starting a proof run");
		const body = await readJsonObject(request);
		const config = this.configService?.config;
		const requestedMode = parseMode(body.mode) ?? record.mode ?? config?.proof.defaultMode ?? this.defaultMode;
		const mode = effectiveProofMode(requestedMode, config);
		const runId = optionalString(body.runId) ?? randomUUID();
		if (!/^[A-Za-z0-9_-]{1,100}$/u.test(runId)) throw new ApiHttpError(400, "runId must contain only letters, numbers, '_' or '-'");
		const existing = record.runs.get(runId);
		if (existing !== undefined) {
			if (existing.promise !== undefined) throw new ApiHttpError(409, `Proof run is already active: ${runId}`);
			await this.beginRun(existing);
			this.sendRunStarted(response, sessionId, runId, mode, "RESUMING");
			return;
		}
		const maxWorkers = positiveInteger(body.maxWorkers) ?? this.defaultMaxWorkers;
		const maxSteps = positiveInteger(body.maxSteps) ?? this.defaultMaxSteps;
		const workflowMode = parseWorkflowMode(body.workflowMode) ?? config?.proof.workflowMode;
		const roles = await this.createRoles({ session: record.session, sessionId, runId, obligation: record.pendingObligation, mode, ...(config === undefined ? {} : { config }) });
		const workflow = new ProofWorkflow({
			session: record.session,
			obligation: record.pendingObligation,
			mode,
			workflowMode,
			...roles,
			maxWorkers,
			maxSteps,
			historyLimit: config?.proof.historyLimit,
			workspaceDirectory: join(this.rootDirectory, "proof-runs", sessionId, runId),
			runId,
			...(record.pendingLeanTheorem === undefined ? {} : { leanTheorem: record.pendingLeanTheorem }),
				...(config?.formalization.enabled === true && mode !== "prove" ? { formalVerifier: configuredProofFormalVerifier(config, join(this.rootDirectory, "proof-runs", sessionId, runId)) } : {}),
			...(config === undefined ? {} : { runConfig: { config: jsonObjectOf(config) } }),
		});
		const run: RunRecord = { runId, workflow, controller: new AbortController(), startedAt: Date.now() };
		record.runs.set(runId, run);
		await this.beginRun(run);
		this.sendRunStarted(response, sessionId, runId, mode, "RUNNING");
	}

	private async beginRun(run: RunRecord): Promise<void> {
		if (run.promise !== undefined) return;
		run.controller = new AbortController();
		run.promise = Promise.resolve().then(() => run.workflow.run(run.controller.signal)).then(async (result) => {
			run.result = result;
			run.finishedAt = Date.now();
			await this.persistRunResult(run);
			this.closeRunStreams(run);
			return result;
		}).catch((error: unknown) => {
			const result: ProofRunResult = {
				runId: run.runId,
				status: "BLOCKED_PROVIDER",
				mode: run.workflow.state.mode,
				steps: run.workflow.state.step,
				reason: errorMessage(error),
			};
			run.result = result;
			run.finishedAt = Date.now();
			void this.persistRunResult(run);
			this.closeRunStreams(run);
			return result;
		});
	}

	private async persistRunResult(run: RunRecord): Promise<void> {
		if (run.result === undefined) return;
		await writeFile(join(run.workflow.runDirectoryPath, "result.json"), `${JSON.stringify(run.result, null, 2)}\n`, "utf8");
	}

	private closeRunStreams(run: RunRecord): void {
		const responses = this.sseRunResponses.get(run);
		if (responses === undefined) return;
		for (const response of responses) response.end();
		this.sseRunResponses.delete(run);
	}

	private sendRunStarted(response: ServerResponse, sessionId: string, runId: string, mode: ProofMode, status: string): void {
		this.send(response, 202, {
			sessionId,
			runId,
			status,
			mode,
			links: {
				status: `/v1/sessions/${sessionId}/proof-runs/${runId}`,
				result: `/v1/sessions/${sessionId}/proof-runs/${runId}/result`,
				events: `/v1/sessions/${sessionId}/proof-runs/${runId}/events`,
			},
		});
	}

	private cancelRun(sessionId: string, run: RunRecord, response: ServerResponse): void {
		if (run.promise === undefined || run.result !== undefined) {
			this.send(response, 409, { error: { code: "NOT_ACTIVE", message: `Proof run ${run.runId} is not active` }, sessionId, runId: run.runId });
			return;
		}
		run.controller.abort();
		this.send(response, 202, { sessionId, runId: run.runId, status: "CANCELLATION_REQUESTED" });
	}

	private async getRunResult(sessionId: string, run: RunRecord, response: ServerResponse): Promise<void> {
		const state = run.workflow.state;
		const result = run.result ?? (isTerminalStatus(state.status) && run.promise === undefined ? resultFromState(run) : undefined);
		if (result === undefined) {
			this.send(response, 202, { ready: false, sessionId, runId: run.runId, status: state.status, state });
			return;
		}
		const proof = result.proofPath === undefined ? await readFirstExisting(join(run.workflow.runDirectoryPath, "PROOF.md")) : await readFirstExisting(result.proofPath);
		const formalProof = result.proofLeanPath === undefined ? await readFirstExisting(join(run.workflow.runDirectoryPath, "PROOF.lean")) : await readFirstExisting(result.proofLeanPath);
		this.send(response, 200, {
			ready: true,
			sessionId,
			runId: run.runId,
			status: result.status,
			result,
			answer: { ...(proof === undefined ? {} : { proof }), ...(formalProof === undefined ? {} : { formalProof }) },
			state,
		});
	}

	private async streamRunEvents(run: RunRecord, request: IncomingMessage, response: ServerResponse): Promise<void> {
		await run.workflow.hydrate();
		response.statusCode = 200;
		response.setHeader("Content-Type", "text/event-stream; charset=utf-8");
		response.setHeader("Cache-Control", "no-cache, no-transform");
		response.setHeader("Connection", "keep-alive");
		this.setCors(response);
		this.sseResponses.add(response);
		const runResponses = this.sseRunResponses.get(run) ?? new Set<ServerResponse>();
		runResponses.add(response);
		this.sseRunResponses.set(run, runResponses);
		const lastEventId = typeof request.headers["last-event-id"] === "string" ? request.headers["last-event-id"] : undefined;
		const startIndex = lastEventId === undefined ? 0 : Math.max(0, run.workflow.events.findIndex((event) => event.eventId === lastEventId) + 1);
		for (const event of run.workflow.events.slice(startIndex)) this.writeSse(response, event);
		if (run.result !== undefined || isTerminalStatus(run.workflow.state.status)) {
			this.removeSseResponse(run, response);
			response.end();
			return;
		}
		const keepAlive = setInterval(() => {
			if (!response.writableEnded) response.write(": keep-alive\n\n");
		}, 15_000);
		const dispose = run.workflow.subscribe((event) => {
			if (response.writableEnded) return;
			this.writeSse(response, event);
			if (isTerminalStatus(event.type === "proof/status_changed" ? event.status : run.workflow.state.status)) {
				clearInterval(keepAlive);
				this.removeSseResponse(run, response);
				response.end();
			}
		});
		const cleanup = (): void => {
			clearInterval(keepAlive);
			dispose();
			this.removeSseResponse(run, response);
		};
		request.once("close", cleanup);
		response.once("close", cleanup);
	}

	private writeSse(response: ServerResponse, event: { readonly eventId: string; readonly type: string }): void {
		response.write(`id: ${event.eventId}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
	}

	private async discoverPersistedSessions(): Promise<void> {
		const sessionDirectory = join(this.rootDirectory, "sessions");
		const sessionEntries = await readdir(sessionDirectory, { withFileTypes: true });
		for (const entry of sessionEntries) {
			if (!entry.isFile() || !entry.name.endsWith(".jsonl")) continue;
			const filePath = join(sessionDirectory, entry.name);
			const session = await Session.resume(filePath);
			const record: SessionRecord = { session, runs: new Map() };
			const theorem = [...session.customEntries("proof-api")].reverse().find((item) => item.type === "theorem_submitted");
			if (theorem !== undefined && isRecord(theorem.payload) && typeof theorem.payload.theorem === "string") {
				record.pendingObligation = {
					obligationId: typeof theorem.payload.obligationId === "string" ? theorem.payload.obligationId : `${session.sessionId}-obligation`,
					theorem: theorem.payload.theorem,
					...(typeof theorem.payload.context === "string" ? { context: theorem.payload.context } : {}),
				};
				record.pendingLeanTheorem = typeof theorem.payload.leanTheorem === "string" ? theorem.payload.leanTheorem : undefined;
				record.mode = parseMode(theorem.payload.mode) ?? this.defaultMode;
			}
			this.sessions.set(session.sessionId, record);
			await this.discoverRuns(session.sessionId, record);
		}
	}

	private async discoverRuns(sessionId: string, record: SessionRecord): Promise<void> {
		if (record.pendingObligation === undefined) return;
		const directory = join(this.rootDirectory, "proof-runs", sessionId);
		let entries;
		try { entries = await readdir(directory, { withFileTypes: true }); } catch (error) { if (isMissingFile(error)) return; throw error; }
		for (const entry of entries) {
			if (!entry.isDirectory() || !/^[A-Za-z0-9_-]{1,100}$/u.test(entry.name)) continue;
			const runConfig = await readJsonFile(join(directory, entry.name, "run_config.json"));
			if (runConfig === undefined) continue;
			const runId = entry.name;
			const mode = parseMode(runConfig.mode) ?? record.mode ?? this.defaultMode;
			const config = mathAgentConfigOf(runConfig.config);
			const workflowMode = parseWorkflowMode(runConfig.workflowMode) ?? config?.proof.workflowMode;
			const roles = await this.createRoles({ session: record.session, sessionId, runId, obligation: record.pendingObligation, mode, ...(config === undefined ? {} : { config }) });
			const workflow = new ProofWorkflow({
				session: record.session,
				obligation: record.pendingObligation,
				mode,
				workflowMode,
				...roles,
				maxWorkers: positiveInteger(runConfig.maxWorkers) ?? this.defaultMaxWorkers,
				maxSteps: positiveInteger(runConfig.maxSteps) ?? this.defaultMaxSteps,
				historyLimit: config?.proof.historyLimit,
				workspaceDirectory: join(directory, runId),
				runId,
				...(record.pendingLeanTheorem === undefined ? {} : { leanTheorem: record.pendingLeanTheorem }),
				...(config?.formalization.enabled === true && mode !== "prove" ? { formalVerifier: configuredProofFormalVerifier(config, join(directory, runId)) } : {}),
				runConfig: jsonObjectOf(runConfig),
			});
			await workflow.hydrate();
			const persistedResult = await readJsonFile(join(directory, runId, "result.json"));
			const result = persistedResult !== undefined && isProofRunResult(persistedResult) ? persistedResult : undefined;
			record.runs.set(runId, {
				runId,
				workflow,
				controller: new AbortController(),
				startedAt: numberOrNow(runConfig.startedAt),
				...(result === undefined ? {} : { result, finishedAt: numberOrNow(runConfig.finishedAt) }),
			});
		}
	}

	private sessionView(sessionId: string, record: SessionRecord): Record<string, unknown> {
		return {
			sessionId,
			projectId: record.session.projectId,
			filePath: record.session.filePath,
			entryCount: record.session.entries.length,
			customEntryCount: record.session.customEntries().length,
			...(record.pendingObligation === undefined ? {} : { obligation: record.pendingObligation }),
			...(record.pendingLeanTheorem === undefined ? {} : { leanTheorem: record.pendingLeanTheorem }),
			...(record.mode === undefined ? {} : { mode: record.mode }),
			runs: [...record.runs.values()].map((run) => this.runView(sessionId, run)),
		};
	}

	private sessionSummary(sessionId: string, record: SessionRecord): Record<string, unknown> {
		return {
			sessionId,
			projectId: record.session.projectId,
			createdAt: record.session.entries[0]?.kind === "session" ? record.session.entries[0].createdAt : undefined,
			theorem: record.pendingObligation?.theorem,
			runCount: record.runs.size,
			latestStatus: [...record.runs.values()].at(-1)?.workflow.state.status ?? "OPEN",
		};
	}

	private runView(sessionId: string, run: RunRecord): Record<string, unknown> {
		const state: ProofState = run.workflow.state;
		return {
			sessionId,
			runId: run.runId,
			status: state.status,
			step: state.step,
			mode: state.mode,
			ready: run.result !== undefined,
			resumable: run.promise === undefined && !isTerminalStatus(state.status),
			startedAt: run.startedAt,
			...(run.finishedAt === undefined ? {} : { finishedAt: run.finishedAt }),
			...(run.result === undefined ? {} : { result: run.result }),
			state,
		};
	}

	private send(response: ServerResponse, status: number, value: unknown): void {
		response.statusCode = status;
		response.setHeader("Content-Type", "application/json; charset=utf-8");
		this.setCors(response);
		if (value === undefined) {
			response.end();
			return;
		}
		response.end(JSON.stringify(value));
	}

	private setCors(response: ServerResponse): void {
		response.setHeader("Access-Control-Allow-Origin", "*");
		response.setHeader("Access-Control-Allow-Headers", "content-type,last-event-id");
		response.setHeader("Access-Control-Allow-Methods", "GET,PUT,POST,OPTIONS");
	}

	private sendError(response: ServerResponse, error: unknown): void {
		if (response.headersSent) {
			response.end();
			return;
		}
		const status = error instanceof ConfigConflictError ? 409 : error instanceof ApiHttpError ? error.status : 500;
		this.send(response, status, {
			error: {
				code: error instanceof ConfigConflictError ? error.code : error instanceof ApiHttpError ? error.code : "INTERNAL_ERROR",
				message: errorMessage(error),
			},
		});
	}

	private allRuns(): RunRecord[] {
		return [...this.sessions.values()].flatMap((record) => [...record.runs.values()]);
	}

	private removeSseResponse(run: RunRecord, response: ServerResponse): void {
		this.sseResponses.delete(response);
		const responses = this.sseRunResponses.get(run);
		if (responses === undefined) return;
		responses.delete(response);
		if (responses.size === 0) this.sseRunResponses.delete(run);
	}
}

class ApiHttpError extends Error {
	readonly status: number;
	readonly code: string;

	constructor(status: number, message: string, code = status === 404 ? "NOT_FOUND" : status === 409 ? "CONFLICT" : "BAD_REQUEST") {
		super(message);
		this.name = "ApiHttpError";
		this.status = status;
		this.code = code;
	}
}

async function readJsonObject(request: IncomingMessage): Promise<Record<string, unknown>> {
	const chunks: Buffer[] = [];
	let size = 0;
	for await (const chunk of request) {
		const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
		size += buffer.length;
		if (size > 2 * 1024 * 1024) throw new ApiHttpError(413, "Request body is too large", "PAYLOAD_TOO_LARGE");
		chunks.push(buffer);
	}
	if (chunks.length === 0) return {};
	let parsed: unknown;
	try { parsed = JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown; } catch { throw new ApiHttpError(400, "Request body must be valid JSON", "INVALID_JSON"); }
	if (!isRecord(parsed)) throw new ApiHttpError(400, "Request body must be a JSON object", "INVALID_BODY");
	return parsed;
}

async function readJsonFile(path: string): Promise<Record<string, unknown> | undefined> {
	try {
		const value: unknown = JSON.parse(await readFile(path, "utf8")) as unknown;
		return isRecord(value) ? value : undefined;
	} catch (error) {
		if (isMissingFile(error)) return undefined;
		throw error;
	}
}

async function readFirstExisting(path: string): Promise<string | undefined> {
	try { await access(path); return readFile(path, "utf8"); } catch { return undefined; }
}

function resultFromState(run: RunRecord): ProofRunResult {
	const state = run.workflow.state;
	return {
		runId: run.runId,
		status: state.status,
		mode: state.mode,
		workflowMode: state.workflowMode,
		steps: state.step,
		...(state.submittedCandidateId === undefined ? {} : { candidateId: state.submittedCandidateId }),
		...(state.proofLeanPath === undefined ? {} : { proofLeanPath: state.proofLeanPath }),
	};
}

function executionTaskStatusForProofTask(status: import("../proof/types.js").ProofTaskStatus): import("../research/types.js").ExecutionTaskStatus {
	if (status === "COMPLETED") return "COMPLETED";
	if (status === "FAILED_TERMINAL") return "FAILED_TERMINAL";
	if (status === "FAILED_RETRYABLE" || status === "BLOCKED" || status === "PARTIAL") return "FAILED_RETRYABLE";
	if (status === "RUNNING") return "INTERRUPTED";
	return "PENDING";
}

function isProofRunResult(value: Record<string, unknown>): value is ProofRunResult {
	return typeof value.runId === "string"
		&& (value.status === "PROVED" || value.status === "CANDIDATE_READY" || value.status === "PARTIAL" || value.status === "FAILED" || value.status === "BLOCKED_FORMAL" || value.status === "BLOCKED_PROVIDER" || value.status === "CANCELLED")
			&& (value.mode === "prove" || value.mode === "prove_and_formalize" || value.mode === "formalize_only")
			&& (value.workflowMode === undefined || value.workflowMode === "dynamic" || value.workflowMode === "legacy")
			&& typeof value.steps === "number";
}

function isTerminalStatus(status: ProofStatus): boolean {
	return TERMINAL_STATUSES.includes(status);
}

function requiredString(value: unknown, name: string): string {
	if (typeof value !== "string" || value.trim().length === 0) throw new ApiHttpError(400, `${name} must be a non-empty string`);
	return value.trim();
}

function optionalString(value: unknown): string | undefined {
	return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

function parseMode(value: unknown): ProofMode | undefined {
	if (value === undefined) return undefined;
	if (value === "prove" || value === "prove_and_formalize" || value === "formalize_only") return value;
	throw new ApiHttpError(400, "mode must be prove, prove_and_formalize, or formalize_only");
}

/**
 * Once the configured application enables formalization, an old UI/client
 * sending `mode=prove` must not be able to finish on PROOF.md alone. The
 * explicit `formalize_only` policy remains respected; every other request is
 * upgraded to the configured end-to-end formal workflow.
 */
function effectiveProofMode(mode: ProofMode, config: MathAgentConfig | undefined): ProofMode {
	if (mode !== "prove" || config?.formalization.enabled !== true) return mode;
	return config.proof.defaultMode === "formalize_only" ? "formalize_only" : "prove_and_formalize";
}

function parseWorkflowMode(value: unknown): ProofWorkflowMode | undefined {
	if (value === undefined) return undefined;
	if (value === "dynamic" || value === "legacy") return value;
	throw new ApiHttpError(400, "workflowMode must be dynamic or legacy");
}

function positiveInteger(value: unknown): number | undefined {
	if (value === undefined) return undefined;
	if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 10_000) throw new ApiHttpError(400, "numeric limits must be positive integers <= 10000");
	return value;
}

function numberOrNow(value: unknown): number {
	return typeof value === "number" && Number.isFinite(value) ? value : Date.now();
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function jsonObjectOf(value: unknown): JsonObject {
	const cloned: unknown = JSON.parse(JSON.stringify(value)) as unknown;
	return isRecord(cloned) ? cloned as JsonObject : {};
}

function mathAgentConfigOf(value: unknown): MathAgentConfig | undefined {
	if (!isRecord(value) || typeof value.version !== "number" || !isRecord(value.models) || !isRecord(value.roles)) return undefined;
	return value as unknown as MathAgentConfig;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMissingFile(error: unknown): boolean {
	return (error as NodeJS.ErrnoException | undefined)?.code === "ENOENT";
}

function researchLinks(projectId: string): Readonly<Record<string, string>> {
	const base = `/v1/research/projects/${projectId}`;
	return { status: base, audit: `${base}/audit`, frontier: `${base}/frontier`, claims: `${base}/claims`, dependencies: `${base}/dependencies`, coverage: `${base}/coverage`, routes: `${base}/routes`, artifacts: `${base}/artifacts`, literature: `${base}/literature`, bootstrapReport: `${base}/bootstrap-report`, checkpoints: `${base}/checkpoints`, events: `${base}/events`, rootReadiness: `${base}/root-readiness`, synthesis: `${base}/synthesis`, formalization: `${base}/formalization`, result: `${base}/result`, resume: `${base}/resume` };
}

function configuredProofFormalVerifier(config: MathAgentConfig, fallbackDirectory: string): CommandProofFormalVerifier {
	const command = config.formalization.command ?? "lake";
	return new CommandProofFormalVerifier({ projectDirectory: resolve(config.formalization.projectDir ?? fallbackDirectory), command, args: command.toLocaleLowerCase().endsWith("lean") ? [] : ["env", "lean"], timeoutMs: Math.max(1, config.roles.formalizer.timeoutSeconds ?? 300) * 1000 });
}

function configuredImportAuthority(value: string | undefined): import("../research/index.js").ImportAuthority {
	const allowed: readonly import("../research/index.js").ImportAuthority[] = ["VERIFIED_CURRENT", "VERIFIED_IMPORTED", "PROVISIONAL_IMPORTED", "UNVERIFIED_NOTE", "FAILED_HISTORICAL_ROUTE", "OPEN_HISTORICAL_OBLIGATION", "DEFINITION", "LITERATURE_SOURCE", "COMPUTATIONAL_EVIDENCE", "FORMAL_CERTIFICATE"];
	return allowed.includes(value as import("../research/index.js").ImportAuthority) ? value as import("../research/index.js").ImportAuthority : "PROVISIONAL_IMPORTED";
}

function configuredExecutables(names: readonly string[]): Readonly<Record<string, string>> {
	const supported: Readonly<Record<string, string>> = { node: process.execPath, python: "python", lean: "lean", lake: "lake" };
	return Object.fromEntries(names.flatMap((name) => supported[name] === undefined ? [] : [[name, supported[name]]]));
}

function stableResearchAuditId(projectId: string, hash: string): string {
	return `audit-${projectId}-${hash.slice(0, 16)}`;
}

function verifierCoveredEvidence(worker: readonly { readonly artifactId: string; readonly contentHash: string }[], verifier: readonly { readonly artifactId: string; readonly contentHash: string }[]): boolean { const inspected = new Set(verifier.map((ref) => `${ref.artifactId}:${ref.contentHash}`)); return worker.every((ref) => inspected.has(`${ref.artifactId}:${ref.contentHash}`)); }

function classifyProofFailure(result: ProofRunResult): import("../research/index.js").ResearchFailureKind | undefined {
	const reason = result.reason?.toLocaleLowerCase() ?? "";
	if (result.status === "CANCELLED") return "CANCELLED";
	if (/\b(?:quota|rate limit|429)\b/u.test(reason)) return "QUOTA_ERROR";
	if (/\b(?:budget|call limit|wall time)\b/u.test(reason)) return "BUDGET_EXHAUSTED";
	if (/\b(?:tool error|tool failed|invalid_tool)\b/u.test(reason)) return "TOOL_ERROR";
	if (/\b(?:protocol|malformed|structured json|schema)\b/u.test(reason)) return "PROTOCOL_ERROR";
	return result.status === "BLOCKED_PROVIDER" ? "PROVIDER_ERROR" : undefined;
}

function primaryReceipt(request: TacticalProofRequest, candidate: import("../proof/types.js").ProofCandidate, artifact: import("../research/index.js").ResearchArtifact, verification: import("../proof/types.js").VerificationResult, available: readonly import("../research/index.js").ArtifactRef[], evidenceReceipts: readonly import("../research/index.js").ToolEvidenceReceipt[]): TrustReceipt { const verifierEvidence = (verification.evidence ?? []).map(({ artifactId, contentHash }) => ({ artifactId, contentHash })), workerRead = candidate.bodyReadEvidence.map(({ artifactId, contentHash }) => ({ artifactId, contentHash })), reliedOn = candidate.evidence.map(({ artifactId, contentHash }) => ({ artifactId, contentHash })); return { receiptId: stableId("receipt", artifact.artifactId, "primary", candidate.candidateId), claimId: candidate.targetClaimId ?? request.targetClaimId, candidate: artifact, verifierProfile: "configured-independent-verifier", evidenceInspected: verifierEvidence, availableEvidence: available, workerReadEvidence: workerRead, workerDeclaredEvidence: reliedOn, verifierReadEvidence: verifierEvidence, toolReceiptIds: evidenceReceipts.filter((item) => item.logicalTaskId === candidate.taskId).map((item) => item.receiptId), verdict: verification.verdict === "NEEDS_MINOR_FIXES" ? "MINOR_FIX" : verification.verdict, independentContext: true, stale: false, createdAt: new Date().toISOString() }; }
