import { readFile, realpath, stat } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { sha256, stableId } from "./ids.js";
import { activeAuthorityForClaim } from "./invariants.js";
import type { AcceptedEffect, ArtifactRef, ArtifactType, AuthorityReceipt, ClaimSnapshot, FinalProofAuthority, ResearchOutcome, ResearchProjectState } from "./types.js";
import type { CorpusArchiveClass, CorpusArchiveDisposition, CorpusArchiveEffectSource, CorpusArchiveIntent, CorpusArchiveSemanticSnapshot, CorpusNodeResolution, CorpusNodeResolutionRequest, CorpusPublishingConfig } from "./corpus-archive-types.js";

export type CorpusSourceArtifactKind = ArtifactType | "RAW_PROVIDER_RESPONSE" | "PLANNER_PROSE" | "VERIFIER_PROSE" | "SCRATCH_FILE" | "STATE_JSON" | "ROUTING_STATE_JSON" | "USAGE_JSON";

export const DEFAULT_ARTIFACT_CORPUS_POLICY: Readonly<Record<CorpusSourceArtifactKind, CorpusArchiveClass>> = {
	RAW_PROVIDER_RESPONSE: "NO_ARCHIVE", PLANNER_PROSE: "NO_ARCHIVE", VERIFIER_PROSE: "NO_ARCHIVE", SCRATCH_FILE: "NO_ARCHIVE", STATE_JSON: "NO_ARCHIVE", ROUTING_STATE_JSON: "NO_ARCHIVE", USAGE_JSON: "NO_ARCHIVE",
	CORPUS_SOURCE: "NO_ARCHIVE", WORKER_CANDIDATE: "NO_ARCHIVE", CANDIDATE_PROOF: "NO_ARCHIVE", PROMOTED_PROOF: "RESULT", COUNTEREXAMPLE: "FAILURE",
	LITERATURE_SOURCE: "LITERATURE", COMPUTATION_RESULT: "COMPUTATION", FORMAL_PROOF: "RESULT", LEAN_SOURCE: "NO_ARCHIVE", LEAN_CERTIFICATE: "NO_ARCHIVE",
	BOOTSTRAP_ANALYSIS: "NO_ARCHIVE", BOOTSTRAP_REPORT: "NO_ARCHIVE", CONTEXT_MANIFEST: "NO_ARCHIVE", SYNTHESIS_MANIFEST: "NO_ARCHIVE", FINAL_PROOF: "NO_ARCHIVE",
	AUDIT_RECEIPT: "NO_ARCHIVE", AUTHORITY_RECEIPT: "NO_ARCHIVE", CHECKPOINT: "NO_ARCHIVE", STRUCTURAL_PROBE: "NO_ARCHIVE", TACTICAL_RESULT: "NO_ARCHIVE", EXECUTION_RECEIPT: "NO_ARCHIVE",
};

export class CorpusArchivePolicy {
	classifyArtifact(kind: CorpusSourceArtifactKind): CorpusArchiveClass { return DEFAULT_ARTIFACT_CORPUS_POLICY[kind]; }

