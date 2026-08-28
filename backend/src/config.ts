import { createHash } from "node:crypto";
import { mkdir, readFile, rename, watch as watchFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import type { ProofMode, ProofWorkflowMode } from "./proof/types.js";
import type { JsonValue } from "./models/json.js";
import { createProvider } from "./providers/registry.js";
import type { MockResponse } from "./providers/mock.js";
import type { ModelConfig, ProviderId, ReasoningEffort } from "./providers/types.js";
import type { CorpusPublishingConfig } from "./research/corpus-archive-types.js";

export const PROOF_ROLES = [
	"research_director",
	"corpus_bootstrapper",
	"planner",
	"worker",
	"verifier",
	"secondary_auditor",
	"synthesizer",
	"formalizer",
	"literature_researcher",
] as const;

export type ProofRole = (typeof PROOF_ROLES)[number];

export type ModelProfile = {
	readonly provider: ProviderId;
	readonly model: string;
	readonly baseUrl?: string;
	readonly apiKeyEnv?: string;
	readonly serviceAccountFileEnv?: string;
	readonly reasoningEffort?: ReasoningEffort;
	readonly contextWindow?: number;
	readonly maxTokens?: number;
	readonly requestHeaders?: Readonly<Record<string, string>>;
	readonly requestParameters?: Readonly<Record<string, JsonValue>>;
	readonly enabled?: boolean;
};

export type RoleProfile = {
	readonly model: string;
	readonly enabled?: boolean;
	readonly maxTurns?: number;
	readonly timeoutSeconds?: number;
};

export type MathAgentConfig = {
	readonly version: number;
	readonly runtime: {
		readonly host: "127.0.0.1" | "0.0.0.0";
		readonly webPort: number;
		readonly proofApiPort: number;
		readonly dataDir: string;
	};
	readonly proof: {
		readonly defaultMode: ProofMode;
		readonly workflowMode: ProofWorkflowMode;
		readonly maxWorkers: number;
		readonly maxSteps: number;
		readonly historyLimit: number;
	};
	readonly formalization: {
		readonly enabled: boolean;
		readonly projectDir?: string;
		readonly command?: string;
	};
	readonly models: Readonly<Record<string, ModelProfile>>;
	readonly roles: Readonly<Record<ProofRole, RoleProfile>>;
	readonly literature: {
		readonly enabled: boolean;
	};
	readonly research: {
		readonly maxCycles: number;
		readonly checkpointInterval: number;
		readonly stallThreshold: number;
		readonly structuralProbeBudget: number;
		readonly maxActiveObligations: number;
	};
	readonly budgets: {
		readonly plannerCalls: number;
		readonly workerCalls: number;
		readonly verifierCalls: number;
		readonly secondaryAuditorCalls: number;
		readonly literatureSearches: number;
		readonly toolCalls: number;
		readonly wallTimeSeconds: number;
	};
	readonly corpus: {
		readonly enabled: boolean; readonly roots: readonly string[]; readonly importAuthorityPolicy: string;
		readonly publishingEnabled: boolean; readonly repositoryUrl: string; readonly localCheckout: string; readonly branch: string;
		readonly autoPush: boolean; readonly indexCommand: readonly string[]; readonly nodePath?: string;
	};
	readonly tools: { readonly enabled: boolean; readonly allowedCapabilities: readonly string[]; readonly allowedExecutables: readonly string[]; readonly executionBoundary: "CONTROLLED_COMMAND_RUNNER" };
};

export type PublicModelProfile = ModelProfile & {
	readonly credentialConfigured: boolean;
};

export type PublicMathAgentConfig = Omit<MathAgentConfig, "models"> & {
	readonly models: Readonly<Record<string, PublicModelProfile>>;
	readonly revision: string;
	readonly path: string;
};

export type ConfigUpdate = {
	readonly runtime?: Partial<MathAgentConfig["runtime"]>;
	readonly proof?: Partial<MathAgentConfig["proof"]>;
	readonly formalization?: Partial<MathAgentConfig["formalization"]>;
	readonly models?: Readonly<Record<string, Partial<ModelProfile>>>;
	readonly roles?: Partial<Record<ProofRole, Partial<RoleProfile>>>;
	readonly literature?: Partial<MathAgentConfig["literature"]>;
	readonly research?: Partial<MathAgentConfig["research"]>;
	readonly budgets?: Partial<MathAgentConfig["budgets"]>;
	readonly corpus?: Partial<MathAgentConfig["corpus"]>;
	readonly tools?: Partial<MathAgentConfig["tools"]>;
};

export const DEFAULT_CONFIG: MathAgentConfig = {
	version: 1,
	runtime: {
		host: "127.0.0.1",
		webPort: 3080,
		proofApiPort: 4310,
		dataDir: ".math-agent",
	},
	proof: {
		defaultMode: "prove",
		workflowMode: "dynamic",
		maxWorkers: 2,
		maxSteps: 8,
		historyLimit: 8,
	},
	formalization: {
		enabled: false,
	},
	models: {
		mock: { provider: "mock", model: "math-proof-offline" },
	},
	roles: {
		research_director: { model: "mock" },
		corpus_bootstrapper: { model: "mock" },
		planner: { model: "mock" },
		worker: { model: "mock" },
		verifier: { model: "mock" },
		secondary_auditor: { model: "mock" },
		synthesizer: { model: "mock", enabled: true },
		formalizer: { model: "mock", enabled: false },
		literature_researcher: { model: "mock", enabled: false },
	},
	literature: { enabled: false },
	research: { maxCycles: 100, checkpointInterval: 1, stallThreshold: 3, structuralProbeBudget: 2, maxActiveObligations: 8 },
	budgets: { plannerCalls: 1000, workerCalls: 1000, verifierCalls: 1000, secondaryAuditorCalls: 100, literatureSearches: 100, toolCalls: 5000, wallTimeSeconds: 86400 },
	corpus: { enabled: true, roots: [], importAuthorityPolicy: "PROVISIONAL_IMPORTED", publishingEnabled: false, repositoryUrl: "https://github.com/dongxuelian2/three-term-decimal-concatenation-square-sum.git", localCheckout: "", branch: "main", autoPush: false, indexCommand: ["python", "tools/update-research-index.py"] },
	tools: { enabled: true, allowedCapabilities: ["artifact_search", "artifact_read", "artifact_metadata", "corpus_read", "corpus_search", "scratch_read", "scratch_write", "controlled_computation"], allowedExecutables: ["node", "python", "lean", "lake"], executionBoundary: "CONTROLLED_COMMAND_RUNNER" },
};

export function corpusPublishingConfigOf(config: MathAgentConfig | undefined): CorpusPublishingConfig {
	const corpus = config?.corpus;
	return { enabled: corpus?.publishingEnabled ?? false, repositoryUrl: corpus?.repositoryUrl ?? DEFAULT_CONFIG.corpus.repositoryUrl, localCheckout: corpus?.localCheckout ?? "", branch: corpus?.branch ?? DEFAULT_CONFIG.corpus.branch, autoPush: corpus?.autoPush ?? false, indexCommand: corpus?.indexCommand ?? DEFAULT_CONFIG.corpus.indexCommand, ...(corpus?.nodePath === undefined ? {} : { nodePath: corpus.nodePath }) };
}

export class ConfigConflictError extends Error {
	readonly code = "CONFIG_CONFLICT" as const;

	constructor(readonly expectedRevision: string, readonly actualRevision: string) {
		super(`Configuration changed on disk; expected ${expectedRevision}, found ${actualRevision}`);
		this.name = "ConfigConflictError";
	}
}

/**
 * The one durable configuration authority used by the API, role factory and
 * settings UI. The implementation intentionally has no provider secrets in
 * memory snapshots returned to clients.
 */
export class MathAgentConfigService {
	private configValue: MathAgentConfig = DEFAULT_CONFIG;
	private textValue = "";
	private revisionValue = "";
	private watcher: AsyncIterableIterator<{ readonly eventType: string }> | undefined;
	private watcherAbort: AbortController | undefined;
	private mutationTail: Promise<void> = Promise.resolve();

	constructor(readonly path: string) {}

	get revision(): string {
		return this.revisionValue;
	}

	get config(): MathAgentConfig {
		return this.configValue;
	}

	/**
	 * Return the canonical, secret-free TOML document used by the settings
	 * editor. Never expose the raw file contents: a manually edited file may
	 * contain an accidental `api_key` field even though the supported schema
	 * only stores an environment-variable name.
	 */
	get tomlText(): string {
		return stringifyMathAgentConfig(this.configValue);
	}

	async load(): Promise<MathAgentConfig> {
		const filename = resolve(this.path);
		try {
			this.textValue = await readFile(filename, "utf8");
		} catch (error) {
			if (!isMissingFile(error)) throw error;
			this.textValue = stringifyMathAgentConfig(DEFAULT_CONFIG);
			await mkdir(dirname(filename), { recursive: true });
			await writeFile(filename, this.textValue, { encoding: "utf8", flag: "wx", mode: 0o600 }).catch((writeError: unknown) => {
				if (!isAlreadyExists(writeError)) throw writeError;
			});
			if (this.textValue.length === 0) this.textValue = await readFile(filename, "utf8");
		}
		this.configValue = normalizeConfig(parseToml(this.textValue));
		this.revisionValue = revisionOf(this.textValue);
		return this.configValue;
	}

	async startWatching(onUpdate?: (config: MathAgentConfig) => void | Promise<void>): Promise<void> {
		if (this.watcher !== undefined) return;
		this.watcherAbort = new AbortController();
		const iterator = watchFile(resolve(this.path), { persistent: false, signal: this.watcherAbort.signal }) as AsyncIterableIterator<{ readonly eventType: string }>;
		this.watcher = iterator;
		void (async () => {
			try {
				for await (const event of iterator) {
					if (event.eventType !== "change" && event.eventType !== "rename") continue;
					try {
						const previous = this.revisionValue;
						await this.load();
						if (previous !== this.revisionValue) await onUpdate?.(this.configValue);
					} catch {
						// An editor can expose a partially written file between rename and
						// publication. The next event retries; the active config remains valid.
					}
				}
			} catch {
				// Closing an async fs watcher is expected during shutdown.
			}
		})();
	}

	async close(): Promise<void> {
		this.watcherAbort?.abort();
		// Aborting the fs watcher is synchronous from the caller's perspective;
		// awaiting iterator.return() can hang on Windows after the abort.
		void this.watcher?.return?.();
		this.watcher = undefined;
		this.watcherAbort = undefined;
	}

	publicSnapshot(): PublicMathAgentConfig {
		const models: Record<string, PublicModelProfile> = {};
		for (const [name, model] of Object.entries(this.configValue.models)) {
			models[name] = {
				...model,
				credentialConfigured: model.apiKeyEnv === undefined && model.serviceAccountFileEnv === undefined
					? false
					: Boolean(
						(model.apiKeyEnv !== undefined && process.env[model.apiKeyEnv])
							|| (model.serviceAccountFileEnv !== undefined && process.env[model.serviceAccountFileEnv]),
					),
			};
		}
		return {
			...this.configValue,
			models,
			revision: this.revisionValue,
			path: resolve(this.path),
		};
	}

	async replaceToml(text: string, expectedRevision?: string): Promise<PublicMathAgentConfig> {
		return this.withMutation(async () => {
			this.assertRevision(expectedRevision);
			const normalized = normalizeConfig(parseToml(text));
			await this.persist(stringifyMathAgentConfig(normalized));
			return this.publicSnapshot();
		});
	}

	async update(update: ConfigUpdate, expectedRevision?: string): Promise<PublicMathAgentConfig> {
		return this.withMutation(async () => {
			this.assertRevision(expectedRevision);
			const current = this.configValue;
			const models: Record<string, ModelProfile> = { ...current.models };
			for (const [name, patch] of Object.entries(update.models ?? {})) {
				const old = models[name] ?? { provider: "mock", model: name };
				models[name] = normalizeModel({ ...old, ...patch });
			}
			const roles: Record<ProofRole, RoleProfile> = { ...current.roles };
			for (const role of PROOF_ROLES) {
				const patch = update.roles?.[role];
				if (patch !== undefined) roles[role] = normalizeRole({ ...roles[role], ...patch });
			}
			const next = normalizeConfig({
				...current,
				runtime: { ...current.runtime, ...update.runtime },
				proof: { ...current.proof, ...update.proof },
				formalization: { ...current.formalization, ...update.formalization },
				models,
				roles,
				literature: { ...current.literature, ...update.literature },
				research: { ...current.research, ...update.research },
				budgets: { ...current.budgets, ...update.budgets },
				corpus: { ...current.corpus, ...update.corpus },
				tools: { ...current.tools, ...update.tools },
			});
			await this.persist(stringifyMathAgentConfig(next));
			return this.publicSnapshot();
		});
	}

	private async withMutation<T>(operation: () => Promise<T>): Promise<T> {
		const previous = this.mutationTail;
		let release!: () => void;
		this.mutationTail = new Promise<void>((resolvePromise) => { release = resolvePromise; });
		await previous;
		try {
			return await operation();
		} finally {
			release();
		}
	}

	private assertRevision(expectedRevision: string | undefined): void {
		if (expectedRevision !== undefined && expectedRevision !== this.revisionValue) {
			throw new ConfigConflictError(expectedRevision, this.revisionValue);
		}
	}

	private async persist(text: string): Promise<void> {
		const filename = resolve(this.path);
		await mkdir(dirname(filename), { recursive: true, mode: 0o700 });
		const temporary = `${filename}.${process.pid}.${Date.now()}.tmp`;
		await writeFile(temporary, text, { encoding: "utf8", mode: 0o600 });
		await rename(temporary, filename);
		this.textValue = text;
		this.configValue = normalizeConfig(parseToml(text));
		this.revisionValue = revisionOf(text);
	}
}

export function modelConfigOf(profile: ModelProfile): ModelConfig {
	return {
		provider: profile.provider,
		model: profile.model,
		...(profile.baseUrl === undefined ? {} : { baseUrl: profile.baseUrl }),
		...(profile.reasoningEffort === undefined ? {} : { reasoningEffort: profile.reasoningEffort }),
		...(profile.contextWindow === undefined ? {} : { contextWindow: profile.contextWindow }),
		...(profile.maxTokens === undefined ? {} : { maxTokens: profile.maxTokens }),
		...(profile.requestHeaders === undefined ? {} : { requestHeaders: profile.requestHeaders }),
		...(profile.requestParameters === undefined ? {} : { requestParameters: profile.requestParameters }),
		...(profile.apiKeyEnv === undefined ? {} : { credentialResolver: () => process.env[profile.apiKeyEnv as string] }),
		...(profile.serviceAccountFileEnv === undefined
			? {}
			: { credentialFileResolver: () => process.env[profile.serviceAccountFileEnv as string] }),
	};
}

export function createMockResponses(role: ProofRole, targetGate?: { readonly targetObligationId: string; readonly targetClaimId: string }): readonly MockResponse[] {
	if (role === "planner") {
		if (targetGate !== undefined) return [
			textResponse(JSON.stringify({ actions: [{ action: "spawn", tasks: [{ taskId: "mock-target-task", summary: "Prove the exact research target", description: "Give a complete proof of the exact target theorem.", scope: "TARGET", targetClaimId: targetGate.targetClaimId }] }] })),
			textResponse(JSON.stringify({ actions: [{ action: "submit_target_proof", candidateId: "mock-target-task-candidate", targetObligationId: targetGate.targetObligationId, targetClaimId: targetGate.targetClaimId }] })),
		];
		return [
			textResponse('{"actions":[{"action":"write_whiteboard","content":"Use ordinary mathematical induction and check the base case."},{"action":"spawn","tasks":[{"taskId":"mock-proof-task","summary":"Construct a complete proof","description":"Give a complete, self-contained proof of the theorem."}]}]}'),
			textResponse('{"actions":[{"action":"submit_proof","candidateId":"mock-proof-task-candidate"}]}'),
		];
	}
	if (role === "worker") {
		return [textResponse(`{"kind":"candidate","candidate":{"strategy":"induction","scope":"${targetGate === undefined ? "CONTRIBUTION" : "TARGET"}","content":"For n = 1, the identity is immediate. Assume the identity holds for n. Adding the next odd number gives n^2 + (2n + 1) = (n + 1)^2, so the statement holds for n + 1. By mathematical induction, the identity holds for every n >= 1."}}`)];
	}
	if (role === "verifier") {
		return [textResponse('{"verdict":"CORRECT","feedback":"The base case and induction step are valid."}')];
	}
	if (role === "secondary_auditor") return [textResponse('{"verdict":"CORRECT","feedback":"Fresh independent audit found no gap."}')];
	if (role === "corpus_bootstrapper") return [textResponse('{"proposals":[],"dependencies":[],"warnings":[]}')];
	if (role === "research_director") return [textResponse('{"action":"ATTACK_OBLIGATION","reason":"offline mock delegates target selection to deterministic validation"}')];
	if (role === "synthesizer") return [textResponse('{"proof":"The authoritative dependency and coverage manifest yields the stated root conclusion.","usedArtifactIds":[]}')];
	if (role === "formalizer") return [textResponse('{"lean":"theorem mrr_placeholder : True := by\\n  trivial","notes":"Untrusted offline draft; the process gate remains authoritative."}')];
	if (role === "literature_researcher") return [textResponse('{"applicable":false,"reason":"offline mock has no source","assumptions":[],"scopeLimitations":[]}')];
	return [];
}

function textResponse(text: string): MockResponse {
	return { events: [{ type: "text_delta", text }, { type: "complete", stopReason: "end_turn" }] };
}

function normalizeConfig(value: Record<string, unknown>): MathAgentConfig {
	const runtime = record(value.runtime) ?? {};
	const proof = record(value.proof) ?? {};
	const formalization = record(value.formalization) ?? {};
	const literature = record(value.literature) ?? {};
	const research = record(value.research) ?? {};
	const budgets = record(value.budgets) ?? {};
	const corpus = record(value.corpus) ?? {};
	const corpusIndexCommand = corpus.index_command ?? corpus.indexCommand;
	const tools = record(value.tools) ?? {};
	const modelValues = record(value.models) ?? {};
	const roleValues = record(value.roles) ?? {};
	const models: Record<string, ModelProfile> = {};
	for (const [name, raw] of Object.entries(modelValues)) models[name] = normalizeModel(record(raw) ?? {}, name);
	if (Object.keys(models).length === 0) Object.assign(models, DEFAULT_CONFIG.models);
	const roles: Record<ProofRole, RoleProfile> = {} as Record<ProofRole, RoleProfile>;
	for (const role of PROOF_ROLES) {
		roles[role] = normalizeRole(record(roleValues[role]) ?? DEFAULT_CONFIG.roles[role]);
	}
	for (const [role, profile] of Object.entries(roles)) {
		if (profile.model.length === 0 || models[profile.model] === undefined) throw new Error(`Role ${role} references unknown model ${profile.model}`);
	}
	return {
		version: positiveInteger(value.version, 1),
		runtime: {
			host: runtime.host === "0.0.0.0" ? "0.0.0.0" : "127.0.0.1",
			webPort: boundedInteger(runtime.web_port ?? runtime.webPort, DEFAULT_CONFIG.runtime.webPort),
			proofApiPort: boundedInteger(runtime.proof_api_port ?? runtime.proofApiPort, DEFAULT_CONFIG.runtime.proofApiPort),
			dataDir: stringValue(runtime.data_dir ?? runtime.dataDir, DEFAULT_CONFIG.runtime.dataDir),
		},
		proof: {
			defaultMode: proof.default_mode === "prove_and_formalize" || proof.defaultMode === "prove_and_formalize"
				? "prove_and_formalize"
				: proof.default_mode === "formalize_only" || proof.defaultMode === "formalize_only" ? "formalize_only" : "prove",
			workflowMode: proof.workflow_mode === "legacy" || proof.workflowMode === "legacy" ? "legacy" : "dynamic",
			maxWorkers: boundedInteger(proof.max_workers ?? proof.maxWorkers, DEFAULT_CONFIG.proof.maxWorkers),
			maxSteps: boundedInteger(proof.max_steps ?? proof.maxSteps, DEFAULT_CONFIG.proof.maxSteps),
			historyLimit: boundedInteger(proof.history_limit ?? proof.historyLimit, DEFAULT_CONFIG.proof.historyLimit),
		},
		formalization: {
			enabled: booleanValue(formalization.enabled, DEFAULT_CONFIG.formalization.enabled),
			...(stringValueOrUndefined(formalization.project_dir ?? formalization.projectDir) === undefined ? {} : { projectDir: stringValueOrUndefined(formalization.project_dir ?? formalization.projectDir) }),
			...(stringValueOrUndefined(formalization.command) === undefined ? {} : { command: stringValueOrUndefined(formalization.command) }),
		},
		models,
		roles,
		literature: { enabled: booleanValue(literature.enabled, DEFAULT_CONFIG.literature.enabled) },
		research: {
			maxCycles: boundedInteger(research.max_cycles ?? research.maxCycles, DEFAULT_CONFIG.research.maxCycles),
			checkpointInterval: boundedInteger(research.checkpoint_interval ?? research.checkpointInterval, DEFAULT_CONFIG.research.checkpointInterval),
			stallThreshold: boundedInteger(research.stall_threshold ?? research.stallThreshold, DEFAULT_CONFIG.research.stallThreshold),
			structuralProbeBudget: boundedInteger(research.structural_probe_budget ?? research.structuralProbeBudget, DEFAULT_CONFIG.research.structuralProbeBudget),
			maxActiveObligations: boundedInteger(research.max_active_obligations ?? research.maxActiveObligations, DEFAULT_CONFIG.research.maxActiveObligations),
		},
		budgets: {
			plannerCalls: boundedInteger(budgets.planner_calls ?? budgets.plannerCalls, DEFAULT_CONFIG.budgets.plannerCalls), workerCalls: boundedInteger(budgets.worker_calls ?? budgets.workerCalls, DEFAULT_CONFIG.budgets.workerCalls), verifierCalls: boundedInteger(budgets.verifier_calls ?? budgets.verifierCalls, DEFAULT_CONFIG.budgets.verifierCalls), secondaryAuditorCalls: boundedInteger(budgets.secondary_auditor_calls ?? budgets.secondaryAuditorCalls, DEFAULT_CONFIG.budgets.secondaryAuditorCalls), literatureSearches: boundedInteger(budgets.literature_searches ?? budgets.literatureSearches, DEFAULT_CONFIG.budgets.literatureSearches), toolCalls: boundedInteger(budgets.tool_calls ?? budgets.toolCalls, DEFAULT_CONFIG.budgets.toolCalls), wallTimeSeconds: boundedInteger(budgets.wall_time_seconds ?? budgets.wallTimeSeconds, DEFAULT_CONFIG.budgets.wallTimeSeconds),
		},
		corpus: {
			enabled: booleanValue(corpus.enabled, DEFAULT_CONFIG.corpus.enabled), roots: Array.isArray(corpus.roots) ? corpus.roots.filter((item): item is string => typeof item === "string") : [], importAuthorityPolicy: configuredImportAuthority(corpus.import_authority_policy ?? corpus.importAuthorityPolicy),
			publishingEnabled: booleanValue(corpus.publishing_enabled ?? corpus.publishingEnabled, DEFAULT_CONFIG.corpus.publishingEnabled), repositoryUrl: stringValue(corpus.repository_url ?? corpus.repositoryUrl, DEFAULT_CONFIG.corpus.repositoryUrl), localCheckout: stringValue(corpus.local_checkout ?? corpus.localCheckout, DEFAULT_CONFIG.corpus.localCheckout), branch: stringValue(corpus.branch, DEFAULT_CONFIG.corpus.branch), autoPush: booleanValue(corpus.auto_push ?? corpus.autoPush, DEFAULT_CONFIG.corpus.autoPush), indexCommand: Array.isArray(corpusIndexCommand) ? corpusIndexCommand.filter((item): item is string => typeof item === "string") : DEFAULT_CONFIG.corpus.indexCommand, ...(stringValueOrUndefined(corpus.node_path ?? corpus.nodePath) === undefined ? {} : { nodePath: stringValueOrUndefined(corpus.node_path ?? corpus.nodePath) }),
		},
		tools: { enabled: booleanValue(tools.enabled, DEFAULT_CONFIG.tools.enabled), allowedCapabilities: configuredCapabilityNames(tools.allowed_capabilities ?? tools.allowedCapabilities), allowedExecutables: configuredExecutableNames(tools.allowed_executables ?? tools.allowedExecutables), executionBoundary: controlledExecutionBoundary(tools.execution_boundary ?? tools.executionBoundary ?? tools.sandbox_policy ?? tools.sandboxPolicy) },
	};
}

function normalizeModel(value: Record<string, unknown>, fallbackName = "model"): ModelProfile {
	const provider = value.provider;
	const allowed: ProviderId[] = ["mock", "openai", "openai-codex", "anthropic", "google", "google-vertex", "openrouter", "deepseek"];
	if (typeof provider !== "string" || !allowed.includes(provider as ProviderId)) throw new Error(`Model ${fallbackName} has an unsupported provider`);
	const model = stringValue(value.model, fallbackName);
	const effort = value.reasoning_effort ?? value.reasoningEffort;
	return {
		provider: provider as ProviderId,
		model,
		...(stringValueOrUndefined(value.base_url ?? value.baseUrl) === undefined ? {} : { baseUrl: stringValueOrUndefined(value.base_url ?? value.baseUrl) }),
		...(stringValueOrUndefined(value.api_key_env ?? value.apiKeyEnv) === undefined ? {} : { apiKeyEnv: stringValueOrUndefined(value.api_key_env ?? value.apiKeyEnv) }),
		...(stringValueOrUndefined(value.service_account_file_env ?? value.serviceAccountFileEnv) === undefined ? {} : { serviceAccountFileEnv: stringValueOrUndefined(value.service_account_file_env ?? value.serviceAccountFileEnv) }),
		...(effort === "low" || effort === "medium" || effort === "high" || effort === "xhigh" || effort === "max" ? { reasoningEffort: effort } : {}),
		...(numberValue(value.context_window ?? value.contextWindow) === undefined ? {} : { contextWindow: numberValue(value.context_window ?? value.contextWindow) }),
		...(numberValue(value.max_tokens ?? value.maxTokens) === undefined ? {} : { maxTokens: numberValue(value.max_tokens ?? value.maxTokens) }),
		...(record(value.request_headers ?? value.requestHeaders) === undefined ? {} : { requestHeaders: stringRecord(record(value.request_headers ?? value.requestHeaders) as Record<string, unknown>) }),
		...(record(value.request_parameters ?? value.requestParameters) === undefined ? {} : { requestParameters: jsonRecord(record(value.request_parameters ?? value.requestParameters) as Record<string, unknown>) }),
		...(value.enabled === undefined ? {} : { enabled: Boolean(value.enabled) }),
	};
}

function normalizeRole(value: Record<string, unknown>): RoleProfile {
	return {
		model: stringValue(value.model, "mock"),
		...(value.enabled === undefined ? {} : { enabled: Boolean(value.enabled) }),
		...(numberValue(value.max_turns ?? value.maxTurns) === undefined ? {} : { maxTurns: numberValue(value.max_turns ?? value.maxTurns) }),
		...(numberValue(value.timeout_seconds ?? value.timeoutSeconds) === undefined ? {} : { timeoutSeconds: numberValue(value.timeout_seconds ?? value.timeoutSeconds) }),
	};
}

export function stringifyMathAgentConfig(config: MathAgentConfig): string {
	const lines = [`version = ${config.version}`, "", "[runtime]", `host = ${quote(config.runtime.host)}`, `web_port = ${config.runtime.webPort}`, `proof_api_port = ${config.runtime.proofApiPort}`, `data_dir = ${quote(config.runtime.dataDir)}`, "", "[proof]", `default_mode = ${quote(config.proof.defaultMode)}`, `workflow_mode = ${quote(config.proof.workflowMode)}`, `max_workers = ${config.proof.maxWorkers}`, `max_steps = ${config.proof.maxSteps}`, `history_limit = ${config.proof.historyLimit}`, "", "[formalization]", `enabled = ${config.formalization.enabled}`];
	if (config.formalization.projectDir !== undefined) lines.push(`project_dir = ${quote(config.formalization.projectDir)}`);
	if (config.formalization.command !== undefined) lines.push(`command = ${quote(config.formalization.command)}`);
	lines.push("", "[literature]", `enabled = ${config.literature.enabled}`);
	lines.push("", "[research]", `max_cycles = ${config.research.maxCycles}`, `checkpoint_interval = ${config.research.checkpointInterval}`, `stall_threshold = ${config.research.stallThreshold}`, `structural_probe_budget = ${config.research.structuralProbeBudget}`, `max_active_obligations = ${config.research.maxActiveObligations}`);
	lines.push("", "[budgets]", `planner_calls = ${config.budgets.plannerCalls}`, `worker_calls = ${config.budgets.workerCalls}`, `verifier_calls = ${config.budgets.verifierCalls}`, `secondary_auditor_calls = ${config.budgets.secondaryAuditorCalls}`, `literature_searches = ${config.budgets.literatureSearches}`, `tool_calls = ${config.budgets.toolCalls}`, `wall_time_seconds = ${config.budgets.wallTimeSeconds}`);
	lines.push("", "[corpus]", `enabled = ${config.corpus.enabled}`, `roots = [${config.corpus.roots.map(quote).join(", ")}]`, `import_authority_policy = ${quote(config.corpus.importAuthorityPolicy)}`, `publishing_enabled = ${config.corpus.publishingEnabled}`, `repository_url = ${quote(config.corpus.repositoryUrl)}`, `local_checkout = ${quote(config.corpus.localCheckout)}`, `branch = ${quote(config.corpus.branch)}`, `auto_push = ${config.corpus.autoPush}`, `index_command = [${config.corpus.indexCommand.map(quote).join(", ")}]`);
	if (config.corpus.nodePath !== undefined) lines.push(`node_path = ${quote(config.corpus.nodePath)}`);
	lines.push("", "[tools]", `enabled = ${config.tools.enabled}`, `allowed_capabilities = [${config.tools.allowedCapabilities.map(quote).join(", ")}]`, `allowed_executables = [${config.tools.allowedExecutables.map(quote).join(", ")}]`, `execution_boundary = ${quote(config.tools.executionBoundary)}`);
	for (const [name, model] of Object.entries(config.models)) {
		lines.push("", `[models.${tomlKey(name)}]`, `provider = ${quote(model.provider)}`, `model = ${quote(model.model)}`);
		if (model.baseUrl !== undefined) lines.push(`base_url = ${quote(model.baseUrl)}`);
		if (model.apiKeyEnv !== undefined) lines.push(`api_key_env = ${quote(model.apiKeyEnv)}`);
		if (model.serviceAccountFileEnv !== undefined) lines.push(`service_account_file_env = ${quote(model.serviceAccountFileEnv)}`);
		if (model.reasoningEffort !== undefined) lines.push(`reasoning_effort = ${quote(model.reasoningEffort)}`);
		if (model.contextWindow !== undefined) lines.push(`context_window = ${model.contextWindow}`);
		if (model.maxTokens !== undefined) lines.push(`max_tokens = ${model.maxTokens}`);
		if (model.requestHeaders !== undefined) lines.push(`request_headers = ${inlineTable(model.requestHeaders)}`);
		if (model.requestParameters !== undefined) lines.push(`request_parameters = ${inlineJsonTable(model.requestParameters)}`);
		if (model.enabled !== undefined) lines.push(`enabled = ${model.enabled}`);
	}
	for (const role of PROOF_ROLES) {
		const profile = config.roles[role];
		lines.push("", `[roles.${tomlKey(role)}]`, `model = ${quote(profile.model)}`);
		if (profile.enabled !== undefined) lines.push(`enabled = ${profile.enabled}`);
		if (profile.maxTurns !== undefined) lines.push(`max_turns = ${profile.maxTurns}`);
		if (profile.timeoutSeconds !== undefined) lines.push(`timeout_seconds = ${profile.timeoutSeconds}`);
	}
	return `${lines.join("\n")}\n`;
}

export function parseToml(text: string): Record<string, unknown> {
	const root: Record<string, unknown> = {};
	let table: string[] = [];
	for (const raw of text.replaceAll("\r\n", "\n").split("\n")) {
		const line = stripTomlComment(raw).trim();
		if (line.length === 0) continue;
		const tableMatch = line.match(/^\[([^\]]+)\]$/u);
		if (tableMatch !== null) {
			table = splitTomlPath(tableMatch[1] ?? "");
			ensureRecordPath(root, table);
			continue;
		}
		const equals = findTopLevel(line, "=");
		if (equals < 1) throw new Error(`Invalid TOML assignment: ${line}`);
		const key = line.slice(0, equals).trim();
		const value = parseTomlValue(line.slice(equals + 1).trim());
		const target = ensureRecordPath(root, table);
		target[unquoteKey(key)] = value;
	}
	return root;
}

