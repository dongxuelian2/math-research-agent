import type { Agent } from "../agent/types.js";
import { assertContributionInvariants } from "../research/invariants.js";
import type { AgentMessage, AgentRunResult, AssistantMessage } from "../models/index.js";
import { isJsonObject, type JsonObject } from "../models/json.js";
import type {
	ProofAction,
	ProofAgentFactory,
	ProofCandidate,
	ProofPlan,
	ProofPlanner,
	ProofPlannerContext,
	ProofPlannerTrace,
	ProofResearchContext,
	ProofResearcher,
	ProofTaskInput,
	ProofVerifier,
	ProofVerifierContext,
	ResearchResult,
	VerificationResult,
} from "./types.js";
import type { ProofPlannerWithTrace } from "./types.js";

export interface AgentProofRoles {
	readonly planner: ProofPlanner;
	readonly researcher: ProofResearcher;
	readonly verifier: ProofVerifier;
	readonly agentFactory?: ProofAgentFactory;
}

/** A model/provider failure is different from a malformed planner response. */
export class ProofProviderError extends Error {
	readonly kind = "provider" as const;

	constructor(message: string, readonly retryable = false) {
		super(message);
		this.name = "ProofProviderError";
	}
}

export class ProofProtocolError extends Error {
	readonly kind = "protocol" as const;

	constructor(message: string) {
		super(message);
		this.name = "ProofProtocolError";
	}
}

export function createAgentProofRoles(options: {
	readonly planner: Agent;
	readonly researcher: Agent;
	readonly verifier: Agent;
	readonly agentFactory?: ProofAgentFactory;
}): AgentProofRoles {
	return {
		planner: new AgentProofPlanner(options.planner),
		researcher: new AgentProofResearcher(options.researcher),
		verifier: new AgentProofVerifier(options.verifier),
		...(options.agentFactory === undefined ? {} : { agentFactory: options.agentFactory }),
	};
}

class AgentProofPlanner implements ProofPlanner, ProofPlannerWithTrace {
	lastTrace: ProofPlannerTrace | undefined;

	constructor(private readonly agent: Agent) {}

	async plan(context: ProofPlannerContext, signal?: AbortSignal): Promise<ProofPlan> {
		throwIfAborted(signal);
		const prompt = formatPlannerPrompt(context);
		const system = plannerSystemPrompt(context.mode ?? "prove", context.workflowMode);
		let lastText = "";
		let lastError = "";
		for (let attempt = 1; attempt <= 3; attempt += 1) {
			throwIfAborted(signal);
			const request = attempt === 1
				? prompt
				: formatPlannerRetry(prompt, lastText, lastError, attempt);
			const result = await promptWithProviderRetry(this.agent, `${system}\n\n${request}`, signal, "planner");
			throwIfAborted(signal);
			lastText = assistantText(result).trim();
			this.lastTrace = { prompt: request, response: lastText, attempts: attempt };
			try {
				const plan = parsePlannerPlan(lastText);
				this.lastTrace = { prompt: request, response: lastText, attempts: attempt };
				return plan;
			} catch (error) {
				lastError = errorMessage(error);
				this.lastTrace = {
					prompt: request,
					response: lastText,
					attempts: attempt,
					parseError: lastError,
				};
			}
		}
		throw new ProofProtocolError(`Planner response could not be parsed after 3 attempts: ${lastError}`);
	}
}

class AgentProofResearcher implements ProofResearcher {
	constructor(private readonly agent: Agent) {}

	async research(context: ProofResearchContext, signal?: AbortSignal): Promise<ResearchResult> {
		throwIfAborted(signal);
		const result = await promptWithProviderRetry(this.agent, [
				context.task.kind === "FORMALIZATION"
					? "You are the Lean formalizer in a dynamic proof workflow. Use the pinned upstream Lean 4 skill and produce or repair the exact full Lean 4 source requested by the focused task. The session's lake env lean process, not your explanation, is authoritative."
					: "You are one independent mathematical worker in a planner/worker proof workflow.",
				"Solve only the focused task below. Do not spawn other agents. Use the available corpus/search/computation/scratch tools when evidence or computation is needed.",
				"Search results are discovery only. Read every proof-critical artifact body; the runtime records exact evidence from the tool calls.",
				"Return only the generated result body as plain text. Do not return JSON, YAML, TOML, metadata envelopes, or a JSON/Markdown wrapper around the result. The runtime constructs the result envelope, candidate id, task scope, strategy, and evidence from this task and the tool trace.",
				context.task.kind === "FORMALIZATION"
					? "Return only the entire Lean source with no Markdown fences or explanatory text. If an exact configured Lean declaration is supplied, preserve it and replace only its sorry hole; otherwise translate the original mathematical theorem into a precise Lean declaration yourself. Never weaken or trivialize the theorem, and never add axiom/constant/opaque/sorry/admit escape hatches. Compiler feedback is authoritative."
					: context.task.contributionKind === "STRUCTURAL_OBSERVATION"
						? "Report only the bounded structural observation in plain text; do not claim that an observation proves the whole target."
						: "Use clean Markdown with LaTeX for mathematics. A candidate must be a complete, self-contained proof or a clearly delimited sub-proof; do not hide a gap behind a slogan.",
				context.task.scope === "TARGET" ? "This task is TARGET-scoped: generate a complete proof of the exact theorem." : "This task is CONTRIBUTION-scoped: do not claim that a lemma or local observation proves the whole target.",
				context.task.agent === undefined ? "Logical agent: the default mathematical worker." : `Logical agent: ${context.task.agent.agentId}\nPurpose: ${context.task.agent.purpose}${context.task.agent.capabilities === undefined ? "" : `\nCapabilities: ${context.task.agent.capabilities.join(", ")}`}`,
				context.task.successCriteria === undefined ? "" : `Success criteria:\n${context.task.successCriteria}`,
					context.task.continuationOf === undefined ? "" : `This is a continuation of partial task ${context.task.continuationOf}. Preserve valid work and change the smallest region that addresses the latest compiler feedback; do not restart without explaining why.`,
					context.task.kind === "FORMALIZATION" && context.leanProject !== undefined
						? `Use exactly this session Lean project: ${context.leanProject.projectDirectory}\nToolchain: ${context.leanProject.toolchain}\nPackages: ${context.leanProject.packages.join(", ") || "none"}\nImports validated at setup: ${context.leanProject.imports.join(", ") || "none"}`
						: "",
				`Theorem:\n${context.obligation.theorem}`,
				context.obligation.context === undefined ? "" : `Theorem context:\n${context.obligation.context}`,
				`Focused task:\n${context.task.description}`,
				context.referencedMaterials.length === 0 ? "Referenced repository materials: none" : context.referencedMaterials,
			].filter((part) => part.length > 0).join("\n\n"), signal, "researcher");
		throwIfAborted(signal);
		const text = assistantText(result).trim();
		const assistantStopReason = lastAssistantStopReason(result);
		if (assistantStopReason === "length" || result.stopReason === "max_turns") {
			return {
				kind: "partial",
				content: text,
				reason: assistantStopReason === "length"
					? "Worker output reached the model output limit before a complete result was returned."
					: "Worker reached its turn budget before a complete result was returned.",
				suggestedNext: `Create a continuation task for ${context.task.taskId} and preserve this partial output.`,
			};
		}
		return parseResearchResult(text, context);
	}
}