	classifyAcceptedEffect(source: CorpusArchiveEffectSource, effect: AcceptedEffect, committed: ResearchProjectState, config: CorpusPublishingConfig): CorpusArchiveDisposition {
		const outcome = source.outcome;
		if (outcome.type === "NO_PROGRESS" || outcome.type === "BLOCKED" || outcome.type === "PARTIAL_PROGRESS" || outcome.type === "STRUCTURAL_DISCOVERY") return noArchive(`${outcome.type} is not independently accepted reusable knowledge`);
		if (outcome.type === "PROVED_CLAIM" && outcome.claimId === committed.rootClaimId) return noArchive("Root effects require active FinalProofAuthority before strict publication");
		if (outcome.type === "FAILED_ROUTE" || outcome.type === "ROUTE_EXHAUSTED") {
			if (outcome.failureKind !== "MATHEMATICAL_FAILURE") return noArchive("Operational or untyped route failure is not reusable negative knowledge");
			if ((outcome.failureDomain?.trim().length ?? 0) === 0 || outcome.evidence.length === 0 || outcome.reopenPredicate === undefined || outcome.failureMechanism.trim().length === 0) return noArchive("Route failure lacks scope, evidence, obstruction, or reopen conditions");
			const routeFailureId = stableId("route", committed.projectId, outcome.obligationId, outcome.family, outcome.strategy), semantic: CorpusArchiveSemanticSnapshot = {
				title: `${outcome.family} ${outcome.mechanism} obstruction`, statement: outcome.failureMechanism, scope: outcome.failureDomain as string, sourceOutcomeType: outcome.type, strictResult: false,
				failure: { routeFamily: outcome.family, mechanism: outcome.mechanism, strategy: outcome.strategy, mathematicalScope: outcome.failureDomain as string, failurePoint: outcome.failureMechanism, obtainedProgress: outcome.evidence.length === 0 ? "No durable progress recorded." : `${outcome.evidence.length} durable evidence reference(s) were retained.`, whatIsRuledOut: `The route family ${outcome.family} using ${outcome.mechanism} within ${outcome.failureDomain as string}.`, whatIsNotRuledOut: "Other mechanisms, assumptions, and scopes are not ruled out.", reopenPredicate: outcome.reopenPredicate },
			};
			return disposition("FAILURE", "Scope-qualified reusable mathematical route failure", intentFrom({ source, effect, committed, config, classification: "FAILURE", semantic, sourceId: effect.effectId, obligationId: outcome.obligationId, routeFailureId, evidenceRefs: outcome.evidence, canonicalIdentity: routeFailureId }));
		}
		if (outcome.type === "REFUTED_CLAIM") {
			if (outcome.targetScope.trim().length === 0 || outcome.counterexampleScope.trim().length === 0) return noArchive("Counterexample is not scope-qualified");
			const authority = authorityForEffect(committed, effect.effectId), claim = committed.claims[outcome.claimId]?.at(-1);
			if (authority === undefined || claim === undefined) return noArchive("Refutation lacks committed authority");
			const semantic: CorpusArchiveSemanticSnapshot = { title: `${claim.statement} counterexample`, statement: claim.statement, scope: outcome.targetScope, sourceOutcomeType: outcome.type, authoritativeArtifact: authority.artifact, claimStatus: claim.status, strictResult: false, failure: { routeFamily: "counterexample", mechanism: "verified-counterexample", strategy: `Exhibit a counterexample in ${outcome.counterexampleScope}`, mathematicalScope: outcome.targetScope, failurePoint: `The claim is refuted by a verified counterexample in ${outcome.counterexampleScope}.`, obtainedProgress: "A verified counterexample was accepted into semantic state.", whatIsRuledOut: `The claim as stated over ${outcome.targetScope}.`, whatIsNotRuledOut: "Restricted or materially revised claims outside the counterexample scope are not ruled out." } };
			return disposition("FAILURE", "Verified scope-qualified counterexample", intentFrom({ source, effect, committed, config, classification: "FAILURE", semantic, sourceId: effect.effectId, theoremId: outcome.claimId, evidenceRefs: uniqueRefs([authority.artifact, ...authority.evidenceRefs]), semanticSummaryRef: authority.artifact, canonicalIdentity: outcome.claimId, claim }));
		}
		if (outcome.type === "REDUCTION" || outcome.type === "CASE_SPLIT") return this.authoritativeDisposition("ATTEMPT", "Verified unresolved mathematical development", source, effect, committed, config, outcome.claimId);
		if (outcome.type === "CASE_CLOSURE" || outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA") {
			const authority = authorityForEffect(committed, effect.effectId);
			if (authority === undefined) return noArchive("Positive result lacks committed authority receipt");
			const originalType = committed.artifacts[authority.sourceArtifact.artifactId]?.artifactType, classification: Exclude<CorpusArchiveClass, "NO_ARCHIVE"> = originalType === "COMPUTATION_RESULT" ? "COMPUTATION" : originalType === "LITERATURE_SOURCE" ? "LITERATURE" : "RESULT";
			return this.authoritativeDisposition(classification, `Accepted ${classification.toLocaleLowerCase()} semantic effect`, source, effect, committed, config, outcome.claimId);
		}
		if (outcome.type === "VERIFIED_OBSERVATION") {
			const artifactType = committed.artifacts[outcome.observation.artifactId]?.artifactType, classification = artifactType === "COMPUTATION_RESULT" ? "COMPUTATION" : artifactType === "LITERATURE_SOURCE" ? "LITERATURE" : "ATTEMPT";
			const semantic: CorpusArchiveSemanticSnapshot = { title: outcome.statement, statement: outcome.statement, scope: "verified observation", sourceOutcomeType: outcome.type, authoritativeArtifact: outcome.observation, strictResult: false };
			return disposition(classification, "Verified reusable observation", intentFrom({ source, effect, committed, config, classification, semantic, sourceId: effect.effectId, evidenceRefs: [outcome.observation], semanticSummaryRef: outcome.observation, canonicalIdentity: outcome.observation.artifactId }));
		}
		return noArchive(`No archive rule for ${outcome.type}`);
	}