function parseTomlValue(value: string): unknown {
	if (value.startsWith("[") && value.endsWith("]")) return splitTopLevel(value.slice(1, -1), ",").filter(Boolean).map(parseTomlValue);
	if (value.startsWith("{") && value.endsWith("}")) {
		const object: Record<string, unknown> = {};
		for (const item of splitTopLevel(value.slice(1, -1), ",")) {
			const equals = findTopLevel(item, "=");
			if (equals < 1) continue;
			object[unquoteKey(item.slice(0, equals).trim())] = parseTomlValue(item.slice(equals + 1).trim());
		}
		return object;
	}
	if (value === "true" || value === "false") return value === "true";
	if (/^-?\d+(?:\.\d+)?$/u.test(value)) return Number(value);
	if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
		if (value.startsWith('"')) {
			try { return JSON.parse(value) as string; } catch { return value.slice(1, -1); }
		}
		return value.slice(1, -1).replaceAll("''", "'");
	}
	return value;
}

function ensureRecordPath(root: Record<string, unknown>, path: readonly string[]): Record<string, unknown> {
	let current = root;
	for (const segment of path) {
		const next = record(current[segment]);
		if (next === undefined) current[segment] = {};
		current = (current[segment] as Record<string, unknown>);
	}
	return current;
}

