import type { ResearchStore } from "./store.js";
import type {
	ArtifactRef, AssumptionAssessment, AssumptionDischargeDependency, AssumptionDischargeWitness, AuthorityReceipt, ClaimSnapshot, ResearchContributionKind, ResearchOutcome, ResearchProjectState,
	ResearchRoute, RouteReopenActor, RouteReopenPredicate, TrustReceipt, VerifiedResearchContribution,
} from "./types.js";

export const RESEARCH_SEMANTIC_INVARIANTS = Object.freeze({
	verifiedSubtaskIsNotTargetProof: "verified subtask != target proof",
	artifactExistenceIsNotAuthority: "artifact exists != artifact has mathematical authority",
	contextAvailabilityIsNotEvidenceUse: "context available != evidence used",
	searchIsNotRead: "search hit != body read",
	readIsNotReliance: "body read != mathematical dependency",
	observationIsNotProgress: "structural observation != structural progress",
	providerFailureIsNotRouteFailure: "provider failure != mathematical route failure",
	historicalProseIsNotTruth: "historical prose != current truth",
	exactlyOnceEffects: "at-least-once execution + exactly-once state effect",
	conditionalIsNotUnconditional: "conditional theorem != unconditional theorem",
	nonemptyDecomposition: "empty decomposition != valid reduction/case split",
});

export interface InvariantViolation {
	readonly code: string;
	readonly message: string;
	readonly entityId?: string;
}

export interface ProjectInvariantResult {
	readonly valid: boolean;
	readonly checkedAt: string;
	readonly violations: readonly InvariantViolation[];
}

type ContributionShape = Pick<VerifiedResearchContribution, "kind" | "statement" | "relationshipToTarget" | "claimId" | "assumptions" | "dependencyClaims" | "childClaims" | "coverageScope" | "coverageAssertion" | "closedCaseClaimId" | "closureReason" | "targetScope" | "counterexampleScope">;

export function contributionInvariantViolations(contribution: ContributionShape, parentClaimId?: string): InvariantViolation[] {
	const violations: InvariantViolation[] = [];
	if (contribution.statement.trim().length === 0) violations.push(issue("CONTRIBUTION_EMPTY_STATEMENT", "Contribution statement must be nonempty"));
	if (contribution.relationshipToTarget.trim().length === 0) violations.push(issue("CONTRIBUTION_EMPTY_RELATIONSHIP", "Contribution relationshipToTarget must be nonempty"));
	if (contribution.assumptions.some((item) => item.trim().length === 0)) violations.push(issue("CONTRIBUTION_EMPTY_ASSUMPTION", "Contribution assumptions must be exact nonempty statements"));
	if (new Set(contribution.dependencyClaims).size !== contribution.dependencyClaims.length) violations.push(issue("CONTRIBUTION_DUPLICATE_DEPENDENCY", "Contribution dependencies must be unique"));
	if (parentClaimId !== undefined && contribution.dependencyClaims.includes(parentClaimId)) violations.push(issue("CONTRIBUTION_SELF_DEPENDENCY", "Contribution cannot depend on its target claim", parentClaimId));
	const children = contribution.childClaims ?? [];
	if (children.some((item) => item.claimId.trim().length === 0 || item.statement.trim().length === 0)) violations.push(issue("DECOMPOSITION_EMPTY_CHILD", "Decomposition children require nonempty claim ids and statements"));
	if (new Set(children.map((item) => item.claimId)).size !== children.length) violations.push(issue("DECOMPOSITION_DUPLICATE_CHILD", "Decomposition children must be unique"));
	if (parentClaimId !== undefined && children.some((item) => item.claimId === parentClaimId)) violations.push(issue("DECOMPOSITION_SELF_CHILD", "A decomposition cannot contain its parent", parentClaimId));
	switch (contribution.kind) {
		case "REDUCTION":
			if (children.length < 1) violations.push(issue("EMPTY_REDUCTION", "A reduction requires at least one child claim"));
			break;
		case "CASE_SPLIT":
			if (children.length < 2) violations.push(issue("INVALID_CASE_SPLIT_CARDINALITY", "A case split requires at least two child claims"));
			if ((contribution.coverageScope?.trim().length ?? 0) === 0) violations.push(issue("CASE_SPLIT_EMPTY_SCOPE", "A case split requires a nonempty coverage scope"));
			if ((contribution.coverageAssertion?.trim().length ?? 0) === 0) violations.push(issue("CASE_SPLIT_MISSING_COVERAGE_ASSERTION", "A case split requires an explicit exhaustiveness assertion"));
			break;
		case "CASE_CLOSURE":
			if ((contribution.closedCaseClaimId?.trim().length ?? 0) === 0) violations.push(issue("CASE_CLOSURE_MISSING_CASE", "Case closure must identify the exact case claim"));
			if ((contribution.closureReason?.trim().length ?? 0) === 0) violations.push(issue("CASE_CLOSURE_MISSING_REASON", "Case closure must state why the case is closed"));
			break;
		case "COUNTEREXAMPLE":
			if ((contribution.targetScope?.trim().length ?? 0) === 0) violations.push(issue("COUNTEREXAMPLE_MISSING_TARGET_SCOPE", "A counterexample must preserve the target domain"));
			if ((contribution.counterexampleScope?.trim().length ?? 0) === 0) violations.push(issue("COUNTEREXAMPLE_MISSING_WITNESS_SCOPE", "A counterexample must identify its witness domain"));
			break;
		case "LEMMA": case "CONSTRUCTION": case "BOUND": case "OBSTRUCTION": case "STRUCTURAL_OBSERVATION": case "LITERATURE_APPLICATION":
			break;
	}
	return violations;
}