	classifyPromotionClosure(committed: ResearchProjectState, authority: FinalProofAuthority, config: CorpusPublishingConfig): CorpusArchiveDisposition {
		const strict = strictPublicationAuthority(committed, authority);
		if (strict === undefined) return noArchive("Strict result lacks current final-proof, root-claim, and fresh-audit authority");
		const { root, rootAuthority } = strict;
		const semantic: CorpusArchiveSemanticSnapshot = { title: root.statement, statement: root.statement, scope: rootAuthority.scope ?? "root theorem", sourceOutcomeType: "FINAL_PROOF_AUTHORITY", authoritativeArtifact: authority.artifact, claimStatus: root.status, strictResult: true }, sourceId = authority.finalProofAuthorityId, now = new Date().toISOString(), canonicalKey = stableId("corpus-canonical", committed.projectId, root.claimId);
		const intent: CorpusArchiveIntent = { schemaVersion: 1, intentId: stableId("corpus-archive-intent", committed.projectId, sourceId), projectId: committed.projectId, sourceId, finalProofAuthorityId: authority.finalProofAuthorityId, theoremId: root.claimId, claimSnapshotHash: sha256(JSON.stringify(root)), researchMapId: committed.projectId, researchMapVersion: committed.events.length, classificationHint: "RESULT", semanticSummaryRef: authority.artifact, evidenceRefs: uniqueRefs([authority.artifact, rootAuthority.artifact, ...rootAuthority.evidenceRefs, ...root.auditRefs]), createdFromAuthoritativeState: true, canonicalKey, artifactSlug: slugify(root.statement), ...(config.nodePath === undefined ? {} : { requestedNodePath: config.nodePath }), semantic, status: "PENDING", createdAt: now, updatedAt: now };
		return disposition("RESULT", "Active final proof authority closes the strict publication gate", intent);
	}

	private authoritativeDisposition(classification: Exclude<CorpusArchiveClass, "NO_ARCHIVE">, reason: string, source: CorpusArchiveEffectSource, effect: AcceptedEffect, committed: ResearchProjectState, config: CorpusPublishingConfig, claimId: string): CorpusArchiveDisposition {
		const authority = authorityForEffect(committed, effect.effectId), claim = committed.claims[claimId]?.at(-1);
		if (authority === undefined || claim === undefined) return noArchive("Effect lacks committed claim authority");
		const semantic: CorpusArchiveSemanticSnapshot = { title: claim.statement, statement: claim.statement, scope: authority.scope ?? "scoped result", sourceOutcomeType: source.outcome.type, authoritativeArtifact: authority.artifact, claimStatus: claim.status, strictResult: false };
		return disposition(classification, reason, intentFrom({ source, effect, committed, config, classification, semantic, sourceId: effect.effectId, theoremId: claimId, evidenceRefs: uniqueRefs([authority.artifact, ...authority.evidenceRefs]), semanticSummaryRef: authority.artifact, canonicalIdentity: claimId, claim }));
	}
}