function splitTomlPath(value: string): string[] {
	return splitTopLevel(value, ".").map((segment) => unquoteKey(segment.trim()));
}

function splitTopLevel(value: string, separator: string): string[] {
	const result: string[] = [];
	let start = 0;
	let depth = 0;
	let quoteChar = "";
	for (let index = 0; index < value.length; index += 1) {
		const char = value[index];
		if (quoteChar !== "") {
			if (char === quoteChar && value[index - 1] !== "\\") quoteChar = "";
			continue;
		}
		if (char === '"' || char === "'") { quoteChar = char; continue; }
		if (char === "[" || char === "{") depth += 1;
		if (char === "]" || char === "}") depth -= 1;
		if (depth === 0 && value.startsWith(separator, index)) {
			result.push(value.slice(start, index).trim());
			start = index + separator.length;
			index += separator.length - 1;
		}
	}
	result.push(value.slice(start).trim());
	return result;
}

function findTopLevel(value: string, needle: string): number {
	let depth = 0;
	let quoteChar = "";
	for (let index = 0; index < value.length; index += 1) {
		const char = value[index];
		if (quoteChar !== "") {
			if (char === quoteChar && value[index - 1] !== "\\") quoteChar = "";
			continue;
		}
		if (char === '"' || char === "'") { quoteChar = char; continue; }
		if (char === "[" || char === "{") depth += 1;
		if (char === "]" || char === "}") depth -= 1;
		if (depth === 0 && value.startsWith(needle, index)) return index;
	}
	return -1;
}