export function assertContributionInvariants(contribution: ContributionShape, parentClaimId?: string): void {
	const violations = contributionInvariantViolations(contribution, parentClaimId);
	if (violations.length > 0) throw new Error(`PROTOCOL_ERROR: ${violations.map((item) => `${item.code}: ${item.message}`).join("; ")}`);
}

export function assertVerifiedContributionInvariants(state: ResearchProjectState, contribution: VerifiedResearchContribution): void {
	if (state.claims[contribution.targetClaimId]?.at(-1) === undefined) throw new Error(`PROTOCOL_ERROR: contribution parent target is missing: ${contribution.targetClaimId}`);
	assertContributionInvariants(contribution, contribution.targetClaimId);
	assertReceipt(contribution.verification, contribution.candidate);
}

export function assertResearchOutcomeInvariants(state: ResearchProjectState, outcome: ResearchOutcome): void {
	switch (outcome.type) {
		case "PROVED_CLAIM": case "NEW_LEMMA":
			assertReceipts(outcome.receipts, outcome.candidate);
			if (outcome.dependencies.includes(outcome.claimId)) throw new Error("INVARIANT_ERROR: a proved claim cannot depend on itself");
			break;
		case "REFUTED_CLAIM":
			assertReceipts(outcome.receipts, outcome.counterexample);
			if (outcome.targetScope.trim().length === 0 || outcome.counterexampleScope.trim().length === 0) throw new Error("INVARIANT_ERROR: refutation requires explicit target and counterexample scope");
			break;
		case "REDUCTION":
			assertReceipts(outcome.receipts, outcome.proof);
			assertDecomposition(state, outcome.claimId, outcome.childClaims, 1, "reduction");
			if (outcome.scope.trim().length === 0) throw new Error("INVARIANT_ERROR: reduction scope must be explicit");
			break;
		case "CASE_SPLIT":
			assertReceipts(outcome.receipts, outcome.proof);
			assertDecomposition(state, outcome.claimId, outcome.cases, 2, "case split");
			if (outcome.scope.trim().length === 0 || outcome.coverageAssertion.trim().length === 0) throw new Error("INVARIANT_ERROR: case split requires scope and coverage assertion");
			break;
		case "CASE_CLOSURE":
			assertReceipts(outcome.receipts, outcome.proof);
			if (state.claims[outcome.claimId]?.at(-1)?.role !== "CASE" || outcome.reason.trim().length === 0) throw new Error("INVARIANT_ERROR: case closure must identify an existing case and exact reason");
			break;
		case "FAILED_ROUTE": case "ROUTE_EXHAUSTED":
			if (outcome.failureKind !== undefined && outcome.failureKind !== "MATHEMATICAL_FAILURE") throw new Error("INVARIANT_ERROR: operational failures cannot become mathematical route failures");
			break;
		case "PARTIAL_PROGRESS": case "STRUCTURAL_DISCOVERY": case "VERIFIED_OBSERVATION": case "NO_PROGRESS": case "BLOCKED":
			break;
	}
}

