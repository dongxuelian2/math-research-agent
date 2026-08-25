import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import { access, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createUserMessage } from "../models/index.js";
import { ConfigConflictError, type ConfigUpdate, type MathAgentConfig, type MathAgentConfigService } from "../config.js";
import type { JsonObject } from "../models/json.js";
import { Session } from "../session/index.js";
import { ProofWorkflow } from "../proof/runtime.js";
import type { AgentProofRoles } from "../proof/agent-role.js";
import type {
	ProofMode,
	ProofObligation,
	ProofRunResult,
	ProofState,
	ProofStatus,
} from "../proof/types.js";

export type ProofApiRoleFactory = (context: {
	readonly session: Session;
	readonly sessionId: string;
	readonly runId: string;
	readonly obligation: ProofObligation;
	readonly mode: ProofMode;
	/** Immutable configuration selected when this run was created. */
	readonly config?: MathAgentConfig;
}) => AgentProofRoles | Promise<AgentProofRoles>;

export type ProofApiServerOptions = {
	readonly rootDirectory: string;
	readonly createRoles: ProofApiRoleFactory;
	readonly configService?: MathAgentConfigService;
	readonly defaultMode?: ProofMode;
	readonly defaultMaxWorkers?: number;
	readonly defaultMaxSteps?: number;
};

type SessionRecord = {
	readonly session: Session;
	pendingObligation?: ProofObligation;
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
	"PROVED", "PARTIAL", "FAILED", "BLOCKED_PROVIDER", "CANCELLED",
];

export class ProofApiServer {
	private readonly rootDirectory: string;
	private readonly createRoles: ProofApiRoleFactory;
	private readonly configService?: MathAgentConfigService;
	private readonly defaultMode: ProofMode;
	private readonly defaultMaxWorkers: number;
	private readonly defaultMaxSteps: number;
	private readonly sessions = new Map<string, SessionRecord>();
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
	}

	get baseUrl(): string | undefined {
		return this.baseUrlValue;
	}

	/** Start the standalone HTTP adapter used by tests and the local launcher. */
	async start(options: { readonly host?: string; readonly port?: number } = {}): Promise<string> {
		if (this.server !== undefined && this.baseUrlValue !== undefined) return this.baseUrlValue;
		await mkdir(join(this.rootDirectory, "sessions"), { recursive: true });
		await mkdir(join(this.rootDirectory, "proof-runs"), { recursive: true });
		if (this.configService !== undefined) {
			await this.configService.load();
			await this.configService.startWatching();
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
		await Promise.allSettled(this.allRuns().map((run) => run.promise ?? Promise.resolve()));
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
				providers: ["mock", "openai", "openai-codex", "anthropic", "google", "openrouter", "deepseek"],
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
		const mode = parseMode(body.mode) ?? this.defaultMode;
		const context = optionalString(body.context);
		const obligation: ProofObligation = {
			obligationId,
			theorem,
			...(context === undefined ? {} : { context }),
		};
		record.pendingObligation = obligation;
		record.mode = mode;
		await record.session.appendMessage(createUserMessage(`Prove the following theorem:\n\n${theorem}`));
		await record.session.appendCustom({
			namespace: "proof-api",
			type: "theorem_submitted",
			payload: { type: "theorem_submitted", sessionId, obligationId, theorem, mode, ...(context === undefined ? {} : { context }) },
		});
		this.send(response, 200, { sessionId, obligation, mode, status: "THEOREM_ACCEPTED" });
	}

	private async startProofRun(sessionId: string, record: SessionRecord, request: IncomingMessage, response: ServerResponse): Promise<void> {
		if (record.pendingObligation === undefined) throw new ApiHttpError(400, "Submit a theorem before starting a proof run");
		const body = await readJsonObject(request);
		const mode = parseMode(body.mode) ?? record.mode ?? this.defaultMode;
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
		const config = this.configService?.config;
		const roles = await this.createRoles({ session: record.session, sessionId, runId, obligation: record.pendingObligation, mode, ...(config === undefined ? {} : { config }) });
		const workflow = new ProofWorkflow({
			session: record.session,
			obligation: record.pendingObligation,
			mode,
			...roles,
			maxWorkers,
			maxSteps,
			workspaceDirectory: join(this.rootDirectory, "proof-runs", sessionId, runId),
			runId,
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
			const roles = await this.createRoles({ session: record.session, sessionId, runId, obligation: record.pendingObligation, mode, ...(config === undefined ? {} : { config }) });
			const workflow = new ProofWorkflow({
				session: record.session,
				obligation: record.pendingObligation,
				mode,
				...roles,
				maxWorkers: positiveInteger(runConfig.maxWorkers) ?? this.defaultMaxWorkers,
				maxSteps: positiveInteger(runConfig.maxSteps) ?? this.defaultMaxSteps,
				workspaceDirectory: join(directory, runId),
				runId,
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
		steps: state.step,
		...(state.submittedCandidateId === undefined ? {} : { candidateId: state.submittedCandidateId }),
		...(state.proofLeanPath === undefined ? {} : { proofLeanPath: state.proofLeanPath }),
	};
}

function isProofRunResult(value: Record<string, unknown>): value is ProofRunResult {
	return typeof value.runId === "string"
		&& (value.status === "PROVED" || value.status === "CANDIDATE_READY" || value.status === "PARTIAL" || value.status === "FAILED" || value.status === "BLOCKED_PROVIDER" || value.status === "CANCELLED")
		&& (value.mode === "prove" || value.mode === "prove_and_formalize" || value.mode === "formalize_only")
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