class AgentProofVerifier implements ProofVerifier {
	constructor(private readonly agent: Agent) {}

	async verify(
		candidate: ProofCandidate,
		context: ProofVerifierContext,
		signal?: AbortSignal,
	): Promise<VerificationResult> {
		throwIfAborted(signal);
		const result = await promptWithProviderRetry(this.agent, [
				"You are an independent verifier. You see the original task and the candidate output, not the researcher's hidden reasoning.",
				"The runtime has already parsed the worker's response envelope. Treat candidate.content below as the proof body; do not reject a mathematically complete candidate merely because the worker did not include JSON/TOML wrapper syntax.",
				"Check the mathematical argument, every non-trivial inference, edge cases, and whether the requested task was actually completed.",
				"Do not repair the candidate silently. Explain the first decisive gap and end with exactly one verdict marker:",
				"VERDICT: CORRECT | VERDICT: CRITICALLY FLAWED - reason | VERDICT: NEEDS MINOR FIXES - reason | VERDICT: UNFINISHED - reason",
				`Original theorem:\n${context.obligation.theorem}`,
				`Original task:\n${context.task.description}`,
				`Candidate proof:\n${candidate.content}`,
				candidate.evidence.length === 0 ? "Candidate evidence receipts: none" : `Candidate evidence receipts (retrieve/check exact ids and hashes):\n${JSON.stringify(candidate.evidence, null, 2)}`,
				context.referencedMaterials === undefined || context.referencedMaterials.length === 0 ? "" : `Exact referenced repository bodies:\n${context.referencedMaterials}`,
		].join("\n\n"), signal, "verifier");
		throwIfAborted(signal);
		if (lastAssistantStopReason(result) === "length") {
			return { verdict: "UNFINISHED", feedback: "Verifier output reached the model output limit before a verdict was returned." };
		}
		return parseVerification(assistantText(result));
	}
}

export function parsePlannerPlan(text: string): ProofPlan {
	const parsed = parseJsonObject(text);
	if (parsed !== undefined && Array.isArray(parsed.actions)) {
		const workflow = parsed.workflow === undefined ? undefined : parseWorkflowSpec(parsed.workflow);
		return {
			actions: parsed.actions.map((action) => parseAction(action)),
			...(typeof parsed.summary === "string" ? { summary: parsed.summary } : {}),
			...(workflow === undefined ? {} : { workflow }),
		};
	}
	if (parsed !== undefined && typeof parsed.action === "string") {
		const workflow = parsed.workflow === undefined ? undefined : parseWorkflowSpec(parsed.workflow);
		return {
			actions: [parseAction(parsed)],
			...(typeof parsed.summary === "string" ? { summary: parsed.summary } : {}),
			...(workflow === undefined ? {} : { workflow }),
		};
	}

	const blocks = extractTomlBlocks(text);
	if (blocks.length === 0) {
		throw new ProofProtocolError("Planner response must contain JSON actions or <OPENPROVER_ACTION> TOML blocks");
	}
	const actions = blocks.map(parseTomlAction);
	return { actions };
}

export function parseVerification(text: string): VerificationResult {
	const parsed = parseJsonObject(text);
	if (parsed !== undefined && isVerdict(parsed.verdict)) {
		return {
			verdict: parsed.verdict,
			feedback: typeof parsed.feedback === "string" ? parsed.feedback : text,
			...(Array.isArray(parsed.checks) ? { checks: parsed.checks.filter((item): item is string => typeof item === "string") } : {}),
		};
	}
	const marker = text.match(/VERDICT\s*:\s*(CRITICALLY\s+FLAWED|NEEDS\s+MINOR\s+FIXES|UNFINISHED|CORRECT|INCORRECT|INCONCLUSIVE)/i)?.[1];
	if (marker !== undefined) {
		const verdict = normalizeVerdict(marker);
		return { verdict, feedback: text };
	}
	return { verdict: "INCONCLUSIVE", feedback: text || "Verifier returned no verdict" };
}