export function inspectResearchState(state: ResearchProjectState): ProjectInvariantResult {
	const violations: InvariantViolation[] = [];
	const contract = state.rootObjectiveContract;
	if (contract !== undefined) {
		if (state.rootClaimId !== contract.rootClaimId) violations.push(issue("ROOT_CONTRACT_CLAIM_MISMATCH", "Root contract claim id does not match project rootClaimId", contract.contractId));
		if (normalize(contract.statement) !== contract.normalizedStatement) violations.push(issue("ROOT_CONTRACT_STATEMENT_IDENTITY", "Root contract normalized statement is inconsistent", contract.contractId));
		if (contract.allowedAssumptions.some((item) => item.trim().length === 0) || new Set(contract.allowedAssumptions.map(normalize)).size !== contract.allowedAssumptions.length) violations.push(issue("ROOT_CONTRACT_ASSUMPTIONS_INVALID", "Root contract assumptions must be nonempty and canonically unique", contract.contractId));
		const root = state.claims[contract.rootClaimId]?.at(-1); if (root !== undefined && normalize(root.statement) !== contract.normalizedStatement) violations.push(issue("ROOT_STATEMENT_MISMATCH", "Current root statement differs from immutable root contract", root.claimId));
	}
	for (const [claimId, revisions] of Object.entries(state.claims)) {
		for (let index = 0; index < revisions.length; index += 1) {
			const claim = revisions[index] as ClaimSnapshot;
			if (claim.claimId !== claimId || claim.revision !== index + 1) violations.push(issue("CLAIM_REVISION_IDENTITY", `Claim revision identity is invalid at revision ${claim.revision}`, claimId));
			if (claim.dependencies.includes(claimId)) violations.push(issue("SELF_DEPENDENCY", "Claim depends on itself", claimId));
			for (const dependency of claim.dependencies) if (state.claims[dependency] === undefined) violations.push(issue("MISSING_DEPENDENCY", `Dependency is missing: ${dependency}`, claimId));
		}
		const latest = revisions.at(-1);
		if (latest !== undefined && isAuthoritativeStatus(latest.status)) {
			const authority = activeAuthorityForClaim(state, claimId, latest.revision);
			if (authority === undefined) violations.push(issue("MISSING_AUTHORITY_RECEIPT", `Authoritative claim revision has no authority receipt`, claimId));
			else if (!sameStringSet(authority.assumptions, latest.assumptions)) violations.push(issue("ASSUMPTION_AUTHORITY_MISMATCH", "Claim assumptions do not match canonical authority", claimId));
		}
		if (latest?.status === "REDUCED" && latest.dependencies.length === 0) violations.push(issue("EMPTY_REDUCTION", "Authoritative reduced claim has no children", claimId));
	}
	for (const coverage of Object.values(state.coverage)) {
		if (state.claims[coverage.parentClaimId] === undefined) violations.push(issue("COVERAGE_PARENT_MISSING", "Coverage parent is missing", coverage.coverageId));
		if (coverage.scope.trim().length === 0 || coverage.coverageAssertion.trim().length === 0) violations.push(issue("COVERAGE_ASSERTION_MISSING", "Coverage requires exact scope and an explicit exhaustiveness assertion", coverage.coverageId));
		if (coverage.childClaimIds.length < 2) violations.push(issue("EMPTY_CASE_SPLIT", "Coverage requires at least two cases", coverage.coverageId));
		if (new Set(coverage.childClaimIds).size !== coverage.childClaimIds.length) violations.push(issue("COVERAGE_DUPLICATE_CHILD", "Coverage children must be unique", coverage.coverageId));
		if (coverage.childClaimIds.includes(coverage.parentClaimId)) violations.push(issue("COVERAGE_SELF_CHILD", "Coverage contains its parent", coverage.coverageId));
		for (const child of coverage.childClaimIds) if (state.claims[child] === undefined) violations.push(issue("COVERAGE_CHILD_MISSING", `Coverage child is missing: ${child}`, coverage.coverageId));
	}
	for (const [effectId, effect] of Object.entries(state.acceptedEffects)) {
		if (effect.effectId !== effectId) violations.push(issue("EFFECT_IDENTITY", "AcceptedEffect map key does not match effect identity", effectId));
		if (!state.events.some((event) => event.effectId === effectId && event.eventId === effect.eventId)) violations.push(issue("EFFECT_EVENT_MISSING", "AcceptedEffect lacks its canonical event", effectId));
	}
	for (const authority of Object.values(state.authorityReceipts)) validateAuthorityRecord(state, authority, violations);
	for (const route of Object.values(state.routes)) if (!routePredicateWellFormed(route.reopenPredicate)) violations.push(issue("MALFORMED_ROUTE_PREDICATE", "Route reopen predicate is malformed", route.routeId));
	return { valid: violations.length === 0, checkedAt: new Date().toISOString(), violations };
}

