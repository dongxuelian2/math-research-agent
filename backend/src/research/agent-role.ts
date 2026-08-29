import type { Agent } from "../agent/types.js";
import type { AgentRunResult, AssistantMessage } from "../models/index.js";
import { createUserMessage } from "../models/messages.js";
import type { JsonObject } from "../models/json.js";
import type { FinalAuditor, SynthesisAgent, SynthesisManifest } from "./closure.js";
import type { CorpusBootstrapper, SemanticFileAnalysis } from "./corpus.js";
import type { DirectorProjectSnapshot, DirectorProposal, ModelResearchDirector } from "./runtime.js";
import type { BootstrapDependencyProposal, BootstrapProposal, TrustReceipt } from "./types.js";
import type { LiteratureCandidate } from "./literature.js";
import type { ProofLeanProjectContext } from "../proof/types.js";
import { BOOTSTRAP_ANALYSIS_SCHEMA, BOOTSTRAP_REVIEW_SCHEMA, BOOTSTRAP_SCHEMA_VERSION, BootstrapSchemaValidationError, BootstrapStructuredOutputParseError, bootstrapSchemaInstructions, parseBootstrapAnalysisText } from "./bootstrap-schema.js";

export class ResearchRoleProtocolError extends Error {
	readonly kind = "protocol" as const;
	constructor(message: string) { super(message); this.name = "ResearchRoleProtocolError"; }
}
export class ResearchRoleProviderError extends Error {
	readonly kind = "provider" as const;
	constructor(message: string) { super(message); this.name = "ResearchRoleProviderError"; }
}

export class AgentResearchDirector implements ModelResearchDirector {
	constructor(private readonly agentFactory: (snapshot: DirectorProjectSnapshot) => Promise<Agent>) {}
	async decide(snapshot: DirectorProjectSnapshot): Promise<DirectorProposal> {
		const agent = await this.agentFactory(snapshot);
		const text = await prompt(agent, [
			"You are the strategic Research Director. You may propose strategy but never promote mathematical truth.",
			"Return exactly one JSON object with action, reason, and only action-relevant stable IDs/fields.",
			"For target actions copy the exact frontier field names targetObligationId and targetClaimId; do not rename them to obligationId or claimId.",
			"Allowed actions: ATTACK_OBLIGATION, CREATE_AUXILIARY_OBLIGATION, SPLIT_OBLIGATION, REQUEST_REDUCTION, REQUEST_COUNTEREXAMPLE, REQUEST_COMPUTATION, REQUEST_LITERATURE, RUN_STRUCTURAL_PROBE, CHANGE_ROUTE, REOPEN_ROUTE, SUSPEND_ROUTE, MARK_ROUTE_EXHAUSTED, RESTRUCTURE_RESEARCH_MAP, TRIGGER_SYNTHESIS, STOP_PROJECT.",
			`Semantic project snapshot:\n${JSON.stringify(snapshot, null, 2)}`,
		].join("\n\n"), "research_director");
		const value = json(text);
		if (typeof value.action !== "string" || typeof value.reason !== "string") throw new ResearchRoleProtocolError("DirectorDecision requires action and reason");
		return {
			action: value.action as DirectorProposal["action"], reason: value.reason,
			...firstStringField(value, "targetObligationId", "obligationId"), ...firstStringField(value, "targetClaimId", "claimId"),
			...optionalStringField(value, "routeId"), ...optionalStringField(value, "routeFamily"),
			...optionalStringField(value, "auxiliaryStatement"), ...optionalStringField(value, "literatureQuery"),
			...(typeof value.budgetAllocated === "number" ? { budgetAllocated: value.budgetAllocated } : {}),
		};
	}
}