function parseResearchResult(text: string, context: ProofResearchContext): ResearchResult {
	// The current worker contract is plain text. Keep the old envelope parser as
	// a compatibility bridge for persisted tests/runs and older model adapters,
	// but an invalid or unexpected JSON-looking response is still just worker
	// text and must not be mislabeled as a partial result.
	const legacy = parseLegacyResearchResult(text, context.task.taskId);
	if (legacy !== undefined) return legacy;
	return buildRuntimeResearchResult(text, context);
}

function parseLegacyResearchResult(text: string, taskId: string): ResearchResult | undefined {
	const parsed = parseJsonObject(text);
	if (parsed === undefined) return undefined;
	if (parsed !== undefined && parsed.kind === "blocked" && typeof parsed.reason === "string") {
		return { kind: "blocked", reason: parsed.reason };
	}
	if (parsed !== undefined && parsed.kind === "observation" && typeof parsed.content === "string") {
		return {
			kind: "observation",
			content: parsed.content,
			...(typeof parsed.suggestedNext === "string" ? { suggestedNext: parsed.suggestedNext } : {}),
		};
	}
	// The dedicated formalizer role historically returns { lean, notes }.
	// Accept that shape when the same role is dispatched as a dynamic worker.
	if (parsed !== undefined && typeof parsed.lean === "string") {
		return {
			kind: "candidate",
			candidate: { taskId, content: parsed.lean, strategy: "lean-formalization" },
		};
	}
	if (parsed !== undefined && parsed.kind === "candidate" && isJsonObject(parsed.candidate)) {
		const candidate = parsed.candidate;
		if (typeof candidate.content !== "string") {
			throw new ProofProtocolError("Research candidate requires candidate.content");
		}
		return {
			kind: "candidate",
			candidate: {
				taskId,
				content: candidate.content,
				strategy: typeof candidate.strategy === "string" ? candidate.strategy : "agent-reasoning",
				...(typeof candidate.candidateId === "string" ? { candidateId: candidate.candidateId } : {}),
				...(typeof candidate.claim === "string" ? { claim: candidate.claim } : {}),
				...(typeof candidate.claimFingerprint === "string" ? { claimFingerprint: candidate.claimFingerprint } : {}),
				...(Array.isArray(candidate.evidence) ? { evidence: candidate.evidence.filter((item): item is { artifactId: string; contentHash: string; ranges?: string[] } => isRecord(item) && typeof item.artifactId === "string" && typeof item.contentHash === "string").map((item) => ({ artifactId: item.artifactId, contentHash: item.contentHash, ...(Array.isArray(item.ranges) ? { ranges: item.ranges.filter((range): range is string => typeof range === "string") } : {}) })) } : {}),
				...(Array.isArray(candidate.reliedOnArtifactIds) ? { reliedOnArtifactIds: candidate.reliedOnArtifactIds.filter((item): item is string => typeof item === "string") } : {}),
				...(candidate.scope === "TARGET" || candidate.scope === "CONTRIBUTION" ? { scope: candidate.scope } : {}),
				...(Array.isArray(candidate.assumptions) ? { assumptions: candidate.assumptions.filter((item): item is string => typeof item === "string") } : {}),
				...(Array.isArray(candidate.dependencyClaims) ? { dependencyClaims: candidate.dependencyClaims.filter((item): item is string => typeof item === "string") } : {}),
				...(isRecord(candidate.contribution) ? optionalContribution(candidate.contribution) : {}),
			},
		};
	}
	return undefined;
}

function buildRuntimeResearchResult(text: string, context: ProofResearchContext): ResearchResult {
	if (text.length === 0) {
		return { kind: "blocked", reason: "Researcher returned an empty response" };
	}
	if (context.task.contributionKind === "STRUCTURAL_OBSERVATION") {
		return { kind: "observation", content: text };
	}
	return {
		kind: "candidate",
		candidate: {
			taskId: context.task.taskId,
			content: text,
			strategy: context.task.kind === "FORMALIZATION" ? "lean-formalization" : context.task.agent?.agentId ?? "agent-reasoning",
			scope: context.task.scope,
		},
	};
}