function stripTomlComment(value: string): string {
	let quoteChar = "";
	for (let index = 0; index < value.length; index += 1) {
		const char = value[index];
		if (quoteChar !== "") {
			if (char === quoteChar && value[index - 1] !== "\\") quoteChar = "";
			continue;
		}
		if (char === '"' || char === "'") quoteChar = char;
		if (char === "#") return value.slice(0, index);
	}
	return value;
}

function unquoteKey(value: string): string {
	if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) return value.slice(1, -1);
	return value;
}

function tomlKey(value: string): string {
	return /^[A-Za-z0-9_-]+$/u.test(value) ? value : quote(value);
}

function inlineTable(value: Readonly<Record<string, string>>): string {
	return `{ ${Object.entries(value).map(([key, item]) => `${tomlKey(key)} = ${quote(item)}`).join(", ")} }`;
}

function inlineJsonTable(value: Readonly<Record<string, JsonValue>>): string {
	return `{ ${Object.entries(value).map(([key, item]) => `${tomlKey(key)} = ${tomlValue(item)}`).join(", ")} }`;
}

function tomlValue(value: JsonValue): string {
	if (typeof value === "string") return quote(value);
	if (typeof value === "number" || typeof value === "boolean") return String(value);
	if (value === null) return quote("null");
	if (Array.isArray(value)) return `[${value.map((item) => tomlValue(item)).join(", ")}]`;
	return inlineJsonTable(value);
}