export class AgentCorpusBootstrapper implements CorpusBootstrapper {
	constructor(private readonly agentFactory: (artifactId: string) => Promise<Agent>) {}
	async analyzeFile(request: Parameters<CorpusBootstrapper["analyzeFile"]>[0]): Promise<SemanticFileAnalysis> {
		const agent = await this.agentFactory(request.record.artifactId);
		let modelRun: AgentRunResult | undefined;
		const text = await prompt(agent, [
			"You are the semantic corpus bootstrapper. Analyze only this bounded source. Historical prose is not automatically current truth.",
			`Bootstrap protocol version: ${BOOTSTRAP_SCHEMA_VERSION}`,
			bootstrapSchemaInstructions(),
			"Kinds: DEFINITION, CLAIM, OPEN_PROBLEM, FAILED_ROUTE, REDUCTION, CASE_SPLIT, COMPUTATIONAL_EVIDENCE. A failed strategy must be FAILED_ROUTE, never a claim to prove.",
			"Dependency direction is fromEntity (the proposal that relies on something) -> toEntity (the relied-on proposal). confidence is the EXPLICIT|INFERRED provenance enum; a numeric score, if useful, belongs only in confidenceScore.",
			`Source metadata: ${JSON.stringify(request.record)}`,
			`Exact bounded line range: ${request.range.startLine}-${request.range.endLine}`,
			`Exact source body:\n${request.body}`,
		].join("\n\n"), "corpus_bootstrapper", BOOTSTRAP_ANALYSIS_SCHEMA, (result) => { modelRun = result; });
		const assistant = [...(modelRun?.messages ?? [])].reverse().find((message): message is AssistantMessage => message.role === "assistant"), metadata = { sessionId: modelRun?.runId ?? "unknown", ...(assistant?.provider === undefined ? {} : { provider: assistant.provider }), ...(assistant?.model === undefined ? {} : { model: assistant.model }) };
		try { return { ...parseBootstrapAnalysisText(text), modelMetadata: { ...metadata, rawResponse: text } }; } catch (error) { if (error instanceof BootstrapSchemaValidationError || error instanceof BootstrapStructuredOutputParseError) error.attachModelMetadata(metadata); throw error; }
	}
	async review(request: Parameters<NonNullable<CorpusBootstrapper["review"]>>[0]): Promise<{ readonly frontierEntityKeys?: readonly string[]; readonly warnings?: readonly string[] }> {
		const agent = await this.agentFactory("consistency-review");
		const text = await prompt(agent, `Review this merged bootstrap map for identity conflicts, dependency direction, failed-route classification, and current frontier. ${bootstrapSchemaInstructions(BOOTSTRAP_REVIEW_SCHEMA)}\n\n${JSON.stringify(request, null, 2)}`, "corpus_bootstrapper", BOOTSTRAP_REVIEW_SCHEMA);
		const value = json(text);
		if (!Array.isArray(value.frontierEntityKeys) || !Array.isArray(value.warnings)) throw new ResearchRoleProtocolError("Bootstrap review schema invalid");
		return { frontierEntityKeys: value.frontierEntityKeys.filter((item): item is string => typeof item === "string"), warnings: value.warnings.filter((item): item is string => typeof item === "string") };
	}
}

export class AgentSynthesisRole implements SynthesisAgent {
	constructor(private readonly agentFactory: () => Promise<Agent>) {}
	async synthesize(manifest: SynthesisManifest, bodies: Readonly<Record<string, string>>): Promise<import("./closure.js").SynthesisResult> {
		const agent = await this.agentFactory();
		const text = await prompt(agent, [
			"Synthesize a new self-contained final proof from the authoritative manifest. Do not merely concatenate. The immutable RootObjectiveContract is authoritative: preserve its exact theorem statement, introduce no assumption outside allowedAssumptions, and close every dependency and case.",
			`Manifest:\n${JSON.stringify(manifest, null, 2)}`, `Exact authoritative bodies:\n${JSON.stringify(bodies, null, 2)}`,
			"Return JSON {proof:string, usedArtifactIds:string[], theoremStatement:string, assumptions:string[]} where theoremStatement and assumptions declare the exact theorem proved by the final body.",
		].join("\n\n"), "synthesizer");
		const value = json(text);
		if (typeof value.proof !== "string" || !Array.isArray(value.usedArtifactIds) || (value.theoremStatement !== undefined && typeof value.theoremStatement !== "string") || (value.assumptions !== undefined && (!Array.isArray(value.assumptions) || !value.assumptions.every((item) => typeof item === "string")))) throw new ResearchRoleProtocolError("SynthesisResult schema invalid");
		return { proof: value.proof, usedArtifactIds: value.usedArtifactIds.filter((item): item is string => typeof item === "string"), theoremStatement: typeof value.theoremStatement === "string" ? value.theoremStatement : manifest.rootObjectiveContract.statement, assumptions: Array.isArray(value.assumptions) ? value.assumptions as string[] : manifest.assumptions };
	}
}