function parseAction(value: unknown): ProofAction {
	if (!isRecord(value) || typeof value.action !== "string") {
		throw new ProofProtocolError("Planner action must be an object with an action field");
	}
	const summary = typeof value.summary === "string" ? { summary: value.summary } : {};
	switch (value.action) {
		case "read_theorem":
			return { action: "read_theorem", ...summary };
		case "read_items": {
			const slugs = value.slugs ?? value.read;
			if (!Array.isArray(slugs) || !slugs.every((slug) => typeof slug === "string")) {
				throw new ProofProtocolError("read_items requires slugs");
			}
			return { action: "read_items", slugs, ...summary };
		}
		case "write_items": {
			if (!Array.isArray(value.items)) {
				throw new ProofProtocolError("write_items requires items");
			}
			return { action: "write_items", items: value.items.map(parseItem), ...summary };
		}
		case "write_whiteboard": {
			const content = value.content ?? value.whiteboard;
			if (typeof content !== "string") {
				throw new ProofProtocolError("write_whiteboard requires content/whiteboard");
			}
			return { action: "write_whiteboard", content, ...summary };
		}
		case "spawn": {
			const taskValues = value.tasks ?? (value.task === undefined ? undefined : [value.task]);
			if (!Array.isArray(taskValues)) {
				throw new ProofProtocolError("spawn requires tasks");
			}
			return { action: "spawn", tasks: taskValues.map(parseTask), ...summary };
		}
		case "literature_search": {
			const query = value.query ?? value.search_query;
			if (typeof query !== "string") {
				throw new ProofProtocolError("literature_search requires query/search_query");
			}
			return {
				action: "literature_search",
				query,
				...(typeof value.context === "string" ? { context: value.context } : typeof value.search_context === "string" ? { context: value.search_context } : {}),
				...summary,
			};
		}
		case "use_tool": {
			const toolName = value.toolName ?? value.tool_name;
			const input = typeof value.input === "string" ? parseJsonObject(value.input) : value.input;
			if (typeof toolName !== "string" || !isJsonObject(input)) {
				throw new ProofProtocolError("use_tool requires toolName and a JSON input object");
			}
			return { action: "use_tool", toolName, input, ...summary };
		}
		case "submit_proof": {
			const candidateId = value.candidateId ?? value.candidate_id;
			const proofSlug = value.proofSlug ?? value.proof_slug;
			if (typeof candidateId !== "string" && typeof proofSlug !== "string") {
				throw new ProofProtocolError("submit_proof requires candidateId or proof_slug");
			}
			return {
				action: "submit_proof",
				...(typeof candidateId === "string" ? { candidateId } : {}),
				...(typeof proofSlug === "string" ? { proofSlug } : {}),
				...summary,
			};
		}
		case "submit_target_proof": {
			const candidateId = value.candidateId ?? value.candidate_id;
			const targetObligationId = value.targetObligationId ?? value.target_obligation_id;
			const targetClaimId = value.targetClaimId ?? value.target_claim_id;
			if (typeof candidateId !== "string" || typeof targetObligationId !== "string" || typeof targetClaimId !== "string") throw new ProofProtocolError("submit_target_proof requires candidateId, targetObligationId, and targetClaimId");
			return { action: "submit_target_proof", candidateId, targetObligationId, targetClaimId, ...summary };
		}
		case "submit_lean_proof": {
			const proofSlug = value.proofSlug ?? value.leanProofSlug ?? value.lean_proof_slug;
			if (typeof proofSlug !== "string") {
				throw new ProofProtocolError("submit_lean_proof requires lean_proof_slug");
			}
			return { action: "submit_lean_proof", proofSlug, leanProofSlug: proofSlug, ...summary };
		}
		case "stop":
			return { action: "stop", ...(typeof value.reason === "string" ? { reason: value.reason } : {}), ...summary };
		default:
			throw new ProofProtocolError(`Unsupported planner action: ${value.action}`);
	}
}

function parseTask(value: unknown): ProofTaskInput {
	if (typeof value === "string") return { summary: summarizeTask(value), description: value };
	if (!isRecord(value)) throw new ProofProtocolError("spawn tasks require a string or object");
	const rawDescription = value.description ?? value.task ?? value.prompt ?? value.instruction;
	const description = typeof rawDescription === "string" ? rawDescription : undefined;
	const summary = typeof value.summary === "string" ? value.summary : description === undefined ? undefined : summarizeTask(description);
	if (summary === undefined || description === undefined) throw new ProofProtocolError("spawn tasks require summary and description (or a task/instruction string)");
	const taskId = typeof value.taskId === "string" ? value.taskId : typeof value.task_id === "string" ? value.task_id : typeof value.id === "string" ? value.id : undefined;
	const contributionKind = value.contributionKind ?? value.contribution_kind;
	const dependsOn = value.dependsOn ?? value.depends_on;
	if (dependsOn !== undefined && (!Array.isArray(dependsOn) || !dependsOn.every((item) => typeof item === "string"))) {
		throw new ProofProtocolError("spawn task dependsOn must be an array of task ids");
	}
	const continuationOf = value.continuationOf ?? value.continuation_of;
	if (continuationOf !== undefined && typeof continuationOf !== "string") throw new ProofProtocolError("spawn task continuationOf must be a task id");
	const agent = parseAgentSpec(value.agent, taskId, summary);
	const successCriteria = value.successCriteria ?? value.success_criteria;
	const rawKind = value.kind ?? value.taskKind ?? value.task_kind;
	const kind = rawKind === "FORMALIZATION" || rawKind === "formalization" || rawKind === "formal" ? "FORMALIZATION" as const : "MATHEMATICAL" as const;
	if (successCriteria !== undefined && typeof successCriteria !== "string") throw new ProofProtocolError("spawn task successCriteria must be a string");
	return {
		...(taskId === undefined ? {} : { taskId }),
		summary,
		description,
		...(typeof value.routeKey === "string" ? { routeKey: value.routeKey } : {}),
		...(scopeOf(value.scope) === undefined ? {} : { scope: scopeOf(value.scope) }),
		...(typeof value.targetClaimId === "string" ? { targetClaimId: value.targetClaimId } : typeof value.target_claim_id === "string" ? { targetClaimId: value.target_claim_id } : {}),
		...(normalizeContributionKind(contributionKind) === undefined ? {} : { contributionKind: normalizeContributionKind(contributionKind) }),
		...(dependsOn === undefined ? {} : { dependsOn }),
		...(agent === undefined ? {} : { agent }),
		...(typeof successCriteria === "string" ? { successCriteria } : {}),
		...(typeof continuationOf === "string" ? { continuationOf } : {}),
		kind,
	};
}

function summarizeTask(description: string): string {
	const normalized = description.replace(/\s+/g, " ").trim();
	return normalized.length <= 120 ? normalized : `${normalized.slice(0, 117)}...`;
}

export function createAgentProofResearcher(researcher: Agent): ProofResearcher { return new AgentProofResearcher(researcher); }

export function createAgentProofVerifier(verifier: Agent): ProofVerifier { return new AgentProofVerifier(verifier); }