export class ResearchInvariantValidator {
	constructor(private readonly store: ResearchStore) {}
	async check(projectId: string): Promise<ProjectInvariantResult> {
		const state = await this.store.read(projectId), base = inspectResearchState(state), violations = [...base.violations];
		for (const artifact of Object.values(state.artifacts)) try { await this.store.resolveArtifact(projectId, artifact); } catch (error) { violations.push(issue("ARTIFACT_RESOLUTION", String(error), artifact.artifactId)); }
		for (const authority of Object.values(state.authorityReceipts)) {
			try { await this.store.resolveArtifact(projectId, authority.artifact); } catch (error) { violations.push(issue("AUTHORITY_ARTIFACT_RESOLUTION", String(error), authority.authorityReceiptId)); }
			try { await this.store.resolveArtifact(projectId, authority.sourceArtifact); } catch (error) { violations.push(issue("AUTHORITY_SOURCE_ARTIFACT_RESOLUTION", String(error), authority.authorityReceiptId)); }
			for (const ref of authority.evidenceRefs) try { await this.store.resolveArtifact(projectId, ref); } catch (error) { violations.push(issue("AUTHORITY_EVIDENCE_RESOLUTION", String(error), authority.authorityReceiptId)); }
		}
		return { valid: violations.length === 0, checkedAt: new Date().toISOString(), violations };
	}
}

export function authorityForClaim(state: ResearchProjectState, claimId: string, revision?: number): AuthorityReceipt | undefined {
	const selected = Object.values(state.authorityReceipts).filter((item) => item.claimId === claimId && (revision === undefined || item.claimRevision === revision));
	return selected.sort((a, b) => b.claimRevision - a.claimRevision || b.createdAt.localeCompare(a.createdAt))[0];
}

/** Current authority only; historical receipts remain resolvable through authorityForClaim. */
export function activeAuthorityForClaim(state: ResearchProjectState, claimId: string, revision?: number): AuthorityReceipt | undefined {
	const claim = state.claims[claimId]?.at(-1);
	if (claim === undefined || !isAuthoritativeStatus(claim.status) || (revision !== undefined && claim.revision !== revision)) return undefined;
	const authority = authorityForClaim(state, claimId, claim.revision);
	if (authority === undefined || state.authorityValidation[authority.authorityReceiptId]?.status !== "ACTIVE") return undefined;
	if (state.artifacts[authority.artifact.artifactId]?.contentHash !== authority.artifact.contentHash) return undefined;
	if (authority.trustReceiptIds.some((id) => state.trustReceipts[id] === undefined || state.trustReceipts[id]?.stale)) return undefined;
	return authority;
}

export function unresolvedAssumptions(state: ResearchProjectState, claims: readonly ClaimSnapshot[]): string[] {
	const all = [...new Set(claims.flatMap((claim) => claim.assumptions).map((item) => item.trim()).filter(Boolean))];
	return all.filter((assumption) => assessAssumption(state, assumption).status !== "DISCHARGED");
}

export function isAssumptionDischarged(state: ResearchProjectState, assumption: string): boolean {
	return assessAssumption(state, assumption).status === "DISCHARGED";
}

export function isAssumptionDischargeDependencyActive(state: ResearchProjectState, dependency: AssumptionDischargeDependency): boolean {
	return dischargeActive(state, dependency, new Set<string>());
}