/** Exact durable authority chain required for strict corpus publication. */
export function strictPublicationAuthority(state: ResearchProjectState, authority: FinalProofAuthority): { readonly root: ClaimSnapshot; readonly rootAuthority: AuthorityReceipt } | undefined {
	if (authority.status !== "ACTIVE" || state.status !== "PROVED" || state.rootClaimId !== authority.rootClaimId || state.currentFinalProofAuthority?.finalProofAuthorityId !== authority.finalProofAuthorityId) return undefined;
	if (!state.finalProofHistory.some((item) => item.status === "ACTIVE" && item.finalProofAuthorityId === authority.finalProofAuthorityId)) return undefined;
	const root = state.claims[authority.rootClaimId]?.at(-1), rootAuthority = root === undefined ? undefined : activeAuthorityForClaim(state, root.claimId, root.revision), contract = state.rootObjectiveContract;
	if (root === undefined || root.status !== "PROVED" || root.revision !== authority.rootClaimRevision || rootAuthority?.authorityReceiptId !== authority.rootAuthorityReceiptId) return undefined;
	if (contract?.status !== "VALID" || contract.rootClaimId !== root.claimId || normalizeMath(contract.statement) !== normalizeMath(root.statement)) return undefined;
	const finalArtifact = state.artifacts[authority.artifact.artifactId];
	if (finalArtifact?.artifactType !== "FINAL_PROOF" || finalArtifact.contentHash !== authority.artifact.contentHash || state.finalProofArtifact?.artifactId !== authority.artifact.artifactId || state.finalProofArtifact.contentHash !== authority.artifact.contentHash) return undefined;
	if (rootAuthority.effectKind !== "PROVED_CLAIM" || rootAuthority.sourceArtifact.artifactId !== authority.artifact.artifactId || rootAuthority.sourceArtifact.contentHash !== authority.artifact.contentHash || rootAuthority.artifact.contentHash !== authority.artifact.contentHash) return undefined;
	if (rootAuthority.trustReceiptIds.length < 2 || rootAuthority.trustReceiptIds.some((id) => { const receipt = state.trustReceipts[id]; return receipt === undefined || receipt.verdict !== "CORRECT" || !receipt.independentContext || receipt.stale || receipt.candidate.artifactId !== rootAuthority.artifact.artifactId || receipt.candidate.contentHash !== rootAuthority.artifact.contentHash; })) return undefined;
	return { root, rootAuthority };
}

interface IntentInput {
	readonly source: CorpusArchiveEffectSource; readonly effect: AcceptedEffect; readonly committed: ResearchProjectState; readonly config: CorpusPublishingConfig;
	readonly classification: Exclude<CorpusArchiveClass, "NO_ARCHIVE">; readonly semantic: CorpusArchiveSemanticSnapshot; readonly sourceId: string;
	readonly theoremId?: string; readonly obligationId?: string; readonly routeFailureId?: string; readonly evidenceRefs: readonly ArtifactRef[]; readonly semanticSummaryRef?: ArtifactRef;
	readonly canonicalIdentity: string; readonly claim?: ClaimSnapshot;
}

function intentFrom(input: IntentInput): CorpusArchiveIntent {
	const now = new Date().toISOString(), canonicalKey = stableId("corpus-canonical", input.committed.projectId, input.canonicalIdentity);
	return { schemaVersion: 1, intentId: stableId("corpus-archive-intent", input.committed.projectId, input.sourceId), projectId: input.committed.projectId, runId: input.source.cycleId, sourceId: input.sourceId, sourceEffectId: input.effect.effectId, sourceEventId: input.effect.eventId, ...(input.theoremId === undefined ? {} : { theoremId: input.theoremId }), ...(input.claim === undefined ? {} : { claimSnapshotHash: sha256(JSON.stringify(input.claim)) }), researchMapId: input.committed.projectId, researchMapVersion: input.committed.events.length, ...(input.obligationId === undefined ? {} : { obligationId: input.obligationId }), ...(input.routeFailureId === undefined ? {} : { routeFailureId: input.routeFailureId }), classificationHint: input.classification, ...(input.semanticSummaryRef === undefined ? {} : { semanticSummaryRef: input.semanticSummaryRef }), evidenceRefs: uniqueRefs(input.evidenceRefs), createdFromAuthoritativeState: true, canonicalKey, artifactSlug: slugify(input.semantic.title), ...(input.config.nodePath === undefined ? {} : { requestedNodePath: input.config.nodePath }), semantic: input.semantic, status: "PENDING", createdAt: now, updatedAt: now };
}

export class CorpusNodeResolver {
	async resolve(request: CorpusNodeResolutionRequest): Promise<CorpusNodeResolution> {
		const checkout = resolve(request.checkout), researchRoot = resolve(checkout, "research");
		if (!await isDirectory(researchRoot)) return blocked("Canonical checkout has no research/ directory");
		if (request.requestedNodePath !== undefined) return this.validateExistingNode(checkout, researchRoot, request.requestedNodePath, "configured node");
		const aliases = await readAliases(checkout), keys = uniqueStrings([request.theoremId, request.projectId, request.obligationId, request.researchMapId]), matches = uniqueStrings(keys.flatMap((key) => aliases[`theorem:${key}`] ?? aliases[`project:${key}`] ?? aliases[`obligation:${key}`] ?? aliases[`research-map:${key}`] ?? []));
		if (matches.length > 1) return blocked(`Alias metadata resolves to multiple canonical nodes: ${matches.join(", ")}`);
		if (matches.length === 1) return this.validateExistingNode(checkout, researchRoot, matches[0] as string, "provenance alias");
		const exact = `research/${request.projectId}`;
		if (await isDirectory(resolve(checkout, ...exact.split("/")))) return this.validateExistingNode(checkout, researchRoot, exact, "exact project node");
		return blocked("No deterministic existing canonical node matches the semantic scope");
	}