function parseAgentSpec(value: unknown, fallbackId: string | undefined, fallbackPurpose: string): import("./types.js").ProofAgentSpec | undefined {
	if (value === undefined) return undefined;
	if (typeof value === "string") return { agentId: fallbackId ?? "dynamic-worker", purpose: value };
	if (!isRecord(value)) throw new ProofProtocolError("spawn task agent must be a string or object");
	const agentId = value.agentId ?? value.agent_id ?? value.id ?? fallbackId ?? "dynamic-worker";
	const purpose = value.purpose ?? value.reason ?? fallbackPurpose;
	if (typeof agentId !== "string" || agentId.trim().length === 0 || typeof purpose !== "string" || purpose.trim().length === 0) throw new ProofProtocolError("spawn task agent requires agentId and purpose");
	const capabilities = value.capabilities;
	if (capabilities !== undefined && (!Array.isArray(capabilities) || !capabilities.every((item) => typeof item === "string"))) throw new ProofProtocolError("spawn task agent capabilities must be an array of strings");
	return {
		agentId: agentId.trim(),
		purpose: purpose.trim(),
		...(capabilities === undefined ? {} : { capabilities }),
		...(value.role === "formalizer" ? { role: "formalizer" as const } : value.role === "worker" ? { role: "worker" as const } : {}),
	};
}

function parseWorkflowSpec(value: unknown): import("./types.js").ProofWorkflowSpec {
	if (!isRecord(value) || typeof value.strategy !== "string" || value.strategy.trim().length === 0) throw new ProofProtocolError("workflow requires a non-empty strategy");
	const successCriteria = value.successCriteria ?? value.success_criteria;
	if (successCriteria !== undefined && (!Array.isArray(successCriteria) || !successCriteria.every((item) => typeof item === "string"))) throw new ProofProtocolError("workflow successCriteria must be an array of strings");
	return {
		strategy: value.strategy.trim(),
		...(typeof value.rationale === "string" ? { rationale: value.rationale } : {}),
		...(successCriteria === undefined ? {} : { successCriteria }),
	};
}

function parseContribution(value: Record<string, unknown>): import("./types.js").ProofContributionDraft {
	const kind = normalizeContributionKind(value.kind);
	if (kind === undefined || typeof value.statement !== "string" || typeof value.relationshipToTarget !== "string") throw new ProofProtocolError("candidate.contribution requires kind, statement, and relationshipToTarget");
	const children = Array.isArray(value.childClaims) ? value.childClaims.filter((item): item is Record<string, unknown> => isRecord(item) && typeof item.claimId === "string" && typeof item.statement === "string").map((item) => ({ claimId: item.claimId as string, statement: item.statement as string })) : undefined;
	const draft: import("./types.js").ProofContributionDraft = { kind, statement: value.statement, relationshipToTarget: value.relationshipToTarget, ...(typeof value.claimId === "string" ? { claimId: value.claimId } : {}), assumptions: Array.isArray(value.assumptions) ? value.assumptions.filter((item): item is string => typeof item === "string") : [], dependencyClaims: Array.isArray(value.dependencyClaims) ? value.dependencyClaims.filter((item): item is string => typeof item === "string") : [], ...(children === undefined ? {} : { childClaims: children }), ...(typeof value.coverageScope === "string" ? { coverageScope: value.coverageScope } : {}), ...(typeof value.coverageAssertion === "string" ? { coverageAssertion: value.coverageAssertion } : {}), ...(typeof value.closedCaseClaimId === "string" ? { closedCaseClaimId: value.closedCaseClaimId } : {}), ...(typeof value.closureReason === "string" ? { closureReason: value.closureReason } : {}), ...(typeof value.targetScope === "string" ? { targetScope: value.targetScope } : {}), ...(typeof value.counterexampleScope === "string" ? { counterexampleScope: value.counterexampleScope } : {}) };
	try { assertContributionInvariants({ ...draft, assumptions: draft.assumptions ?? [], dependencyClaims: draft.dependencyClaims ?? [] }); } catch (error) { throw new ProofProtocolError(String(error)); }
	return draft;
}

function optionalContribution(value: Record<string, unknown>): { readonly contribution?: import("./types.js").ProofContributionDraft } {
	try {
		return { contribution: parseContribution(value) };
	} catch {
		// Contribution metadata is optional decoration on a candidate. Preserve a
		// complete target proof while refusing to promote an unknown/malformed
		// contribution into the research graph.
		return {};
	}
}

function normalizeContributionKind(value: unknown): import("./types.js").ProofContributionKind | undefined {
	if (typeof value !== "string") return undefined;
	const normalized = value.trim().toUpperCase().replace(/[\s-]+/gu, "_");
	return normalized === "LEMMA" || normalized === "REDUCTION" || normalized === "CASE_SPLIT" || normalized === "CASE_CLOSURE" || normalized === "COUNTEREXAMPLE" || normalized === "CONSTRUCTION" || normalized === "BOUND" || normalized === "OBSTRUCTION" || normalized === "STRUCTURAL_OBSERVATION" || normalized === "LITERATURE_APPLICATION"
		? normalized
		: undefined;
}

function scopeOf(value: unknown): "TARGET" | "CONTRIBUTION" | undefined {
	if (typeof value !== "string") return undefined;
	const normalized = value.trim().toUpperCase();
	return normalized === "TARGET" || normalized === "CONTRIBUTION" ? normalized : undefined;
}