export function assessAssumption(state: ResearchProjectState, assumption: string): AssumptionAssessment {
	const exact = assumption.trim(), normalizedAssumption = normalize(exact), witnesses: AssumptionDischargeWitness[] = [], previousWitnesses: AssumptionDischargeWitness[] = [];
	for (const [claimId, revisions] of Object.entries(state.claims)) {
		for (const claim of revisions) {
			if (normalize(claim.statement) !== normalizedAssumption) continue;
			const authority = authorityForClaim(state, claimId, claim.revision); if (authority === undefined) continue;
			const witness = witnessFor(exact, authority); previousWitnesses.push(witness);
			if (revisions.at(-1)?.revision === claim.revision && activeAuthorityForClaim(state, claimId, claim.revision)?.authorityReceiptId === authority.authorityReceiptId && authorityUnconditional(state, authority, new Set<string>())) witnesses.push(witness);
		}
	}
	if (witnesses.length > 0) return { status: "DISCHARGED", assumption: exact, normalizedAssumption, witnesses: uniqueBy(witnesses, (item) => item.authorityReceiptId) };
	return { status: "UNRESOLVED", assumption: exact, normalizedAssumption, previousWitnesses: uniqueBy(previousWitnesses, (item) => item.authorityReceiptId), reason: previousWitnesses.length === 0 ? "NO_AUTHORITATIVE_WITNESS" : "WITNESS_STALE" };
}

export function evaluateRouteReopenPredicate(state: ResearchProjectState, route: ResearchRoute, actor: RouteReopenActor = "SYSTEM"): { readonly satisfied: boolean; readonly reason?: string; readonly evidence?: string } {
	const predicate = route.reopenPredicate;
	if (predicate === undefined) return { satisfied: false };
	switch (predicate.type) {
		case "CLAIM_PROVED": case "CLAIM_REFUTED": {
			const expected = predicate.type === "CLAIM_PROVED" ? "PROVED" : "REFUTED", claim = state.claims[predicate.claimId]?.at(-1);
			return claim?.status === expected && activeAuthorityForClaim(state, predicate.claimId, claim.revision) !== undefined ? { satisfied: true, reason: `${predicate.type}(${predicate.claimId})`, evidence: `claim-revision:${claim.revision}` } : { satisfied: false };
		}
		case "NEW_EVIDENCE_FOR": {
			const claim = state.claims[predicate.claimId]?.at(-1), prior = new Set(route.artifactRefs.map(refKey));
			const evidence = claim?.evidenceRefs.find((ref) => !prior.has(refKey(ref)) && (state.artifacts[ref.artifactId]?.createdAt ?? "") >= route.updatedAt);
			return evidence === undefined ? { satisfied: false } : { satisfied: true, reason: `NEW_EVIDENCE_FOR(${predicate.claimId})`, evidence: evidence.artifactId };
		}
		case "PARAMETER_DOMAIN_REDUCED": {
			const claim = state.claims[predicate.domainId]?.at(-1), event = state.events.find((item) => item.type === "research/parameter_domain_reduced" && item.detail.domainId === predicate.domainId);
			return claim?.status === "REDUCED" || event !== undefined ? { satisfied: true, reason: `PARAMETER_DOMAIN_REDUCED(${predicate.domainId})`, evidence: event?.eventId ?? `claim-revision:${claim?.revision}` } : { satisfied: false };
		}
		case "LITERATURE_AVAILABLE": {
			const artifact = Object.values(state.artifacts).find((item) => item.artifactType === "LITERATURE_SOURCE" && item.metadata?.authorityTier === predicate.sourceClass && item.createdAt >= route.updatedAt);
			return artifact === undefined ? { satisfied: false } : { satisfied: true, reason: `LITERATURE_AVAILABLE(${predicate.sourceClass})`, evidence: artifact.artifactId };
		}
		case "ASSUMPTION_CHANGED": {
			const event = state.events.find((item) => item.type === "research/assumption_changed" && item.detail.assumption === predicate.assumption && (predicate.value === undefined || item.detail.value === predicate.value));
			return event === undefined ? { satisfied: false } : { satisfied: true, reason: `ASSUMPTION_CHANGED(${predicate.assumption})`, evidence: event.eventId };
		}
		case "MANUAL_REOPEN": return actor === "OPERATOR" ? { satisfied: true, reason: "MANUAL_REOPEN", evidence: "authorized-operator-api" } : { satisfied: false };
	}
}

export function isStructuralResearchOutcome(outcome: ResearchOutcome): boolean {
	return outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA" || outcome.type === "REFUTED_CLAIM" || outcome.type === "REDUCTION" || outcome.type === "CASE_SPLIT" || outcome.type === "CASE_CLOSURE";
}

export function desiredContributionKind(action: string): ResearchContributionKind | undefined {
	if (action === "REQUEST_REDUCTION") return "REDUCTION";
	if (action === "REQUEST_COUNTEREXAMPLE") return "COUNTEREXAMPLE";
	if (action === "SPLIT_OBLIGATION") return "CASE_SPLIT";
	if (action === "RUN_STRUCTURAL_PROBE") return "STRUCTURAL_OBSERVATION";
	if (action === "REQUEST_LITERATURE") return "LITERATURE_APPLICATION";
	return undefined;
}

