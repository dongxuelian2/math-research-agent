import type { Agent } from "../agent/types.js";
import { assertContributionInvariants } from "../research/invariants.js";
import type { AgentMessage, AgentRunResult, AssistantMessage } from "../models/index.js";
import { isJsonObject, type JsonObject } from "../models/json.js";
import type {
	ProofAction,
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
}

/** A model/provider failure is different from a malformed planner response. */
export class ProofProviderError extends Error {
	readonly kind = "provider" as const;

	constructor(message: string) {
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
}): AgentProofRoles {
	return {
		planner: new AgentProofPlanner(options.planner),
		researcher: new AgentProofResearcher(options.researcher),
		verifier: new AgentProofVerifier(options.verifier),
	};
}

class AgentProofPlanner implements ProofPlanner, ProofPlannerWithTrace {
	lastTrace: ProofPlannerTrace | undefined;

	constructor(private readonly agent: Agent) {}

	async plan(context: ProofPlannerContext, signal?: AbortSignal): Promise<ProofPlan> {
		throwIfAborted(signal);
		const prompt = formatPlannerPrompt(context);
		const system = plannerSystemPrompt(context.mode ?? "prove");
		let lastText = "";
		let lastError = "";
		for (let attempt = 1; attempt <= 3; attempt += 1) {
			throwIfAborted(signal);
			const request = attempt === 1
				? prompt
				: formatPlannerRetry(prompt, lastText, lastError, attempt);
			const result = await this.agent.prompt(`${system}\n\n${request}`);
			throwIfAborted(signal);
			assertAgentResult(result, "planner");
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
		const result = await this.agent.prompt(
			[
				"You are one independent mathematical worker in a planner/worker proof workflow.",
				"Solve only the focused task below. Do not spawn other agents. Use the available corpus/search/computation/scratch tools when evidence or computation is needed.",
				"Search results are discovery only. Read every proof-critical artifact body. Declare reliedOnArtifactIds, which must be a subset of artifacts actually read.",
				"Return JSON when possible: {kind: \"candidate\", candidate: {strategy, claim?, content, assumptions?:string[], dependencyClaims?:string[], reliedOnArtifactIds?:string[]}}; or {kind: \"observation\", content}; or {kind: \"blocked\", reason}.",
				"For research contributions return candidate.contribution={kind,statement,relationshipToTarget,claimId?,assumptions,dependencyClaims,childClaims?,coverageScope?,coverageAssertion?,closedCaseClaimId?,closureReason?,targetScope?,counterexampleScope?}. Reductions require >=1 unique non-self child; case splits require >=2 plus explicit scope/exhaustiveness; counterexamples require target and witness scopes. Do not label a lemma or local case as the target.",
				"A candidate must be a complete, self-contained proof or a clearly delimited sub-proof; do not hide a gap behind a slogan.",
				`Theorem:\n${context.obligation.theorem}`,
				context.obligation.context === undefined ? "" : `Theorem context:\n${context.obligation.context}`,
				`Focused task:\n${context.task.description}`,
				context.referencedMaterials.length === 0 ? "Referenced repository materials: none" : context.referencedMaterials,
			].filter((part) => part.length > 0).join("\n\n"),
		);
		throwIfAborted(signal);
		assertAgentResult(result, "researcher");
		const text = assistantText(result).trim();
		return parseResearchResult(text, context.task.taskId);
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
		const result = await this.agent.prompt(
			[
				"You are an independent verifier. You see the original task and the candidate output, not the researcher's hidden reasoning.",
				"Check the mathematical argument, every non-trivial inference, edge cases, and whether the requested task was actually completed.",
				"Do not repair the candidate silently. Explain the first decisive gap and end with exactly one verdict marker:",
				"VERDICT: CORRECT | VERDICT: CRITICALLY FLAWED - reason | VERDICT: NEEDS MINOR FIXES - reason | VERDICT: UNFINISHED - reason",
				`Original theorem:\n${context.obligation.theorem}`,
				`Original task:\n${context.task.description}`,
				`Candidate proof:\n${candidate.content}`,
				candidate.evidence.length === 0 ? "Candidate evidence receipts: none" : `Candidate evidence receipts (retrieve/check exact ids and hashes):\n${JSON.stringify(candidate.evidence, null, 2)}`,
				context.referencedMaterials === undefined || context.referencedMaterials.length === 0 ? "" : `Exact referenced repository bodies:\n${context.referencedMaterials}`,
			].join("\n\n"),
		);
		throwIfAborted(signal);
		assertAgentResult(result, "verifier");
		return parseVerification(assistantText(result));
	}
}

export function parsePlannerPlan(text: string): ProofPlan {
	const parsed = parseJsonObject(text);
	if (parsed !== undefined && Array.isArray(parsed.actions)) {
		return {
			actions: parsed.actions.map((action) => parseAction(action)),
			...(typeof parsed.summary === "string" ? { summary: parsed.summary } : {}),
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

function parseResearchResult(text: string, taskId: string): ResearchResult {
	const parsed = parseJsonObject(text);
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
				...(isRecord(candidate.contribution) ? { contribution: parseContribution(candidate.contribution) } : {}),
			},
		};
	}
	if (text.length === 0) {
		return { kind: "blocked", reason: "Researcher returned an empty response" };
	}
	return {
		kind: "candidate",
		candidate: { taskId, content: text, strategy: "agent-reasoning" },
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
			if (!Array.isArray(value.tasks)) {
				throw new ProofProtocolError("spawn requires tasks");
			}
			return { action: "spawn", tasks: value.tasks.map(parseTask), ...summary };
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
	if (!isRecord(value) || typeof value.summary !== "string" || typeof value.description !== "string") {
		throw new ProofProtocolError("spawn tasks require summary and description");
	}
	const contributionKind = value.contributionKind ?? value.contribution_kind;
	return {
		...(typeof value.taskId === "string" ? { taskId: value.taskId } : {}),
		summary: value.summary,
		description: value.description,
		...(typeof value.routeKey === "string" ? { routeKey: value.routeKey } : {}),
		...(value.scope === "TARGET" || value.scope === "CONTRIBUTION" ? { scope: value.scope } : {}),
		...(typeof value.targetClaimId === "string" ? { targetClaimId: value.targetClaimId } : typeof value.target_claim_id === "string" ? { targetClaimId: value.target_claim_id } : {}),
		...(isContributionKind(contributionKind) ? { contributionKind } : {}),
	};
}

export function createAgentProofVerifier(verifier: Agent): ProofVerifier { return new AgentProofVerifier(verifier); }

function parseContribution(value: Record<string, unknown>): import("./types.js").ProofContributionDraft {
	if (!isContributionKind(value.kind) || typeof value.statement !== "string" || typeof value.relationshipToTarget !== "string") throw new ProofProtocolError("candidate.contribution requires kind, statement, and relationshipToTarget");
	const children = Array.isArray(value.childClaims) ? value.childClaims.filter((item): item is Record<string, unknown> => isRecord(item) && typeof item.claimId === "string" && typeof item.statement === "string").map((item) => ({ claimId: item.claimId as string, statement: item.statement as string })) : undefined;
	const draft: import("./types.js").ProofContributionDraft = { kind: value.kind, statement: value.statement, relationshipToTarget: value.relationshipToTarget, ...(typeof value.claimId === "string" ? { claimId: value.claimId } : {}), assumptions: Array.isArray(value.assumptions) ? value.assumptions.filter((item): item is string => typeof item === "string") : [], dependencyClaims: Array.isArray(value.dependencyClaims) ? value.dependencyClaims.filter((item): item is string => typeof item === "string") : [], ...(children === undefined ? {} : { childClaims: children }), ...(typeof value.coverageScope === "string" ? { coverageScope: value.coverageScope } : {}), ...(typeof value.coverageAssertion === "string" ? { coverageAssertion: value.coverageAssertion } : {}), ...(typeof value.closedCaseClaimId === "string" ? { closedCaseClaimId: value.closedCaseClaimId } : {}), ...(typeof value.closureReason === "string" ? { closureReason: value.closureReason } : {}), ...(typeof value.targetScope === "string" ? { targetScope: value.targetScope } : {}), ...(typeof value.counterexampleScope === "string" ? { counterexampleScope: value.counterexampleScope } : {}) };
	try { assertContributionInvariants({ ...draft, assumptions: draft.assumptions ?? [], dependencyClaims: draft.dependencyClaims ?? [] }); } catch (error) { throw new ProofProtocolError(String(error)); }
	return draft;
}

function isContributionKind(value: unknown): value is import("./types.js").ProofContributionKind { return value === "LEMMA" || value === "REDUCTION" || value === "CASE_SPLIT" || value === "CASE_CLOSURE" || value === "COUNTEREXAMPLE" || value === "CONSTRUCTION" || value === "BOUND" || value === "OBSTRUCTION" || value === "STRUCTURAL_OBSERVATION" || value === "LITERATURE_APPLICATION"; }

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
	const failures = context.failedRoutes.length === 0
		? "none"
		: context.failedRoutes.map((failure) => `- ${failure.routeFingerprint.slice(0, 12)}: ${failure.reason}`).join("\n");
	return [
		"# WHITEBOARD\n\n" + (context.whiteboard || "(empty — initialize Goal / Plan / Failed / Backlog / Status)"),
		"# THEOREM\n\n" + context.obligation.theorem,
		context.obligation.context === undefined ? "" : "# THEOREM CONTEXT\n\n" + context.obligation.context,
		`# STATUS\n\nmode=${context.mode ?? "prove"}\nstatus=${context.status}\nstep=${context.step}`,
		"# REPOSITORY\n\n" + (repository || "(empty)"),
		"# FAILED ROUTES\n\n" + failures,
		context.tacticalDirective === undefined ? "" : "# TACTICAL DIRECTIVE\n\n" + JSON.stringify(context.tacticalDirective, null, 2),
		"# RECENT WORKER / VERIFIER OUTPUT\n\n" + (history || "(none)"),
		"# COORDINATION RULES\n\nDelegate all mathematical reasoning to workers. Use one focused task per worker. After a failed route, write why it failed and change routeKey or strategy. Read referenced repo items before relying on them. Write durable findings before the next step. Submit only after an independent CORRECT verifier result. Return one or more actions in JSON or OpenProver TOML format.",
	].filter((part) => part.length > 0).join("\n\n---\n\n");
}

function plannerSystemPrompt(mode: string): string {
	return [
		"You are the PLANNER in an OpenProver-style proof workflow. Decide what work should happen next; workers do the mathematical reasoning.",
	"Available actions: read_theorem, read_items, write_items, write_whiteboard, spawn, literature_search, use_tool, submit_proof, submit_target_proof, submit_lean_proof, stop.",
	"Keep task descriptions self-contained. Workers only receive the theorem, task, and explicitly referenced repository material. Never silently repair a verifier rejection; create a new route and record the failure.",
	mode === "prove" ? "Goal: accept a verified informal proof." : mode === "formalize_only" ? "Goal: accept only a formally checked Lean proof." : "Goal: accept both a verified informal proof and a formally checked Lean proof.",
	].join("\n\n");
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

function assistantMessageText(message: AssistantMessage): string {
	return message.content
		.filter((part): part is Extract<AssistantMessage["content"][number], { kind: "text" }> => part.kind === "text")
		.map((part) => part.text)
		.join("");
}

function assertAgentResult(result: AgentRunResult, role: string): void {
	if (result.error !== undefined || ["model_error", "session_error"].includes(result.stopReason)) {
		throw new ProofProviderError(`${role} provider failed: ${result.error?.message ?? result.stopReason}`);
	}
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