function parseItem(value: unknown): {
	readonly slug: string;
	readonly content?: string;
	readonly summary?: string;
	readonly format?: "text" | "lean";
} {
	if (!isRecord(value) || typeof value.slug !== "string") {
		throw new ProofProtocolError("write_items entries require slug");
	}
	if (value.content !== undefined && typeof value.content !== "string") {
		throw new ProofProtocolError("write_items content must be a string when provided");
	}
	if (value.format !== undefined && value.format !== "text" && value.format !== "lean") {
		throw new ProofProtocolError("write_items format must be text or lean");
	}
	return {
		slug: value.slug,
		...(typeof value.content === "string" ? { content: value.content } : {}),
		...(typeof value.summary === "string" ? { summary: value.summary } : {}),
		...(value.format === "text" || value.format === "lean" ? { format: value.format } : {}),
	};
}

type TomlBlock = Record<string, unknown> & { tasks?: Record<string, unknown>[]; items?: Record<string, unknown>[] };

function extractTomlBlocks(text: string): TomlBlock[] {
	const tagged = [...text.matchAll(/<OPENPROVER_ACTION>\s*([\s\S]*?)\s*<\/OPENPROVER_ACTION>/gi)].map((match) => match[1] ?? "");
	if (tagged.length > 0) {
		return tagged.map(parseTomlBlock);
	}
	const fenced = [...text.matchAll(/```(?:toml)?\s*([\s\S]*?)```/gi)].map((match) => match[1] ?? "").filter((block) => /(^|\n)\s*action\s*=/.test(block));
	if (fenced.length > 0) {
		return fenced.map(parseTomlBlock);
	}
	if (/(^|\n)\s*action\s*=/.test(text)) {
		return [parseTomlBlock(text.slice(text.lastIndexOf("action =")))];
	}
	return [];
}

function parseTomlBlock(text: string): TomlBlock {
	const root: TomlBlock = {};
	let current: Record<string, unknown> = root;
	let currentKind: "root" | "tasks" | "items" = "root";
	const lines = text.replace(/\r\n/g, "\n").split("\n");
	for (let index = 0; index < lines.length; index += 1) {
		const rawLine = lines[index] ?? "";
		const line = rawLine.trim();
		if (line.length === 0 || line.startsWith("#")) continue;
		const table = line.match(/^\[\[(tasks|items)\]\]$/);
		if (table !== null) {
			currentKind = table[1] as "tasks" | "items";
			const array = (root[currentKind] as Record<string, unknown>[] | undefined) ?? [];
			const entry: Record<string, unknown> = {};
			array.push(entry);
			root[currentKind] = array;
			current = entry;
			continue;
		}
		const assignment = line.match(/^([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(.*)$/);
		if (assignment === null) continue;
		const key = assignment[1] ?? "";
		let valueText = assignment[2] ?? "";
		if (valueText.startsWith('"""')) {
			const first = valueText.slice(3);
			const pieces: string[] = [];
			const sameLineEnd = first.indexOf('"""');
			if (sameLineEnd >= 0) {
				pieces.push(first.slice(0, sameLineEnd));
			} else {
				pieces.push(first);
				while (index + 1 < lines.length) {
					index += 1;
					const next = lines[index] ?? "";
					const end = next.indexOf('"""');
					if (end >= 0) {
						pieces.push(next.slice(0, end));
						break;
					}
					pieces.push(next);
				}
			}
			valueText = `"${pieces.join("\n").replaceAll('"', '\\"')}"`;
		}
		current[key] = parseTomlValue(valueText);
		void currentKind;
	}
	return root;
}

function parseTomlValue(value: string): unknown {
	const trimmed = value.trim();
	if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
		return [...trimmed.slice(1, -1).matchAll(/"((?:\\.|[^"\\])*)"|'([^']*)'/g)].map((match) => match[1] ?? match[2] ?? "");
	}
	if (trimmed === "true") return true;
	if (trimmed === "false") return false;
	if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) return Number(trimmed);
	if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
		if (trimmed.startsWith('"')) {
			try {
				return JSON.parse(trimmed) as string;
			} catch {
				return trimmed.slice(1, -1);
			}
		}
		return trimmed.slice(1, -1);
	}
	return trimmed;
}

function parseTomlAction(block: TomlBlock): ProofAction {
	return parseAction({
		...block,
		...(Array.isArray(block.tasks) ? { tasks: block.tasks } : {}),
		...(Array.isArray(block.items) ? { items: block.items } : {}),
	});
}