function assertDecomposition(state: ResearchProjectState, parent: string, children: readonly { readonly claimId: string; readonly statement: string }[], minimum: number, label: string): void {
	if (state.claims[parent]?.at(-1) === undefined) throw new Error(`INVARIANT_ERROR: ${label} parent is missing: ${parent}`);
	if (children.length < minimum) throw new Error(`INVARIANT_ERROR: ${label} requires at least ${minimum} child claim(s)`);
	if (new Set(children.map((item) => item.claimId)).size !== children.length) throw new Error(`INVARIANT_ERROR: ${label} children must be unique`);
	if (children.some((item) => item.claimId === parent)) throw new Error(`INVARIANT_ERROR: ${label} cannot contain its parent`);
	if (children.some((item) => item.claimId.trim().length === 0 || item.statement.trim().length === 0)) throw new Error(`INVARIANT_ERROR: ${label} children must have nonempty ids and statements`);
}

function assertReceipts(receipts: readonly TrustReceipt[], candidate: ArtifactRef): void {
	if (receipts.length === 0) throw new Error("INVARIANT_ERROR: authoritative mathematical effects require verifier receipts");
	for (const receipt of receipts) assertReceipt(receipt, candidate);
}

function assertReceipt(receipt: TrustReceipt, candidate: ArtifactRef): void {
	if (receipt.verdict !== "CORRECT" || receipt.stale || !receipt.independentContext) throw new Error("INVARIANT_ERROR: receipt is not independently authoritative");
	if (refKey(receipt.candidate) !== refKey(candidate)) throw new Error("INVARIANT_ERROR: receipt refers to a different candidate body");
}