	private async validateExistingNode(checkout: string, researchRoot: string, input: string, reason: string): Promise<CorpusNodeResolution> {
		const normalized = input.trim().replaceAll("\\", "/").replace(/^\.\//u, "").replace(/\/$/u, "");
		if (normalized !== "research" && !normalized.startsWith("research/")) return blocked("Corpus node must be research/ or a descendant");
		if (isAbsolute(input) || normalized.split("/").some((part) => part === ".." || part === "." || part.length === 0)) return blocked("Corpus node path is not canonical and relative");
		const prohibited = /^(?:campaigns?|rounds?|cycles?|five-round-plan|strict-layer|critical-layer|\d+)$/iu;
		if (normalized.split("/").some((part) => prohibited.test(part))) return blocked("Corpus node uses obsolete workflow organization");
		const candidate = resolve(checkout, ...normalized.split("/"));
		if (!await isDirectory(candidate)) return blocked(`Configured corpus node does not exist: ${normalized}`);
		const [realResearch, realCandidate] = await Promise.all([realpath(researchRoot), realpath(candidate)]), escaped = relative(realResearch, realCandidate);
		if (escaped === ".." || escaped.startsWith(`..${sep}`) || isAbsolute(escaped)) return blocked("Corpus node resolves outside the canonical research tree");
		return { status: "RESOLVED", nodePath: relative(checkout, realCandidate).split(sep).join("/"), reason };
	}
}

async function readAliases(checkout: string): Promise<Readonly<Record<string, readonly string[]>>> {
	const path = resolve(checkout, "provenance", "corpus-node-aliases.json");
	let value: unknown;
	try { value = JSON.parse(await readFile(path, "utf8")); } catch (error) { if (isMissing(error)) return {}; throw new Error(`Invalid corpus node aliases: ${String(error)}`); }
	if (!isRecord(value) || !isRecord(value.aliases)) throw new Error("Invalid corpus node aliases schema");
	const result: Record<string, readonly string[]> = {};
	for (const [key, raw] of Object.entries(value.aliases)) {
		if (typeof raw === "string") result[key] = [raw];
		else if (Array.isArray(raw) && raw.every((item) => typeof item === "string")) result[key] = raw;
		else throw new Error(`Invalid corpus node alias: ${key}`);
	}
	return result;
}

function authorityForEffect(state: ResearchProjectState, effectId: string): AuthorityReceipt | undefined { return Object.values(state.authorityReceipts).find((receipt) => receipt.effectId === effectId); }
function noArchive(reason: string): CorpusArchiveDisposition { return { classification: "NO_ARCHIVE", reason }; }
function disposition(classification: Exclude<CorpusArchiveClass, "NO_ARCHIVE">, reason: string, intent: CorpusArchiveIntent): CorpusArchiveDisposition { return { classification, reason, intent }; }
function blocked(reason: string): CorpusNodeResolution { return { status: "BLOCKED_PLACEMENT", reason }; }
function slugify(value: string): string { const slug = value.normalize("NFKD").toLocaleLowerCase().replace(/[^a-z0-9]+/gu, "-").replace(/^-+|-+$/gu, "").replace(/-{2,}/gu, "-").slice(0, 72).replace(/-+$/u, ""); return slug.length === 0 ? "research-knowledge" : slug; }
function normalizeMath(value: string): string { return value.trim().replace(/\s+/gu, " "); }
function uniqueStrings(values: readonly (string | undefined)[]): string[] { return [...new Set(values.filter((value): value is string => typeof value === "string" && value.trim().length > 0).map((value) => value.trim()))]; }
function uniqueRefs(values: readonly ArtifactRef[]): ArtifactRef[] { const seen = new Set<string>(); return values.filter((value) => { const key = `${value.artifactId}:${value.contentHash}`; if (seen.has(key)) return false; seen.add(key); return true; }); }
async function isDirectory(path: string): Promise<boolean> { try { return (await stat(path)).isDirectory(); } catch (error) { if (isMissing(error)) return false; throw error; } }
function isMissing(error: unknown): boolean { return (error as NodeJS.ErrnoException | undefined)?.code === "ENOENT"; }
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