function formatPlannerPrompt(context: ProofPlannerContext): string {
	const repository = context.repositoryIndex ?? context.repository.map((item) => `- [[${item.slug}]]: ${item.summary}`).join("\n");
	const history = context.recentOutputs.map((output) => `### ${output.action} — ${output.summary}\n${output.content}`).join("\n\n");
	const tasks = context.tasks.length === 0
		? "none"
		: context.tasks.map((task) => [
			`- ${task.taskId} [${task.kind}, ${task.status}, attempt=${task.attempt}]${task.dependsOn.length === 0 ? "" : ` dependsOn=${task.dependsOn.join(",")}`}${task.continuationOf === undefined ? "" : ` continuationOf=${task.continuationOf}`} — ${task.summary}`,
			`  task description: ${task.description}`,
			task.successCriteria === undefined ? "" : `  success criteria: ${task.successCriteria}`,
			task.agent === undefined ? "" : `  logical agent: ${task.agent.agentId} (${task.agent.purpose})`,
			task.lastError === undefined ? "" : `  last error: ${task.lastError}`,
		].filter((part) => part.length > 0).join("\n")).join("\n");
	const failures = context.failedRoutes.length === 0
		? "none"
		: context.failedRoutes.map((failure) => `- ${failure.routeFingerprint.slice(0, 12)}: ${failure.reason}`).join("\n");
	const formalAttempts = context.formalAttempts.length === 0
		? "none"
		: context.formalAttempts.map((attempt) => `- attempt ${attempt.attempt}, step ${attempt.step}: ${attempt.result.ok ? "PASSED" : attempt.result.failureKind ?? "REJECTED"}\n  ${attempt.result.feedback}`).join("\n");
	const unresolved = context.tasks
		.filter((task) => task.status === "PARTIAL" || task.status === "FAILED_RETRYABLE")
		.filter((task) => !context.tasks.some((child) => child.continuationOf === task.taskId))
		.map((task) => `- ${task.taskId} [${task.status}] requires a continuation or an explicitly different route`)
		.join("\n");
	const decomposition = context.decomposition === undefined
		? ""
		: [
			`complexity=${context.decomposition.complexity}; detected units=${context.decomposition.unitCount}; recommended minimum tasks=${context.decomposition.recommendedMinimumTasks}`,
			`signals: ${context.decomposition.signals.join("; ")}`,
			...context.decomposition.units.map((unit) => `- ${unit.unitId}: ${unit.label}`),
		].join("\n");
	const coordination = context.workflowMode === "dynamic"
		? "You are the workflow controller. Decide the decomposition for this round from the theorem, evidence, decomposition signal, and current task graph. For a COMPOSITE obligation, schedule focused work for every detected unit and a final synthesis/closure task; do not compress multiple units into one worker. Put independent units in the same ready frontier and make synthesis dependOn all required unit task ids. Use one worker only when the obligation is SIMPLE or one independently verifiable result genuinely covers the whole obligation in one worker turn. The number of tasks, boundaries, roles, and strategy must come from the problem-derived units, not a fixed theorem-specific template. Use stable task ids, successCriteria, and continuationOf for partial work. The runtime executes the ready frontier and will preserve incomplete results for the next round. A partial or retryable task that you do not explicitly continue will receive a generic runtime continuation automatically. Do not assume a fixed set of agent roles. A worker task must return one complete proof or a clearly delimited contribution; do not use write_items as a substitute for a candidate. A valid spawn action is {\"action\":\"spawn\",\"tasks\":[{\"taskId\":\"stable-id\",\"summary\":\"short label\",\"description\":\"self-contained task\",\"successCriteria\":\"exact completion condition\"}]}; do not use a singular task field."
		: "Delegate all mathematical reasoning to workers. Use one focused task per worker. After a failed route, write why it failed and change routeKey or strategy.";
	const targetGate = targetGateInstruction(context.tacticalDirective);
	return [
		"# WHITEBOARD\n\n" + (context.whiteboard || "(empty — initialize Goal / Plan / Failed / Backlog / Status)"),
		"# THEOREM\n\n" + context.obligation.theorem,
		context.obligation.context === undefined ? "" : "# THEOREM CONTEXT\n\n" + context.obligation.context,
		`# STATUS\n\nmode=${context.mode ?? "prove"}\nworkflowMode=${context.workflowMode}\nstatus=${context.status}\nstep=${context.step}`,
		decomposition.length === 0 ? "" : "# DECOMPOSITION SIGNAL\n\n" + decomposition,
		"# TASK GRAPH\n\n" + tasks,
		unresolved.length === 0 ? "" : "# UNRESOLVED WORK\n\n" + unresolved,
		"# REPOSITORY\n\n" + (repository || "(empty)"),
		"# FAILED ROUTES\n\n" + failures,
		context.mode === "prove" ? "" : "# LEAN PROCESS ATTEMPTS\n\n" + formalAttempts,
		context.tacticalDirective === undefined ? "" : "# TACTICAL DIRECTIVE\n\n" + JSON.stringify(context.tacticalDirective, null, 2),
		targetGate,
		"# RECENT WORKER / VERIFIER OUTPUT\n\n" + (history || "(none)"),
		`# COORDINATION RULES\n\n${coordination} Read referenced repo items before relying on them. Write durable findings before the next step. Submit informal mathematics only after an independent CORRECT verifier result. In formal modes, the runtime creates FORMALIZATION tasks and only the configured Lean process may finish the run; failed Lean attempts must be repaired in a continuation task. When a task is PARTIAL or FAILED_RETRYABLE, create a new task id and set continuationOf to the old task id; preserve the old output instead of silently discarding it. Use these JSON shapes: {\"action\":\"write_items\",\"items\":[{\"slug\":\"notes/name\",\"content\":\"...\",\"format\":\"text\"}]}, {\"action\":\"submit_proof\",\"candidateId\":\"<taskId>-candidate\"}. Return one or more actions in JSON or OpenProver TOML format. In JSON, an optional top-level workflow object may describe strategy, rationale, and successCriteria.`,
	].filter((part) => part.length > 0).join("\n\n---\n\n");
}

function plannerSystemPrompt(mode: string, workflowMode: "dynamic" | "legacy"): string {
	return [
		workflowMode === "dynamic"
			? "You are the WORKFLOW CONTROLLER in a mathematical research system. You own the next-step workflow: choose the number, purpose, and dependencies of logical agents from the theorem and current evidence."
			: "You are the PLANNER in an OpenProver-style proof workflow. Decide what work should happen next; workers do the mathematical reasoning.",
		"Available actions: read_theorem, read_items, write_items, write_whiteboard, spawn, literature_search, use_tool, submit_proof, submit_target_proof, submit_lean_proof, stop.",
		workflowMode === "dynamic"
			? "Keep each task self-contained. For a composite obligation, assign separate logical agents to the problem-derived units and add a final synthesis task with dependsOn edges; do not return one broad task that covers multiple units. A task may name a logical agent with agentId, purpose, and descriptive capabilities; this metadata never grants tools. Use dependsOn only for real prerequisites, and use continuationOf to continue a partial result. Never silently repair a verifier rejection; create a new route and record the failure. The runtime owns mandatory FORMALIZATION tasks and the Lean process gate. The exact spawn shape is {\"action\":\"spawn\",\"tasks\":[{\"taskId\":\"stable-id\",\"summary\":\"short label\",\"description\":\"self-contained task\"}]}; use tasks (plural), not task."
			: "Keep task descriptions self-contained. Workers only receive the theorem, task, and explicitly referenced repository material. Never silently repair a verifier rejection; create a new route and record the failure.",
		mode === "prove" ? "Goal: accept a verified informal proof." : mode === "formalize_only" ? "Goal: accept only a formally checked Lean proof." : "Goal: accept both a verified informal proof and a formally checked Lean proof.",
	].join("\n\n");
}