function validateAuthorityRecord(state: ResearchProjectState, authority: AuthorityReceipt, violations: InvariantViolation[]): void {
	const effect = state.acceptedEffects[authority.effectId], claim = state.claims[authority.claimId]?.find((item) => item.revision === authority.claimRevision), event = state.events.find((item) => item.effectId === authority.effectId);
	if (effect === undefined) violations.push(issue("AUTHORITY_EFFECT_MISSING", "Authority receipt lacks its AcceptedEffect", authority.authorityReceiptId));
	if (claim === undefined) violations.push(issue("AUTHORITY_CLAIM_REVISION_MISSING", "Authority receipt claim revision is missing", authority.authorityReceiptId));
	if (effect !== undefined && effect.outcomeType !== authority.effectKind) violations.push(issue("AUTHORITY_EFFECT_KIND_MISMATCH", "Authority receipt effect kind differs from AcceptedEffect", authority.authorityReceiptId));
	if (event === undefined || event?.eventId !== effect?.eventId) violations.push(issue("AUTHORITY_EVENT_LINEAGE_MISMATCH", "Authority receipt lacks the canonical AcceptedEffect event lineage", authority.authorityReceiptId));
	if (claim !== undefined && authority.statement !== claim.statement) violations.push(issue("AUTHORITY_STATEMENT_MISMATCH", "Authority receipt statement differs from the exact claim revision", authority.authorityReceiptId));
	if (claim !== undefined && !sameStringSet(authority.assumptions, claim.assumptions)) violations.push(issue("AUTHORITY_ASSUMPTION_MISMATCH", "Authority receipt assumptions differ from the exact claim revision", authority.authorityReceiptId));
	if (claim !== undefined && !sameStringSet(authority.dependencies, claim.dependencies)) violations.push(issue("AUTHORITY_DEPENDENCY_MISMATCH", "Authority receipt dependencies differ from the exact claim revision", authority.authorityReceiptId));
	if (claim !== undefined && state.authorityValidation[authority.authorityReceiptId]?.status === "ACTIVE") for (const dependency of claim.dependencies) {
		const dependencyRevision = authority.dependencyRevisions[dependency];
		if (dependencyRevision === undefined || state.claims[dependency]?.some((item) => item.revision === dependencyRevision) !== true) violations.push(issue("AUTHORITY_DEPENDENCY_REVISION_MISSING", `Authority dependency revision is missing or invalid: ${dependency}`, authority.authorityReceiptId));
		else {
			const exactDependencyAuthority = authorityForClaim(state, dependency, dependencyRevision);
			if (exactDependencyAuthority !== undefined && activeAuthorityForClaim(state, dependency, dependencyRevision)?.authorityReceiptId !== exactDependencyAuthority.authorityReceiptId) violations.push(issue("ACTIVE_AUTHORITY_DEPENDENCY_STALE", `An active authority references a non-active exact dependency revision: ${dependency}@${dependencyRevision}`, authority.authorityReceiptId));
		}
	}
	if (claim !== undefined && !claim.evidenceRefs.some((ref) => refKey(ref) === refKey(authority.artifact))) violations.push(issue("AUTHORITY_CANDIDATE_MISMATCH", "Authority artifact is not the candidate/proof of the exact claim revision", authority.authorityReceiptId));
	if (state.artifacts[authority.artifact.artifactId]?.contentHash !== authority.artifact.contentHash) violations.push(issue("AUTHORITY_ARTIFACT_MISSING", "Authority artifact metadata/hash is missing", authority.authorityReceiptId));
	const promoted = state.artifacts[authority.artifact.artifactId]; if (promoted !== undefined && refKey(authority.artifact) !== refKey(authority.sourceArtifact) && !promoted.references.some((ref) => refKey(ref) === refKey(authority.sourceArtifact))) violations.push(issue("AUTHORITY_SOURCE_LINEAGE_MISMATCH", "Promoted authority artifact does not reference its source candidate", authority.authorityReceiptId));
	for (const receiptId of authority.trustReceiptIds) { const receipt = state.trustReceipts[receiptId]; if (receipt === undefined) violations.push(issue("AUTHORITY_TRUST_RECEIPT_MISSING", `Authority trust receipt is missing: ${receiptId}`, authority.authorityReceiptId)); else if (refKey(receipt.candidate) !== refKey(authority.artifact)) violations.push(issue("AUTHORITY_VERIFICATION_CANDIDATE_MISMATCH", `Trust receipt ${receiptId} verifies a different candidate`, authority.authorityReceiptId)); }
	for (const discharge of authority.assumptionDischarges) validateDischargeRecord(state, authority, discharge, violations);
	const validation = state.authorityValidation[authority.authorityReceiptId];
	if (validation === undefined) violations.push(issue("AUTHORITY_VALIDATION_MISSING", "Authority receipt lacks an active/historical validation record", authority.authorityReceiptId));
	else if (validation.authorityReceiptId !== authority.authorityReceiptId) violations.push(issue("AUTHORITY_VALIDATION_IDENTITY", "Authority validation key does not match receipt", authority.authorityReceiptId));
	else if (validation.status === "ACTIVE" && authority.assumptionDischarges.some((item) => !dischargeActive(state, item, new Set([authority.authorityReceiptId])))) violations.push(issue("ACTIVE_DISCHARGE_AUTHORITY_STALE", "An active authority has a stale assumption-discharge dependency", authority.authorityReceiptId));
	if (event !== undefined && authority.scope !== undefined) { const eventScope = typeof event.detail.scope === "string" ? event.detail.scope : typeof event.detail.targetScope === "string" ? event.detail.targetScope : undefined; if (eventScope !== undefined && eventScope !== authority.scope) violations.push(issue("AUTHORITY_SCOPE_MISMATCH", "Authority receipt scope differs from its canonical event", authority.authorityReceiptId)); }
}

function validateDischargeRecord(state: ResearchProjectState, dependent: AuthorityReceipt, discharge: AssumptionDischargeDependency, violations: InvariantViolation[]): void {
	const witness = state.authorityReceipts[discharge.witnessAuthorityReceiptId], witnessClaim = state.claims[discharge.witnessClaimId]?.find((item) => item.revision === discharge.witnessClaimRevision), dependentClaim = state.claims[discharge.dependentClaimId]?.find((item) => item.revision === discharge.dependentClaimRevision);
	if (dependentClaim === undefined) violations.push(issue("DISCHARGE_DEPENDENT_REVISION_MISSING", "Discharge dependent claim revision is missing", dependent.authorityReceiptId));
	if (witness === undefined) violations.push(issue("DISCHARGE_WITNESS_AUTHORITY_MISSING", "Discharge witness authority receipt is missing", dependent.authorityReceiptId));
	else {
		if (witness.claimId !== discharge.witnessClaimId || witness.claimRevision !== discharge.witnessClaimRevision) violations.push(issue("DISCHARGE_WITNESS_REVISION_MISMATCH", "Discharge witness authority has the wrong exact claim revision", dependent.authorityReceiptId));
		if (witness.effectId !== discharge.acceptedEffectId || witness.artifact.artifactId !== discharge.witnessArtifactId || witness.artifact.contentHash !== discharge.witnessArtifactHash) violations.push(issue("DISCHARGE_WITNESS_LINEAGE_MISMATCH", "Discharge witness effect/artifact lineage is inconsistent", dependent.authorityReceiptId));
	}
	if (witnessClaim === undefined || normalize(witnessClaim.statement) !== discharge.normalizedAssumption || normalize(discharge.assumption) !== discharge.normalizedAssumption) violations.push(issue("DISCHARGE_PROPOSITION_MISMATCH", "Discharge witness does not prove the exact normalized assumption", dependent.authorityReceiptId));
}