export class AgentFinalAuditRole implements FinalAuditor {
	constructor(private readonly agentFactory: () => Promise<Agent>, private readonly profile: string) {}
	async audit(request: { readonly manifest: SynthesisManifest; readonly candidate: import("./types.js").ArtifactRef; readonly body: string; readonly dependencyBodies: Readonly<Record<string, string>> }, _signal?: AbortSignal): Promise<{ readonly verdict: TrustReceipt["verdict"]; readonly feedback: string; readonly profile: string }> {
		const agent = await this.agentFactory();
		const text = await prompt(agent, [
			"Independently audit this synthesized proof from scratch for correctness, dependencies, coverage, notation and assumptions. Explicitly verify that the final theorem statement matches RootObjectiveContract.statement, all final assumptions are allowed by that contract or discharged in the manifest, quantifiers have not drifted, and no new hypothesis was introduced. Do not inherit component verdicts.",
			`Manifest:\n${JSON.stringify(request.manifest, null, 2)}`, `Candidate:\n${request.body}`, `Exact dependency/source bodies (artifact_read is still preferred when tools are available):\n${JSON.stringify(request.dependencyBodies, null, 2)}`,
			"Return JSON {verdict:'CORRECT'|'CRITICALLY_FLAWED'|'UNFINISHED'|'INCORRECT'|'INCONCLUSIVE',feedback:string}.",
		].join("\n\n"), this.profile);
		const value = json(text);
		if (!isVerdict(value.verdict) || typeof value.feedback !== "string") throw new ResearchRoleProtocolError("FinalAuditResult schema invalid");
		return { verdict: value.verdict, feedback: value.feedback, profile: this.profile };
	}
}

export class AgentLiteratureApplicability {
	constructor(private readonly agentFactory: () => Promise<Agent>) {}
	async assess(candidate: LiteratureCandidate, body: string, targetStatement: string): Promise<{ readonly literatureClaim: string; readonly assumptions: readonly string[]; readonly applicable: boolean; readonly reason: string; readonly scopeLimitations: readonly string[] }> {
		const agent = await this.agentFactory(); const text = await prompt(agent, ["Assess applicability of an acquired exact literature source to the target. A snippet or summary is not authority. Fail closed if the source body does not establish the claimed theorem.", `Target:\n${targetStatement}`, `Metadata:\n${JSON.stringify(candidate, null, 2)}`, `Exact acquired source:\n${body}`, "Return JSON {literatureClaim,assumptions:string[],applicable:boolean,reason,scopeLimitations:string[]}"].join("\n\n"), "literature_researcher"); const value = json(text); if (typeof value.literatureClaim !== "string" || !Array.isArray(value.assumptions) || typeof value.applicable !== "boolean" || typeof value.reason !== "string" || !Array.isArray(value.scopeLimitations)) throw new ResearchRoleProtocolError("LiteratureApplicability schema invalid"); return { literatureClaim: value.literatureClaim, assumptions: value.assumptions.filter((item): item is string => typeof item === "string"), applicable: value.applicable, reason: value.reason, scopeLimitations: value.scopeLimitations.filter((item): item is string => typeof item === "string") };
	}
}