function quote(value: string): string {
	return JSON.stringify(value);
}

function record(value: unknown): Record<string, unknown> | undefined {
	return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function stringRecord(value: Record<string, unknown>): Readonly<Record<string, string>> {
	const output: Record<string, string> = {};
	for (const [key, item] of Object.entries(value)) {
		if (typeof item !== "string") throw new Error(`Request header ${key} must be a string`);
		output[key] = item;
	}
	return output;
}

function jsonRecord(value: Record<string, unknown>): Readonly<Record<string, JsonValue>> {
	const output: Record<string, JsonValue> = {};
	for (const [key, item] of Object.entries(value)) {
		if (!isJsonValue(item)) throw new Error(`Request parameter ${key} must be JSON-compatible`);
		output[key] = item;
	}
	return output;
}

function isJsonValue(value: unknown): value is JsonValue {
	if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return true;
	if (Array.isArray(value)) return value.every(isJsonValue);
	const object = record(value);
	return object !== undefined && Object.values(object).every(isJsonValue);
}

function stringValue(value: unknown, fallback: string): string {
	return typeof value === "string" && value.length > 0 ? value : fallback;
}

function controlledExecutionBoundary(value: unknown): "CONTROLLED_COMMAND_RUNNER" {
	if (value === undefined || value === "CONTROLLED_COMMAND_RUNNER") return "CONTROLLED_COMMAND_RUNNER";
	throw new Error("tools.execution_boundary must be CONTROLLED_COMMAND_RUNNER; this runtime does not claim a hardened sandbox");
}

function configuredImportAuthority(value: unknown): string {
	if (value === undefined) return DEFAULT_CONFIG.corpus.importAuthorityPolicy;
	const allowed = ["VERIFIED_CURRENT", "VERIFIED_IMPORTED", "PROVISIONAL_IMPORTED", "UNVERIFIED_NOTE"];
	if (typeof value === "string" && allowed.includes(value)) return value;
	throw new Error(`corpus.import_authority_policy must be one of ${allowed.join(", ")}`);
}

function configuredExecutableNames(value: unknown): readonly string[] {
	if (value === undefined) return DEFAULT_CONFIG.tools.allowedExecutables;
	if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) throw new Error("tools.allowed_executables must be an array of executable names");
	const allowed = ["node", "python", "lean", "lake"], invalid = value.filter((item) => !allowed.includes(item));
	if (invalid.length > 0) throw new Error(`Unsupported controlled executable(s): ${invalid.join(", ")}`);
	return [...new Set(value)];
}