function targetGateInstruction(directive: Readonly<Record<string, unknown>> | undefined): string {
	const targetObligationId = typeof directive?.targetObligationId === "string" ? directive.targetObligationId : undefined;
	const targetClaimId = typeof directive?.targetClaimId === "string" ? directive.targetClaimId : undefined;
	if (targetObligationId === undefined || targetClaimId === undefined) return "";
	return `# EXACT TARGET GATE\n\nThis run has an exact research-target gate. To prove the target, spawn a task with scope=\"TARGET\" and targetClaimId=\"${targetClaimId}\"; its worker must return the complete target proof. Use scope=\"CONTRIBUTION\" only for a lemma, reduction, case, or observation that does not itself close the target. After a TARGET candidate receives VERDICT: CORRECT, submit it with {\"action\":\"submit_target_proof\",\"candidateId\":\"<taskId>-candidate\",\"targetObligationId\":\"${targetObligationId}\",\"targetClaimId\":\"${targetClaimId}\"}. Do not submit a local lemma as the target.`;
}

function formatPlannerRetry(original: string, raw: string, error: string, attempt: number): string {
	return `${original}\n\nYour previous planner response could not be parsed (attempt ${attempt - 1}): ${error}\n\nPrevious response:\n${raw}\n\nReturn ONLY a valid action object or a complete <OPENPROVER_ACTION> TOML block. Do not add a partial block.`;
}

function assistantText(result: AgentRunResult): string {
	for (let index = result.messages.length - 1; index >= 0; index -= 1) {
		const message = result.messages[index];
		if (message?.role === "assistant") return assistantMessageText(message);
	}
	return "";
}

function lastAssistantStopReason(result: AgentRunResult): AssistantMessage["stopReason"] | undefined {
	for (let index = result.messages.length - 1; index >= 0; index -= 1) {
		const message = result.messages[index];
		if (message?.role === "assistant") return message.stopReason;
	}
	return undefined;
}

function assistantMessageText(message: AssistantMessage): string {
	return message.content
		.filter((part): part is Extract<AssistantMessage["content"][number], { kind: "text" }> => part.kind === "text")
		.map((part) => part.text)
		.join("");
}

function assertAgentResult(result: AgentRunResult, role: string): void {
	if (result.error !== undefined || ["model_error", "session_error"].includes(result.stopReason)) {
		const message = `${role} provider failed: ${result.error?.message ?? result.stopReason}`;
		throw new ProofProviderError(message, isRetryableProviderMessage(message));
	}
}

async function promptWithProviderRetry(agent: Agent, input: string, signal: AbortSignal | undefined, role: string): Promise<AgentRunResult> {
	for (let attempt = 1; attempt <= 3; attempt += 1) {
		throwIfAborted(signal);
		const result = await agent.prompt(input);
		try {
			assertAgentResult(result, role);
			return result;
		} catch (error) {
			if (!(error instanceof ProofProviderError) || !error.retryable || attempt === 3) throw error;
			await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** (attempt - 1)));
		}
	}
	throw new ProofProviderError(`${role} provider retry loop exhausted`, true);
}

function isRetryableProviderMessage(message: string): boolean {
	return /fetch failed|enotfound|eai_again|econnreset|econnrefused|etimedout|timeout|socket|temporarily unavailable|http (?:408|425|429|5\d\d)\b/iu.test(message);
}

function parseJsonObject(text: string): Record<string, unknown> | undefined {
	const trimmed = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
	const start = trimmed.indexOf("{");
	const end = trimmed.lastIndexOf("}");
	if (start < 0 || end <= start) return undefined;
	try {
		const value: unknown = JSON.parse(trimmed.slice(start, end + 1));
		return isRecord(value) ? value : undefined;
	} catch {
		return undefined;
	}
}

function isVerdict(value: unknown): value is VerificationResult["verdict"] {
	return value === "CORRECT" || value === "CRITICALLY_FLAWED" || value === "NEEDS_MINOR_FIXES" || value === "UNFINISHED" || value === "INCORRECT" || value === "INCONCLUSIVE";
}

function normalizeVerdict(value: string): VerificationResult["verdict"] {
	const normalized = value.toUpperCase().replace(/\s+/g, "_");
	if (normalized === "CRITICALLY_FLAWED") return "CRITICALLY_FLAWED";
	if (normalized === "NEEDS_MINOR_FIXES") return "NEEDS_MINOR_FIXES";
	if (normalized === "UNFINISHED") return "UNFINISHED";
	if (normalized === "INCORRECT") return "INCORRECT";
	if (normalized === "CORRECT") return "CORRECT";
	return "INCONCLUSIVE";
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function throwIfAborted(signal: AbortSignal | undefined): void {
	if (signal?.aborted) throw new DOMException("The proof role was aborted", "AbortError");
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