/** The model may draft Lean, but only a process-backed verifier may certify it. */
export class AgentFormalizerRole {
	constructor(private readonly agentFactory: () => Promise<Agent>) {}
	async formalize(request: { readonly rootStatement: string; readonly informalProof: string; readonly existingLean?: string; readonly leanProject?: ProofLeanProjectContext; readonly formalizerSkill?: string }): Promise<{ readonly lean: string; readonly notes: string }> {
		const agent = await this.agentFactory();
		const text = await prompt(agent, [
			"Use the pinned upstream Lean 4 skill below to draft a complete Lean proof for the exact root statement. Your output is an untrusted candidate and will be checked by the configured Lean process; never claim compilation succeeded.",
			...(request.leanProject === undefined ? [] : [`Session Lean project (authoritative):\n${JSON.stringify(request.leanProject, null, 2)}`]),
			...(request.formalizerSkill === undefined ? [] : [`Pinned upstream Lean 4 skill:\n${request.formalizerSkill}`]),
			`Root statement:\n${request.rootStatement}`,
			`Verified informal proof:\n${request.informalProof}`,
			...(request.existingLean === undefined ? [] : [`Existing Lean source to repair:\n${request.existingLean}`]),
			"Return JSON {lean:string,notes:string}. Do not claim that compilation succeeded.",
		].join("\n\n"), "formalizer");
		const value = json(text);
		if (typeof value.lean !== "string" || value.lean.trim().length === 0 || typeof value.notes !== "string") throw new ResearchRoleProtocolError("FormalizationResult schema invalid");
		return { lean: value.lean, notes: value.notes };
	}
}

async function prompt(agent: Agent, input: string, role: string, responseSchema?: JsonObject, capture?: (result: AgentRunResult) => void): Promise<string> {
	const result = await agent.prompt(responseSchema === undefined ? input : createUserMessage(input, responseSchema));
	capture?.(result);
	if (result.error !== undefined || result.stopReason === "model_error" || result.stopReason === "session_error") throw new ResearchRoleProviderError(`${role} provider failed: ${result.error?.message ?? result.stopReason}`);
	return assistantText(result).trim();
}
function assistantText(result: AgentRunResult): string {
	for (let index = result.messages.length - 1; index >= 0; index -= 1) { const message = result.messages[index]; if (message?.role === "assistant") return (message as AssistantMessage).content.filter((part): part is Extract<AssistantMessage["content"][number], { kind: "text" }> => part.kind === "text").map((part) => part.text).join(""); }
	return "";
}
function json(text: string): Record<string, unknown> {
	const trimmed = text.trim().replace(/^```(?:json)?\s*/iu, "").replace(/\s*```$/u, ""), start = trimmed.indexOf("{"), end = trimmed.lastIndexOf("}");
	if (start < 0 || end <= start) throw new ResearchRoleProtocolError("Structured role returned no JSON object");
	try { const value: unknown = JSON.parse(trimmed.slice(start, end + 1)); if (!record(value)) throw new Error("not object"); return value; } catch (error) { throw new ResearchRoleProtocolError(`Malformed structured JSON: ${String(error)}`); }
}
function optionalStringField<K extends string>(value: Record<string, unknown>, key: K): Partial<Record<K, string>> { return typeof value[key] === "string" ? { [key]: value[key] } as Partial<Record<K, string>> : {}; }
function firstStringField<K extends string>(value: Record<string, unknown>, key: K, alias: string): Partial<Record<K, string>> { return typeof value[key] === "string" ? { [key]: value[key] } as Partial<Record<K, string>> : typeof value[alias] === "string" ? { [key]: value[alias] } as Partial<Record<K, string>> : {}; }
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function isVerdict(value: unknown): value is TrustReceipt["verdict"] { return value === "CORRECT" || value === "MINOR_FIX" || value === "UNFINISHED" || value === "CRITICALLY_FLAWED" || value === "INCORRECT" || value === "INCONCLUSIVE"; }