function authorityUnconditional(state: ResearchProjectState, authority: AuthorityReceipt, visiting: Set<string>): boolean {
	if (visiting.has(authority.authorityReceiptId)) return false;
	if (authority.assumptions.length === 0) return true;
	const next = new Set(visiting); next.add(authority.authorityReceiptId);
	return authority.assumptions.every((assumption) => authority.assumptionDischarges.some((item) => item.dependentClaimId === authority.claimId && item.dependentClaimRevision === authority.claimRevision && item.normalizedAssumption === normalize(assumption) && dischargeActive(state, item, next)));
}

function dischargeActive(state: ResearchProjectState, discharge: AssumptionDischargeDependency, visiting: Set<string>): boolean {
	const witness = state.authorityReceipts[discharge.witnessAuthorityReceiptId];
	if (witness === undefined || activeAuthorityForClaim(state, discharge.witnessClaimId, discharge.witnessClaimRevision)?.authorityReceiptId !== witness.authorityReceiptId) return false;
	if (witness.effectId !== discharge.acceptedEffectId || witness.artifact.artifactId !== discharge.witnessArtifactId || witness.artifact.contentHash !== discharge.witnessArtifactHash || normalize(witness.statement) !== discharge.normalizedAssumption) return false;
	return authorityUnconditional(state, witness, visiting);
}

function witnessFor(assumption: string, authority: AuthorityReceipt): AssumptionDischargeWitness {
	return { assumption, normalizedAssumption: normalize(assumption), claimId: authority.claimId, claimRevision: authority.claimRevision, authorityReceiptId: authority.authorityReceiptId, proofArtifactId: authority.artifact.artifactId, proofArtifactHash: authority.artifact.contentHash, acceptedEffectId: authority.effectId, statement: authority.statement, contentHash: authority.artifact.contentHash };
}

function routePredicateWellFormed(predicate: RouteReopenPredicate | undefined): boolean {
	if (predicate === undefined || predicate.type === "MANUAL_REOPEN") return true;
	if (predicate.type === "CLAIM_PROVED" || predicate.type === "CLAIM_REFUTED" || predicate.type === "NEW_EVIDENCE_FOR") return predicate.claimId.trim().length > 0;
	if (predicate.type === "PARAMETER_DOMAIN_REDUCED") return predicate.domainId.trim().length > 0;
	if (predicate.type === "LITERATURE_AVAILABLE") return predicate.sourceClass.trim().length > 0;
	return predicate.type === "ASSUMPTION_CHANGED" && predicate.assumption.trim().length > 0;
}

function isAuthoritativeStatus(status: ClaimSnapshot["status"]): boolean { return status === "PROVED" || status === "REFUTED" || status === "REDUCED"; }
function sameStringSet(left: readonly string[], right: readonly string[]): boolean { return [...new Set(left)].sort().join("\n") === [...new Set(right)].sort().join("\n"); }
function uniqueBy<T>(values: readonly T[], keyOf: (value: T) => string): T[] { const seen = new Set<string>(); return values.filter((value) => { const key = keyOf(value); if (seen.has(key)) return false; seen.add(key); return true; }); }
function refKey(ref: ArtifactRef): string { return `${ref.artifactId}:${ref.contentHash}`; }
function normalize(value: string): string { return value.trim().replace(/\s+/gu, " "); }
function issue(code: string, message: string, entityId?: string): InvariantViolation { return { code, message, ...(entityId === undefined ? {} : { entityId }) }; }