function configuredCapabilityNames(value: unknown): readonly string[] {
	if (value === undefined) return DEFAULT_CONFIG.tools.allowedCapabilities;
	if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) throw new Error("tools.allowed_capabilities must be an array of capability names");
	const allowed = ["artifact_search", "artifact_read", "artifact_metadata", "corpus_read", "corpus_search", "scratch_read", "scratch_write", "controlled_computation"], invalid = value.filter((item) => !allowed.includes(item));
	if (invalid.length > 0) throw new Error(`Unsupported research capability/capabilities: ${invalid.join(", ")}`);
	return [...new Set(value)];
}

function stringValueOrUndefined(value: unknown): string | undefined {
	return typeof value === "string" && value.length > 0 ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
	return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function positiveInteger(value: unknown, fallback: number): number {
	return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : fallback;
}

function boundedInteger(value: unknown, fallback: number): number {
	return Math.min(100_000, Math.max(0, positiveInteger(value, fallback)));
}

function booleanValue(value: unknown, fallback: boolean): boolean {
	return typeof value === "boolean" ? value : fallback;
}

function revisionOf(text: string): string {
	return createHash("sha256").update(text).digest("hex").slice(0, 16);
}

function isMissingFile(error: unknown): boolean {
	return (error as NodeJS.ErrnoException | undefined)?.code === "ENOENT";
}

function isAlreadyExists(error: unknown): boolean {
	return (error as NodeJS.ErrnoException | undefined)?.code === "EEXIST";
}

export { createProvider };
