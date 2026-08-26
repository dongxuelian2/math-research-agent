import { effectIdentity, stableId } from "./ids.js";
import {
	activeAuthorityForClaim, assessAssumption, assertResearchOutcomeInvariants, assertVerifiedContributionInvariants, authorityForClaim, evaluateRouteReopenPredicate,
	isAssumptionDischargeDependencyActive, isStructuralResearchOutcome, unresolvedAssumptions,
} from "./invariants.js";
import { ArtifactAuthorityError, ResearchStore } from "./store.js";
import type {
	AcceptedEffect, ArtifactRef, AssumptionDischargeDependency, AssumptionDischargeWitness, AuthorityReceipt, AuthorityValidationState, ClaimSnapshot, ResearchArtifact, ResearchEvent, ResearchObligation,
	ResearchOutcome, ResearchProjectState, ResearchRoute, TacticalResearchResult, TrustReceipt, VerifiedResearchContribution,
} from "./types.js";

export interface ApplyOutcomeRequest {
	readonly projectId: string;
	readonly cycleId: string;
	readonly logicalJobId: string;
	readonly effectSlot: string;
	readonly outcome: ResearchOutcome;
}

export type AuthorityFaultPoint = "after_validation" | "after_prepare" | "after_claim_transition" | "after_accepted_effect" | "after_authority_receipt" | "after_event";

/** Sole canonical mathematical state writer. */
export class ResearchStateReducer {
	constructor(private readonly store: ResearchStore, private readonly options: { readonly faultPoint?: AuthorityFaultPoint } = {}) {}

	async apply(request: ApplyOutcomeRequest): Promise<{ readonly applied: boolean; readonly effect: AcceptedEffect; readonly state: ResearchProjectState }> {
		const effectId = effectIdentity(request.projectId, request.cycleId, request.logicalJobId, request.effectSlot);
		const existingState = await this.store.read(request.projectId), existing = existingState.acceptedEffects[effectId];
		if (existing !== undefined) return { applied: false, effect: existing, state: existingState };
		assertRootContractCompatibility(existingState, request.outcome);
		assertResearchOutcomeInvariants(existingState, request.outcome);
		await this.validateReferences(existingState, request.outcome);
		this.inject("after_validation");
		const prepared = await this.prepareAuthority(existingState, withDerivedDischarges(existingState, request.outcome));
		this.inject("after_prepare");
		const eventId = stableId("event", effectId, "applied"), appliedAt = new Date().toISOString();
		const effect: AcceptedEffect = { effectId, logicalJobId: request.logicalJobId, effectSlot: request.effectSlot, outcomeType: request.outcome.type, appliedAt, eventId };
		const result = await this.store.transaction(request.projectId, (draft) => {
			if (draft.acceptedEffects[effectId] !== undefined) return false;
			const mutable = draft as MutableState;
			if (prepared.promoted !== undefined) mutable.artifacts = { ...draft.artifacts, [prepared.promoted.artifactId]: draft.artifacts[prepared.promoted.artifactId] ?? prepared.promoted };
			applyValidated(mutable, prepared.outcome);
			this.inject("after_claim_transition");
			mutable.acceptedEffects = { ...mutable.acceptedEffects, [effectId]: effect };
			this.inject("after_accepted_effect");
			const authority = authorityReceiptFor(mutable, effect, prepared.outcome, prepared.source);
			if (authority !== undefined) {
				mutable.authorityReceipts = { ...mutable.authorityReceipts, [authority.authorityReceiptId]: authority };
				activateAuthority(mutable, authority);
			}
			// Authority-dependent predicates must be evaluated only after the canonical
			// receipt exists, but still inside the same committed transaction.
			reopenSatisfiedRoutes(mutable);
			this.inject("after_authority_receipt");
			const event: ResearchEvent = { eventId, type: eventType(prepared.outcome), projectId: request.projectId, timestamp: appliedAt, effectId, detail: outcomeDetail(prepared.outcome) };
			mutable.events = [...mutable.events, event];
			reconcileProjectTruthState(mutable);
			this.inject("after_event");
			return true;
		});
		return { applied: result.result, effect: result.state.acceptedEffects[effectId] ?? effect, state: result.state };
	}

	/** Validate the tactical truth gate, then translate verified protocol objects into deterministic effects. */
	async applyTactical(request: { readonly projectId: string; readonly cycleId: string; readonly logicalJobId: string; readonly obligationId: string; readonly targetClaimId: string; readonly result: TacticalResearchResult }): Promise<ResearchProjectState> {
		const result = request.result, initial = await this.store.read(request.projectId);
		if (result.obligationId !== request.obligationId || result.targetClaimId !== request.targetClaimId) throw new Error("Tactical result identity does not match the active obligation");
		if (result.targetStatus === "TARGET_PROVED") {
			const submission = result.targetSubmission;
			if (submission === undefined || submission.scope !== "TARGET" || submission.targetObligationId !== request.obligationId || submission.targetClaimId !== request.targetClaimId) throw new Error("TARGET_PROVED requires an exact verified target submission");
			if (submission.primaryReceipt.verdict !== "CORRECT" || refKey(submission.primaryReceipt.candidate) !== refKey(submission.candidate)) throw new Error("TARGET_PROVED requires an exact independent primary receipt");
		} else if (result.targetSubmission !== undefined) throw new Error("Unresolved tactical result cannot carry an authoritative target submission");
		if (result.targetStatus === "TARGET_REFUTED" && !result.contributions.some((item) => item.kind === "COUNTEREXAMPLE" && (item.claimId ?? item.targetClaimId) === request.targetClaimId)) throw new Error("TARGET_REFUTED requires an exact verified counterexample contribution");
		for (const contribution of result.contributions) {
			if (contribution.targetObligationId !== request.obligationId || contribution.targetClaimId !== request.targetClaimId) throw new Error(`Contribution target mismatch: ${contribution.contributionId}`);
			assertVerifiedContributionInvariants(initial, contribution);
			await this.store.resolveArtifact(request.projectId, contribution.candidate);
			for (const evidence of contribution.evidenceArtifacts) await this.store.resolveArtifact(request.projectId, evidence);
		}
		for (const observation of result.routeObservations) {
			if (observation.targetObligationId !== request.obligationId) throw new Error("Route observation target mismatch");
			for (const evidence of observation.evidence) await this.store.resolveArtifact(request.projectId, evidence);
		}
		for (const observation of result.routeObservations) if (observation.status !== "VIABLE") {
			const target = initial.claims[request.targetClaimId]?.at(-1);
			await this.apply({ projectId: request.projectId, cycleId: request.cycleId, logicalJobId: request.logicalJobId, effectSlot: `route:${observation.observationId}`, outcome: { type: observation.status === "EXHAUSTED" ? "ROUTE_EXHAUSTED" : "FAILED_ROUTE", obligationId: observation.targetObligationId, family: observation.routeFamily, mechanism: observation.mechanism, strategy: observation.strategy, assumptions: target?.assumptions ?? [], dependencySnapshot: target?.dependencies ?? [], failureMechanism: observation.failureMechanism ?? observation.status, ...(observation.failureDomain === undefined ? {} : { failureDomain: observation.failureDomain }), evidence: observation.evidence, attemptId: result.executionReceipt.attemptId, ...(observation.reopenPredicate === undefined ? {} : { reopenPredicate: observation.reopenPredicate }), failureKind: "MATHEMATICAL_FAILURE" } });
		}
		for (const contribution of result.contributions) {
			const outcome = this.outcomeForContribution(request.projectId, contribution), compatibility = rootContractCompatibility(initial, outcome);
			if (compatibility === undefined) await this.apply({ projectId: request.projectId, cycleId: request.cycleId, logicalJobId: request.logicalJobId, effectSlot: `contribution:${contribution.contributionId}`, outcome });
			else {
				await this.apply({ projectId: request.projectId, cycleId: request.cycleId, logicalJobId: request.logicalJobId, effectSlot: `conditional-contribution:${contribution.contributionId}`, outcome: conditionalSupportOutcome(request.projectId, contribution) });
				await this.recordRootContractBlocker(request.projectId, request.cycleId, request.logicalJobId, `contribution:${contribution.contributionId}`, compatibility, contribution.candidate);
			}
		}
		if (result.targetStatus === "TARGET_PROVED") {
			const submission = result.targetSubmission as NonNullable<TacticalResearchResult["targetSubmission"]>, claim = (await this.store.read(request.projectId)).claims[request.targetClaimId]?.at(-1);
			const outcome: ResearchOutcome = { type: "PROVED_CLAIM", claimId: request.targetClaimId, statement: submission.statement, candidate: submission.candidate, receipts: [submission.primaryReceipt, ...(submission.secondaryReceipt === undefined ? [] : [submission.secondaryReceipt])], dependencies: uniqueStrings([...(claim?.dependencies ?? []), ...submission.dependencies]), assumptions: uniqueStrings(submission.assumptions), scope: "TARGET" }, current = await this.store.read(request.projectId), compatibility = rootContractCompatibility(current, outcome);
			if (compatibility === undefined) await this.apply({ projectId: request.projectId, cycleId: request.cycleId, logicalJobId: request.logicalJobId, effectSlot: "target-submission", outcome });
			else {
				await this.apply({ projectId: request.projectId, cycleId: request.cycleId, logicalJobId: request.logicalJobId, effectSlot: "conditional-target-submission", outcome: { type: "NEW_LEMMA", claimId: stableId("claim", request.projectId, "conditional-target", submission.submissionId, submission.statement, ...submission.assumptions), statement: submission.statement, candidate: submission.candidate, receipts: outcome.receipts, dependencies: submission.dependencies, assumptions: submission.assumptions, scope: "CONDITIONAL_SUPPORT", supportsClaimId: request.targetClaimId, contributionId: submission.submissionId } });
				await this.recordRootContractBlocker(request.projectId, request.cycleId, request.logicalJobId, "target-submission", compatibility, submission.candidate);
			}
		}
		if (result.contributions.length === 0 && result.routeObservations.length === 0 && result.targetStatus !== "TARGET_PROVED") await this.apply({ projectId: request.projectId, cycleId: request.cycleId, logicalJobId: request.logicalJobId, effectSlot: "no-progress", outcome: { type: result.targetStatus === "EXECUTION_FAILED" ? "BLOCKED" : "NO_PROGRESS", reason: result.feedback, ...(result.executionReceipt.failureKind === undefined ? {} : { failureKind: result.executionReceipt.failureKind }) } });
		return this.store.read(request.projectId);
	}

	private outcomeForContribution(projectId: string, contribution: VerifiedResearchContribution): ResearchOutcome {
		const receipt = contribution.verification;
		switch (contribution.kind) {
			case "REDUCTION": return { type: "REDUCTION", claimId: contribution.targetClaimId, childClaims: contribution.childClaims ?? [], proof: contribution.candidate, receipts: [receipt], assumptions: contribution.assumptions, dependencies: contribution.dependencyClaims, scope: contribution.targetScope ?? contribution.relationshipToTarget };
			case "CASE_SPLIT": return { type: "CASE_SPLIT", claimId: contribution.targetClaimId, scope: contribution.coverageScope ?? "", coverageAssertion: contribution.coverageAssertion ?? contribution.relationshipToTarget, cases: contribution.childClaims ?? [], proof: contribution.candidate, receipts: [receipt], assumptions: contribution.assumptions, dependencies: contribution.dependencyClaims };
			case "CASE_CLOSURE": return { type: "CASE_CLOSURE", claimId: contribution.closedCaseClaimId ?? contribution.claimId ?? "", reason: contribution.closureReason ?? contribution.relationshipToTarget, proof: contribution.candidate, receipts: [receipt], assumptions: contribution.assumptions, dependencies: contribution.dependencyClaims };
			case "COUNTEREXAMPLE": return { type: "REFUTED_CLAIM", claimId: contribution.claimId ?? contribution.targetClaimId, counterexample: contribution.candidate, receipts: [receipt], assumptions: contribution.assumptions, dependencies: contribution.dependencyClaims, targetScope: contribution.targetScope ?? "", counterexampleScope: contribution.counterexampleScope ?? "" };
			case "STRUCTURAL_OBSERVATION": return { type: "VERIFIED_OBSERVATION", observation: contribution.candidate, statement: contribution.statement };
			case "LEMMA": case "CONSTRUCTION": case "BOUND": case "OBSTRUCTION": case "LITERATURE_APPLICATION": return { type: "NEW_LEMMA", claimId: contribution.claimId ?? stableId("claim", projectId, contribution.contributionId, contribution.statement), statement: contribution.statement, candidate: contribution.candidate, receipts: [receipt], dependencies: contribution.dependencyClaims, assumptions: contribution.assumptions, scope: contribution.targetScope, supportsClaimId: contribution.targetClaimId, contributionId: contribution.contributionId };
		}
	}

	private async recordRootContractBlocker(projectId: string, cycleId: string, logicalJobId: string, slot: string, violation: RootContractViolation, candidate: ArtifactRef): Promise<void> {
		await this.store.transaction(projectId, (draft) => { const mutable = draft as MutableState, eventId = stableId("event", projectId, cycleId, logicalJobId, slot, violation.code), timestamp = new Date().toISOString(); if (draft.events.some((item) => item.eventId === eventId)) return; mutable.events = [...draft.events, { eventId, type: "research/root_contract_blocked", projectId, timestamp, detail: { ...violation, logicalJobId, effectSlot: slot, candidate } }]; mutable.lastError = `${violation.code}: ${violation.message}`; if (draft.status === "PROVED") mutable.status = "PARTIAL"; });
	}

	async invalidate(projectId: string, claimId: string, reason: string): Promise<ResearchProjectState> {
		return (await this.store.transaction(projectId, (draft) => { invalidateClaimAuthorityInState(draft as MutableState, claimId, reason); })).state;
	}

	private async validateReferences(state: ResearchProjectState, outcome: ResearchOutcome): Promise<void> {
		const source = authoritativeSource(outcome);
		if (source !== undefined) await this.store.resolveArtifact(state.projectId, source);
		for (const receipt of outcomeReceipts(outcome)) {
			for (const evidence of receipt.evidenceInspected) await this.store.resolveArtifact(state.projectId, evidence);
			for (const evidence of receipt.workerDeclaredEvidence ?? receipt.workerReadEvidence ?? []) await this.store.resolveArtifact(state.projectId, evidence);
		}
		if (outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA") {
			if (state.rootClaimId === outcome.claimId && outcome.receipts.length < 2) throw new Error("Root promotion requires primary and fresh secondary audit receipts");
			const accepted = state.rootClaimId === outcome.claimId ? new Set((state.rootObjectiveContract?.allowedAssumptions ?? []).map(normalize)) : new Set<string>();
			for (const dependency of outcome.dependencies) if (!claimClosureReady(state, dependency, new Set([outcome.claimId]), accepted)) throw new Error(`Dependency closure is not authoritative: ${dependency}`);
		}
		if (outcome.type === "REFUTED_CLAIM" && state.rootClaimId === outcome.claimId && outcome.receipts.length < 2) throw new Error("Root refutation requires primary and fresh secondary audit receipts");
		if (outcome.type === "FAILED_ROUTE" || outcome.type === "ROUTE_EXHAUSTED") for (const evidence of outcome.evidence) await this.store.resolveArtifact(state.projectId, evidence);
		if (outcome.type === "PARTIAL_PROGRESS" || outcome.type === "STRUCTURAL_DISCOVERY" || outcome.type === "VERIFIED_OBSERVATION") await this.store.resolveArtifact(state.projectId, outcome.observation);
	}

	private async prepareAuthority(state: ResearchProjectState, outcome: ResearchOutcome): Promise<{ readonly outcome: ResearchOutcome; readonly promoted?: ResearchArtifact; readonly source?: ArtifactRef }> {
		const source = authoritativeSource(outcome);
		if (source === undefined) return { outcome };
		const resolved = await this.store.resolveArtifact(state.projectId, source);
		const promoted = await this.store.prepareArtifact(state.projectId, { artifactType: "PROMOTED_PROOF", body: resolved.body, provenance: `accepted-effect-promotion:${outcome.type}`, references: [source, ...resolved.artifact.references], ...(resolved.artifact.creationAttemptId === undefined ? {} : { creationAttemptId: resolved.artifact.creationAttemptId }) });
		return { outcome: replaceAuthoritativeSource(outcome, promoted), promoted, source };
	}

	private inject(point: AuthorityFaultPoint): void { if (this.options.faultPoint === point) throw new Error(`Injected authority fault: ${point}`); }
}

function applyValidated(state: MutableState, outcome: ResearchOutcome): void {
	const now = new Date().toISOString();
	switch (outcome.type) {
		case "PROVED_CLAIM": case "NEW_LEMMA": {
			const previous = latest(state, outcome.claimId), assumptions = outcome.type === "PROVED_CLAIM" && outcome.scope === "ROOT_SYNTHESIS" ? uniqueStrings(outcome.assumptions ?? []) : uniqueStrings([...(previous?.assumptions ?? []), ...(outcome.assumptions ?? [])]), dependencies = uniqueStrings([...(previous?.dependencies ?? []), ...outcome.dependencies]);
			appendClaim(state, { claimId: outcome.claimId, statement: outcome.statement, status: "PROVED", role: state.rootClaimId === outcome.claimId ? "ROOT" : previous?.role ?? "LEMMA", dependencies, evidenceRefs: [outcome.candidate], auditRefs: outcome.receipts.map((receipt) => receipt.candidate), assumptions, provenance: "verified-research-outcome" });
			storeTrustReceipts(state, outcome.receipts);
			if (outcome.type === "NEW_LEMMA" && outcome.supportsClaimId !== undefined) { const edgeId = stableId("support", state.projectId, outcome.claimId, outcome.supportsClaimId, outcome.contributionId ?? outcome.candidate.artifactId); if (!state.supportEdges.some((edge) => edge.edgeId === edgeId)) state.supportEdges = [...state.supportEdges, { edgeId, fromClaimId: outcome.claimId, toClaimId: outcome.supportsClaimId, contributionId: outcome.contributionId ?? outcome.candidate.artifactId, createdAt: now }]; }
			closeObligations(state, outcome.claimId); break;
		}
		case "REFUTED_CLAIM": {
			const previous = latest(state, outcome.claimId); appendClaim(state, { claimId: outcome.claimId, statement: previous?.statement ?? outcome.claimId, status: "REFUTED", role: previous?.role ?? "CONJECTURE", dependencies: uniqueStrings([...(previous?.dependencies ?? []), ...outcome.dependencies]), evidenceRefs: [outcome.counterexample], auditRefs: outcome.receipts.map((receipt) => receipt.candidate), assumptions: uniqueStrings([...(previous?.assumptions ?? []), ...outcome.assumptions]), provenance: `verified-counterexample:${outcome.counterexampleScope}->${outcome.targetScope}` }); storeTrustReceipts(state, outcome.receipts); closeObligations(state, outcome.claimId); break;
		}
		case "REDUCTION": {
			const parent = latest(state, outcome.claimId); if (parent === undefined) throw new Error(`Reduction parent missing: ${outcome.claimId}`);
			const dependencies = uniqueStrings([...outcome.childClaims.map((item) => item.claimId), ...outcome.dependencies]);
			for (const child of outcome.childClaims) createOpenClaimAndObligation(state, child.claimId, child.statement, "LEMMA", now);
			appendClaim(state, { ...parent, status: "REDUCED", dependencies, evidenceRefs: [outcome.proof], auditRefs: outcome.receipts.map((receipt) => receipt.candidate), assumptions: uniqueStrings([...parent.assumptions, ...outcome.assumptions]), provenance: "verified-reduction" });
			storeTrustReceipts(state, outcome.receipts); closeObligations(state, outcome.claimId); break;
		}
		case "CASE_SPLIT": {
			const parent = latest(state, outcome.claimId); if (parent === undefined) throw new Error(`Case-split parent missing: ${outcome.claimId}`);
			const dependencies = uniqueStrings([...outcome.cases.map((item) => item.claimId), ...outcome.dependencies]);
			for (const child of outcome.cases) createOpenClaimAndObligation(state, child.claimId, child.statement, "CASE", now);
			appendClaim(state, { ...parent, status: "REDUCED", dependencies, evidenceRefs: [outcome.proof], auditRefs: outcome.receipts.map((receipt) => receipt.candidate), assumptions: uniqueStrings([...parent.assumptions, ...outcome.assumptions]), provenance: "verified-case-split" });
			storeTrustReceipts(state, outcome.receipts);
			const coverageId = stableId("coverage", state.projectId, outcome.claimId, outcome.scope); state.coverage = { ...state.coverage, [coverageId]: { coverageId, parentClaimId: outcome.claimId, scope: outcome.scope, coverageAssertion: outcome.coverageAssertion, childClaimIds: outcome.cases.map((item) => item.claimId), disposition: "OPEN", provenanceArtifact: outcome.proof } }; closeObligations(state, outcome.claimId); break;
		}
		case "CASE_CLOSURE": {
			const claim = latest(state, outcome.claimId); if (claim === undefined) throw new Error(`Case closure target missing: ${outcome.claimId}`);
			appendClaim(state, { ...claim, status: "PROVED", dependencies: uniqueStrings([...claim.dependencies, ...outcome.dependencies]), evidenceRefs: [outcome.proof], auditRefs: outcome.receipts.map((receipt) => receipt.candidate), assumptions: uniqueStrings([...claim.assumptions, ...outcome.assumptions]), provenance: `verified-case-closure:${outcome.reason}` }); storeTrustReceipts(state, outcome.receipts); closeObligations(state, outcome.claimId); break;
		}
		case "FAILED_ROUTE": case "ROUTE_EXHAUSTED": {
			const routeId = stableId("route", state.projectId, outcome.obligationId, outcome.family, outcome.strategy), previous = state.routes[routeId];
			const route: ResearchRoute = { routeId, targetObligationId: outcome.obligationId, family: outcome.family, mechanism: outcome.mechanism, strategyDescription: outcome.strategy, assumptions: outcome.assumptions ?? [], dependencySnapshot: outcome.dependencySnapshot ?? [], artifactRefs: outcome.evidence, status: outcome.type === "ROUTE_EXHAUSTED" ? "EXHAUSTED" : "FAILED", attemptIds: uniqueStrings([...(previous?.attemptIds ?? []), ...(outcome.attemptId === undefined ? [] : [outcome.attemptId])]), failureMechanism: outcome.failureMechanism, ...(outcome.failureDomain === undefined ? {} : { failureDomain: outcome.failureDomain }), ...(outcome.reopenPredicate === undefined ? {} : { reopenPredicate: outcome.reopenPredicate }), createdAt: previous?.createdAt ?? now, updatedAt: now };
			state.routes = { ...state.routes, [routeId]: route }; break;
		}
		case "PARTIAL_PROGRESS": case "STRUCTURAL_DISCOVERY": case "VERIFIED_OBSERVATION": case "NO_PROGRESS": case "BLOCKED": break;
	}
	state.cyclesSinceStructuralProgress = isStructuralResearchOutcome(outcome) ? 0 : state.cyclesSinceStructuralProgress + 1;
	closeCoverage(state); reopenSatisfiedRoutes(state);
	state.status = state.rootClaimId !== undefined && latest(state, state.rootClaimId)?.status === "PROVED" ? "PROVED" : "RUNNING";
}

function authorityReceiptFor(state: ResearchProjectState, effect: AcceptedEffect, outcome: ResearchOutcome, source?: ArtifactRef): AuthorityReceipt | undefined {
	const artifact = authoritativeSource(outcome); if (artifact === undefined || source === undefined) return undefined;
	const claimId = authoritativeClaimId(outcome); if (claimId === undefined) return undefined;
	const claim = latest(state, claimId); if (claim === undefined) throw new Error("INVARIANT_ERROR: authority transition produced no claim revision");
	const receipts = outcomeReceipts(outcome), evidenceRefs = uniqueRefs(receipts.flatMap((receipt) => receipt.workerDeclaredEvidence ?? receipt.workerReadEvidence ?? []).concat(receipts.flatMap((receipt) => receipt.evidenceInspected)));
	const scope = outcomeScope(outcome), authorityReceiptId = stableId("authority", effect.effectId, claimId, String(claim.revision), artifact.contentHash), producerAttemptId = state.artifacts[artifact.artifactId]?.creationAttemptId;
	const assumptionDischarges = ("assumptionDischarges" in outcome ? outcome.assumptionDischarges : undefined) ?? [];
	return { authorityReceiptId, effectId: effect.effectId, effectKind: outcome.type as AuthorityReceipt["effectKind"], claimId, claimRevision: claim.revision, statement: claim.statement, artifact, sourceArtifact: source, ...(producerAttemptId === undefined ? {} : { producerAttemptId }), trustReceiptIds: receipts.map((item) => item.receiptId), evidenceRefs, assumptions: claim.assumptions, dependencies: claim.dependencies, dependencyRevisions: claim.dependencyRevisions ?? {}, assumptionDischarges, ...(scope === undefined ? {} : { scope }), createdAt: effect.appliedAt };
}

function appendClaim(state: MutableState, input: Omit<ClaimSnapshot, "revision" | "createdAt">): void { const revisions = state.claims[input.claimId] ?? [], dependencyRevisions = Object.fromEntries(input.dependencies.map((dependency) => [dependency, latest(state, dependency)?.revision]).filter((entry): entry is [string, number] => entry[1] !== undefined)); const snapshot: ClaimSnapshot = { ...input, assumptions: input.assumptions ?? [], dependencyRevisions, revision: (revisions.at(-1)?.revision ?? 0) + 1, createdAt: new Date().toISOString() }; state.claims = { ...state.claims, [input.claimId]: [...revisions, snapshot] }; }
function createOpenClaimAndObligation(state: MutableState, claimId: string, statement: string, role: ClaimSnapshot["role"], now: string): void { if (state.claims[claimId] === undefined) appendClaim(state, { claimId, statement, status: "OPEN", role, dependencies: [], evidenceRefs: [], auditRefs: [], assumptions: [], provenance: "validated-outcome" }); const obligationId = stableId("obligation", state.projectId, claimId, "research"); if (state.obligations[obligationId] === undefined) state.obligations = { ...state.obligations, [obligationId]: { obligationId, claimId, kind: "PROVE", statement, status: "OPEN", priority: 70, createdAt: now, updatedAt: now } }; }
function closeObligations(state: MutableState, claimId: string): void { const obligations = { ...state.obligations }; for (const [id, obligation] of Object.entries(obligations)) if (obligation.claimId === claimId) obligations[id] = { ...obligation, status: "CLOSED", updatedAt: new Date().toISOString() }; state.obligations = obligations; }
function closeCoverage(state: MutableState): void { const coverage = { ...state.coverage }; for (const [id, record] of Object.entries(coverage)) if (record.disposition === "OPEN" && record.childClaimIds.length >= 2 && new Set(record.childClaimIds).size === record.childClaimIds.length && record.childClaimIds.every((claimId) => latest(state, claimId)?.status === "PROVED" || latest(state, claimId)?.status === "REFUTED")) coverage[id] = { ...record, disposition: "CLOSED" }; state.coverage = coverage; }
function reopenSatisfiedRoutes(state: MutableState): void { const routes = { ...state.routes }; for (const [id, route] of Object.entries(routes)) { if (route.status !== "FAILED" && route.status !== "EXHAUSTED" && route.status !== "SUSPENDED") continue; const result = evaluateRouteReopenPredicate(state, route); if (result.satisfied) routes[id] = { ...route, status: "ACTIVE", reopenedBecause: `${result.reason ?? "predicate satisfied"}${result.evidence === undefined ? "" : ` via ${result.evidence}`}`, updatedAt: new Date().toISOString() }; } state.routes = routes; }
function storeTrustReceipts(state: MutableState, receipts: readonly TrustReceipt[]): void { const values = { ...state.trustReceipts }; for (const receipt of receipts) values[receipt.receiptId] = receipt; state.trustReceipts = values; }

export function researchFrontier(state: ResearchProjectState): readonly ResearchObligation[] { return Object.values(state.obligations).filter((item) => item.status === "OPEN" || item.status === "IN_PROGRESS").sort((a, b) => b.priority - a.priority || a.obligationId.localeCompare(b.obligationId)); }

export type RootReadinessDiagnosticCode = "ROOT_CONTRACT_NEEDS_REVALIDATION" | "ROOT_STATEMENT_MISMATCH" | "ROOT_ASSUMPTION_NOT_AUTHORIZED" | "ROOT_ASSUMPTION_UNRESOLVED" | "ASSUMPTION_DISCHARGE_STALE" | "AUTHORITY_DEPENDENCY_STALE" | "ROOT_NOT_CLOSED" | "INVALID_REDUCTION" | "INVALID_COVERAGE" | "STALE_AUTHORITY" | "FINAL_PROOF_STALE";
export interface RootReadinessDiagnostic { readonly code: RootReadinessDiagnosticCode; readonly message: string; readonly claimId?: string; readonly assumption?: string; readonly requiredBy?: readonly string[]; readonly entityId?: string; readonly previousWitness?: string; }
export interface RootAssumptionAssessment { readonly assumption: string; readonly normalizedAssumption: string; readonly requiredBy: readonly string[]; readonly status: "ALLOWED" | "DISCHARGED" | "UNRESOLVED"; readonly witnesses: readonly AssumptionDischargeWitness[]; readonly previousWitnesses: readonly AssumptionDischargeWitness[]; }
export interface RootSynthesisReadiness {
	readonly ready: boolean; readonly rootObjective?: string; readonly allowedAssumptions: readonly string[];
	readonly currentResearchRequiredAssumptions: readonly RootAssumptionAssessment[]; readonly closureRequiredAssumptions: readonly RootAssumptionAssessment[]; readonly unresolvedAssumptions: readonly string[];
	readonly diagnostics: readonly RootReadinessDiagnostic[]; readonly blockers: readonly string[];
}

export function rootSynthesisReadiness(state: ResearchProjectState): RootSynthesisReadiness {
	const diagnostics: RootReadinessDiagnostic[] = [], contract = state.rootObjectiveContract;
	if (contract === undefined || contract.status !== "VALID") diagnostics.push({ code: "ROOT_CONTRACT_NEEDS_REVALIDATION", message: contract === undefined ? "Root objective contract is not configured" : "Root objective contract requires explicit revalidation", ...(state.rootClaimId === undefined ? {} : { claimId: state.rootClaimId }) });
	const allowedAssumptions = contract?.allowedAssumptions ?? [], accepted = new Set(allowedAssumptions.map(normalize));
	let closure: ClaimSnapshot[] = [];
	if (state.rootClaimId === undefined) diagnostics.push({ code: "ROOT_NOT_CLOSED", message: "Root claim is not configured" });
	else {
		const root = latest(state, state.rootClaimId);
		if (root === undefined) diagnostics.push({ code: "ROOT_NOT_CLOSED", message: "Root claim snapshot is missing", claimId: state.rootClaimId });
		else {
			closure = authoritativeClaimClosure(state, root.claimId);
			if (contract !== undefined && (contract.rootClaimId !== root.claimId || contract.normalizedStatement !== normalize(root.statement) || contract.normalizedStatement !== normalize(contract.statement))) diagnostics.push({ code: "ROOT_STATEMENT_MISMATCH", message: "Current root statement does not match the immutable root objective contract", claimId: root.claimId });
			if (root.status !== "PROVED" && root.status !== "REDUCED") diagnostics.push({ code: "ROOT_NOT_CLOSED", message: `Root claim is neither proved nor verified-reduced: ${root.status}`, claimId: root.claimId });
			if ((root.status === "PROVED" || root.status === "REDUCED") && activeAuthorityForClaim(state, root.claimId, root.revision) === undefined) diagnostics.push({ code: "STALE_AUTHORITY", message: `Root claim lacks current canonical authority: ${root.claimId}@${root.revision}`, claimId: root.claimId });
			if (root.status === "INVALIDATED" || root.status === "NEEDS_REVALIDATION") { const previous = authorityForClaim(state, root.claimId); for (const discharge of previous?.assumptionDischarges ?? []) if (!isAssumptionDischargeDependencyActive(state, discharge)) diagnostics.push({ code: "ASSUMPTION_DISCHARGE_STALE", message: `Assumption discharge authority is stale: ${discharge.assumption}; previous witness ${discharge.witnessClaimId}@${discharge.witnessClaimRevision}`, assumption: discharge.assumption, claimId: discharge.dependentClaimId, requiredBy: [discharge.dependentClaimId], previousWitness: `${discharge.witnessClaimId}@${discharge.witnessClaimRevision}:${discharge.witnessAuthorityReceiptId}` }); }
			for (const dependency of root.dependencies) if (!claimClosureReady(state, dependency, new Set([root.claimId]), accepted)) {
				const dependent = latest(state, dependency), code: RootReadinessDiagnosticCode = dependent?.status === "REDUCED" && dependent.dependencies.length === 0 ? "INVALID_REDUCTION" : activeAuthorityForClaim(state, dependency, dependent?.revision) === undefined ? "AUTHORITY_DEPENDENCY_STALE" : "ROOT_NOT_CLOSED";
				diagnostics.push({ code, message: `Root dependency not closed: ${dependency} (${dependent?.status ?? "MISSING"})`, claimId: dependency });
			}
		}
	}
	const closureRequirements = assumptionAssessments(state, closure, accepted), supportClaimIds = state.rootClaimId === undefined ? [] : state.supportEdges.filter((edge) => edge.toClaimId === state.rootClaimId).map((edge) => edge.fromClaimId), researchClaims = uniqueBy([...closure, ...supportClaimIds.map((claimId) => latest(state, claimId)).filter((claim): claim is ClaimSnapshot => claim !== undefined)], (claim) => claim.claimId), researchRequirements = assumptionAssessments(state, researchClaims, accepted);
	for (const requirement of closureRequirements) if (requirement.status === "UNRESOLVED") { const stale = requirement.previousWitnesses.length > 0, previousWitness = requirement.previousWitnesses[0]; diagnostics.push({ code: stale ? "ASSUMPTION_DISCHARGE_STALE" : requirement.requiredBy.includes(state.rootClaimId ?? "") ? "ROOT_ASSUMPTION_NOT_AUTHORIZED" : "ROOT_ASSUMPTION_UNRESOLVED", message: stale ? `Assumption discharge authority is stale: ${requirement.assumption}; previous witness ${previousWitness?.claimId}@${previousWitness?.claimRevision}` : `Assumption is neither authorized by the root contract nor discharged: ${requirement.assumption}`, assumption: requirement.assumption, requiredBy: requirement.requiredBy, ...(requirement.requiredBy[0] === undefined ? {} : { claimId: requirement.requiredBy[0] }), ...(previousWitness === undefined ? {} : { previousWitness: `${previousWitness.claimId}@${previousWitness.claimRevision}:${previousWitness.authorityReceiptId}` }) }); }
	for (const coverage of Object.values(state.coverage)) {
		if (coverage.childClaimIds.length < 2 || new Set(coverage.childClaimIds).size !== coverage.childClaimIds.length) diagnostics.push({ code: "INVALID_COVERAGE", message: `Coverage structure invalid: ${coverage.coverageId}`, entityId: coverage.coverageId });
		if (coverage.disposition !== "CLOSED") diagnostics.push({ code: "INVALID_COVERAGE", message: `Coverage remains open: ${coverage.coverageId}`, entityId: coverage.coverageId });
	}
	for (const claim of closure) if (claim.status === "INVALIDATED" || claim.status === "NEEDS_REVALIDATION") diagnostics.push({ code: "STALE_AUTHORITY", message: `Claim authority invalid: ${claim.claimId}`, claimId: claim.claimId });
	const currentRoot = state.rootClaimId === undefined ? undefined : latest(state, state.rootClaimId); if (state.currentFinalProofAuthority?.status === "STALE" && (currentRoot === undefined || activeAuthorityForClaim(state, currentRoot.claimId, currentRoot.revision) === undefined)) diagnostics.push({ code: "FINAL_PROOF_STALE", message: `Previous final proof is historical/stale: ${state.currentFinalProofAuthority.artifact.artifactId}`, entityId: state.currentFinalProofAuthority.finalProofAuthorityId });
	const uniqueDiagnostics = uniqueBy(diagnostics, (item) => `${item.code}\n${item.claimId ?? ""}\n${item.assumption ?? ""}\n${item.entityId ?? ""}\n${item.message}`), blockers = uniqueDiagnostics.map((item) => `${item.code}: ${item.message}`);
	return { ready: blockers.length === 0, ...(contract === undefined ? {} : { rootObjective: contract.statement }), allowedAssumptions, currentResearchRequiredAssumptions: researchRequirements, closureRequiredAssumptions: closureRequirements, unresolvedAssumptions: closureRequirements.filter((item) => item.status === "UNRESOLVED").map((item) => item.assumption), diagnostics: uniqueDiagnostics, blockers };
}

function claimClosureReady(state: ResearchProjectState, claimId: string, visiting: Set<string>, acceptedAssumptions = new Set<string>()): boolean { if (visiting.has(claimId)) return false; const claim = latest(state, claimId); if (claim === undefined || activeAuthorityForClaim(state, claimId, claim.revision) === undefined) return false; if (!unresolvedAssumptions(state, [claim]).every((item) => acceptedAssumptions.has(normalize(item)))) return false; if (claim.status === "PROVED") return true; if (claim.status !== "REDUCED" || claim.dependencies.length === 0) return false; const next = new Set(visiting); next.add(claimId); const coverage = Object.values(state.coverage).filter((record) => record.parentClaimId === claimId); return claim.dependencies.every((dependency) => claimClosureReady(state, dependency, next, acceptedAssumptions)) && coverage.every((record) => record.childClaimIds.length >= 2 && record.disposition === "CLOSED"); }
export function authoritativeClaimClosure(state: ResearchProjectState, claimId: string): ClaimSnapshot[] { const result: ClaimSnapshot[] = [], seen = new Set<string>(); const visit = (id: string): void => { if (seen.has(id)) return; seen.add(id); const claim = latest(state, id); if (claim === undefined) return; for (const dependency of claim.dependencies) visit(dependency); for (const assumption of claim.assumptions) { const assessment = assessAssumption(state, assumption); if (assessment.status === "DISCHARGED") for (const witness of assessment.witnesses) visit(witness.claimId); } result.push(claim); }; visit(claimId); return result; }

interface RootContractViolation { readonly code: "ROOT_CONTRACT_NEEDS_REVALIDATION" | "ROOT_STATEMENT_MISMATCH" | "ROOT_ASSUMPTION_ESCALATION"; readonly message: string; readonly claimId: string; readonly assumptions?: readonly string[]; }
function rootContractCompatibility(state: ResearchProjectState, outcome: ResearchOutcome): RootContractViolation | undefined {
	const claimId = authoritativeClaimId(outcome); if (claimId === undefined || state.rootClaimId !== claimId) return undefined;
	const contract = state.rootObjectiveContract;
	if (contract === undefined || contract.status !== "VALID") return { code: "ROOT_CONTRACT_NEEDS_REVALIDATION", message: "Root authority is blocked until the immutable objective contract is explicitly valid", claimId };
	if ((outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA") && normalize(outcome.statement) !== contract.normalizedStatement) return { code: "ROOT_STATEMENT_MISMATCH", message: "Submitted theorem statement does not match the immutable root objective", claimId };
	const accepted = new Set(contract.allowedAssumptions.map(normalize)), unauthorized = outcomeAssumptions(outcome).filter((item) => !accepted.has(normalize(item)) && assessAssumption(state, item).status !== "DISCHARGED");
	return unauthorized.length === 0 ? undefined : { code: "ROOT_ASSUMPTION_ESCALATION", message: `Research result requires assumptions not authorized by the root contract: ${unauthorized.join("; ")}`, claimId, assumptions: unauthorized };
}
function assertRootContractCompatibility(state: ResearchProjectState, outcome: ResearchOutcome): void { const violation = rootContractCompatibility(state, outcome); if (violation !== undefined) throw new Error(`${violation.code}: ${violation.message}`); }
function outcomeAssumptions(outcome: ResearchOutcome): readonly string[] { return "assumptions" in outcome && Array.isArray(outcome.assumptions) ? outcome.assumptions : []; }
function conditionalSupportOutcome(projectId: string, contribution: VerifiedResearchContribution): ResearchOutcome {
	return { type: "NEW_LEMMA", claimId: contribution.claimId === contribution.targetClaimId || contribution.claimId === undefined ? stableId("claim", projectId, "conditional-support", contribution.contributionId, contribution.statement, ...contribution.assumptions) : contribution.claimId, statement: contribution.statement, candidate: contribution.candidate, receipts: [contribution.verification], dependencies: contribution.dependencyClaims, assumptions: contribution.assumptions, scope: "CONDITIONAL_SUPPORT", supportsClaimId: contribution.targetClaimId, contributionId: contribution.contributionId };
}
function assumptionAssessments(state: ResearchProjectState, claims: readonly ClaimSnapshot[], accepted: ReadonlySet<string>): RootAssumptionAssessment[] {
	const requirements = new Map<string, { assumption: string; requiredBy: Set<string> }>();
	for (const claim of claims) for (const assumption of claim.assumptions) { const exact = assumption.trim(), key = normalize(exact); if (key.length === 0) continue; const value = requirements.get(key) ?? { assumption: exact, requiredBy: new Set<string>() }; value.requiredBy.add(claim.claimId); requirements.set(key, value); }
	return [...requirements.entries()].map(([normalizedAssumption, value]) => { if (accepted.has(normalizedAssumption)) return { assumption: value.assumption, normalizedAssumption, requiredBy: [...value.requiredBy].sort(), status: "ALLOWED" as const, witnesses: [], previousWitnesses: [] }; const assessment = assessAssumption(state, value.assumption); return { assumption: value.assumption, normalizedAssumption, requiredBy: [...value.requiredBy].sort(), status: assessment.status === "DISCHARGED" ? "DISCHARGED" as const : "UNRESOLVED" as const, witnesses: assessment.status === "DISCHARGED" ? assessment.witnesses : [], previousWitnesses: assessment.status === "UNRESOLVED" ? assessment.previousWitnesses : [] }; }).sort((a, b) => a.normalizedAssumption.localeCompare(b.normalizedAssumption));
}

function withDerivedDischarges(state: ResearchProjectState, outcome: ResearchOutcome): ResearchOutcome {
	const claimId = authoritativeClaimId(outcome); if (claimId === undefined || !("assumptions" in outcome)) return outcome;
	const allowed = claimId === state.rootClaimId ? new Set((state.rootObjectiveContract?.allowedAssumptions ?? []).map(normalize)) : new Set<string>(), dependentClaimRevision = (latest(state, claimId)?.revision ?? 0) + 1, derived: AssumptionDischargeDependency[] = [];
	for (const assumption of outcomeAssumptions(outcome)) {
		if (allowed.has(normalize(assumption))) continue;
		const assessment = assessAssumption(state, assumption); if (assessment.status !== "DISCHARGED") continue;
		for (const witness of assessment.witnesses) derived.push({ assumption: assessment.assumption, normalizedAssumption: assessment.normalizedAssumption, dependentClaimId: claimId, dependentClaimRevision, witnessClaimId: witness.claimId, witnessClaimRevision: witness.claimRevision, witnessAuthorityReceiptId: witness.authorityReceiptId, witnessArtifactId: witness.proofArtifactId, witnessArtifactHash: witness.proofArtifactHash, acceptedEffectId: witness.acceptedEffectId, witnessStatement: witness.statement });
	}
	const supplied = "assumptionDischarges" in outcome ? outcome.assumptionDischarges ?? [] : [];
	return { ...outcome, assumptionDischarges: uniqueBy([...supplied, ...derived], dischargeKey) } as ResearchOutcome;
}

function activateAuthority(state: MutableState, authority: AuthorityReceipt): void {
	const changedAt = authority.createdAt, validation: Record<string, AuthorityValidationState> = { ...state.authorityValidation }, superseded: AuthorityReceipt[] = [];
	for (const previous of Object.values(state.authorityReceipts)) if (previous.authorityReceiptId !== authority.authorityReceiptId && previous.claimId === authority.claimId && validation[previous.authorityReceiptId]?.status === "ACTIVE") { validation[previous.authorityReceiptId] = { authorityReceiptId: previous.authorityReceiptId, status: "SUPERSEDED", reason: `Superseded by ${authority.authorityReceiptId}`, causalAuthorityReceiptIds: [authority.authorityReceiptId], changedAt }; superseded.push(previous); }
	validation[authority.authorityReceiptId] = { authorityReceiptId: authority.authorityReceiptId, status: "ACTIVE", causalAuthorityReceiptIds: authority.assumptionDischarges.map((item) => item.witnessAuthorityReceiptId), changedAt };
	state.authorityValidation = validation;
	if (superseded.length > 0) propagateAuthorityLossInState(state, [], superseded, `Exact authority revision superseded by ${authority.authorityReceiptId}`, { supersededAuthorityReceiptIds: superseded.map((item) => item.authorityReceiptId), newAuthorityReceiptId: authority.authorityReceiptId });
}

/** Mutates one canonical draft and follows exact ordinary and discharge authority edges. */
export function invalidateClaimAuthorityInState(state: MutableState, claimId: string, reason: string): readonly string[] {
	const starting = latest(state, claimId); if (starting === undefined) return [];
	if (starting.status !== "PROVED" && starting.status !== "REFUTED" && starting.status !== "REDUCED") { reconcileProjectTruthState(state, reason); return []; }
	const startingAuthority = activeAuthorityForClaim(state, claimId, starting.revision);
	return propagateAuthorityLossInState(state, [{ claimId, revision: starting.revision, ...(startingAuthority === undefined ? {} : { authorityReceiptId: startingAuthority.authorityReceiptId }) }], startingAuthority === undefined ? [] : [startingAuthority], reason);
}

interface DirectAuthorityLoss { readonly claimId: string; readonly revision: number; readonly authorityReceiptId?: string; }
interface SupersessionAudit { readonly supersededAuthorityReceiptIds: readonly string[]; readonly newAuthorityReceiptId: string; }
interface AffectedAuthority { readonly revision: number; readonly authorityReceiptId?: string; readonly direct: boolean; readonly causalAuthorityReceiptIds: readonly string[]; }

/** Shared exact-edge closure for explicit invalidation and revision supersession. */
function propagateAuthorityLossInState(state: MutableState, directLosses: readonly DirectAuthorityLoss[], lostAuthorities: readonly AuthorityReceipt[], reason: string, supersession?: SupersessionAudit): readonly string[] {
	const affected = new Map<string, AffectedAuthority>(), lost = new Map(lostAuthorities.map((authority) => [authority.authorityReceiptId, authority]));
	for (const direct of directLosses) affected.set(direct.claimId, { revision: direct.revision, ...(direct.authorityReceiptId === undefined ? {} : { authorityReceiptId: direct.authorityReceiptId }), direct: true, causalAuthorityReceiptIds: direct.authorityReceiptId === undefined ? [] : [direct.authorityReceiptId] });
	let expanded = true;
	while (expanded) {
		expanded = false;
		for (const [candidateId, revisions] of Object.entries(state.claims)) {
			if (affected.has(candidateId)) continue;
			const claim = revisions.at(-1); if (claim === undefined || (claim.status !== "PROVED" && claim.status !== "REFUTED" && claim.status !== "REDUCED")) continue;
			const authority = activeAuthorityForClaim(state, candidateId, claim.revision); if (authority === undefined) continue;
			const causes = supersession === undefined
				? [...affected.entries()].filter(([affectedId, detail]) => authority.dependencies.includes(affectedId) || authority.assumptionDischarges.some((edge) => edge.witnessAuthorityReceiptId === detail.authorityReceiptId)).flatMap(([, detail]) => detail.authorityReceiptId === undefined ? detail.causalAuthorityReceiptIds : [detail.authorityReceiptId])
				: [...lost.values()].filter((lostAuthority) => authority.dependencyRevisions[lostAuthority.claimId] === lostAuthority.claimRevision || authority.assumptionDischarges.some((edge) => edge.witnessAuthorityReceiptId === lostAuthority.authorityReceiptId)).map((item) => item.authorityReceiptId);
			if (causes.length === 0) continue;
			affected.set(candidateId, { revision: claim.revision, authorityReceiptId: authority.authorityReceiptId, direct: false, causalAuthorityReceiptIds: [...new Set(causes)] }); lost.set(authority.authorityReceiptId, authority); expanded = true;
		}
	}
	const claims = { ...state.claims }, obligations = { ...state.obligations }, validation: Record<string, AuthorityValidationState> = { ...state.authorityValidation }, now = new Date().toISOString();
	for (const [id, detail] of affected) {
		const revisions = claims[id], claim = revisions?.at(-1); if (revisions === undefined || claim === undefined) continue;
		if (detail.authorityReceiptId !== undefined) validation[detail.authorityReceiptId] = { authorityReceiptId: detail.authorityReceiptId, status: detail.direct ? "INVALIDATED" : "STALE", reason: detail.direct ? reason : `Exact authority dependency lost: ${reason}`, causalAuthorityReceiptIds: detail.causalAuthorityReceiptIds, changedAt: now };
		if (claim.status === "PROVED" || claim.status === "REFUTED" || claim.status === "REDUCED") claims[id] = [...revisions, { ...claim, revision: claim.revision + 1, status: detail.direct ? "INVALIDATED" : "NEEDS_REVALIDATION", invalidationReason: detail.direct ? reason : `Exact authority dependency lost: ${reason}`, createdAt: now }];
		for (const [obligationId, obligation] of Object.entries(obligations)) if (obligation.claimId === id) obligations[obligationId] = { ...obligation, status: "OPEN", updatedAt: now, causalReason: `Authority revoked: ${reason}` };
		if (!Object.values(obligations).some((item) => item.claimId === id)) { const obligationId = stableId("obligation", state.projectId, id, "authority-revocation"); obligations[obligationId] = { obligationId, claimId: id, kind: "AUDIT", statement: claim.statement, status: "OPEN", priority: 100, createdAt: now, updatedAt: now, causalReason: `Authority revoked: ${reason}` }; }
		const eventId = stableId("event", state.projectId, "authority-revoked", detail.authorityReceiptId ?? `${id}@${detail.revision}`); if (!state.events.some((item) => item.eventId === eventId)) state.events = [...state.events, { eventId, type: "research/authority_revoked", projectId: state.projectId, timestamp: now, detail: { claimId: id, claimRevision: detail.revision, authorityReceiptId: detail.authorityReceiptId, dependentAuthorityReceiptId: detail.direct ? undefined : detail.authorityReceiptId, status: detail.direct ? "INVALIDATED" : "STALE", reason, causalAuthorityReceiptIds: detail.causalAuthorityReceiptIds, ...(supersession === undefined ? {} : { supersededAuthorityReceiptIds: supersession.supersededAuthorityReceiptIds, newAuthorityReceiptId: supersession.newAuthorityReceiptId }) } }];
		const authority = detail.authorityReceiptId === undefined ? undefined : state.authorityReceipts[detail.authorityReceiptId]; if (authority?.assumptionDischarges.some((edge) => detail.causalAuthorityReceiptIds.includes(edge.witnessAuthorityReceiptId))) { const dischargeEventId = stableId("event", state.projectId, "assumption-discharge-invalidated", authority.authorityReceiptId, ...detail.causalAuthorityReceiptIds); if (!state.events.some((item) => item.eventId === dischargeEventId)) state.events = [...state.events, { eventId: dischargeEventId, type: "research/assumption_discharge_invalidated", projectId: state.projectId, timestamp: now, detail: { dependentAuthorityReceiptId: authority.authorityReceiptId, causalAuthorityReceiptIds: detail.causalAuthorityReceiptIds, discharges: authority.assumptionDischarges.filter((edge) => detail.causalAuthorityReceiptIds.includes(edge.witnessAuthorityReceiptId)) } }]; }
	}
	const affectedIds = new Set(affected.keys()), coverage = { ...state.coverage }; for (const [id, record] of Object.entries(coverage)) if (affectedIds.has(record.parentClaimId) || record.childClaimIds.some((child) => affectedIds.has(child))) coverage[id] = { ...record, disposition: "INVALIDATED" };
	state.claims = claims; state.obligations = obligations; state.coverage = coverage; state.authorityValidation = validation;
	reconcileProjectTruthState(state, reason, [...new Set([...affected.values()].flatMap((item) => item.authorityReceiptId === undefined ? [] : [item.authorityReceiptId]))]);
	return [...affected.keys()];
}

/** Central deterministic policy preventing sticky project/final-proof truth. */
export function reconcileProjectTruthState(state: MutableState, reason = "Current root authority is not valid", causalAuthorityReceiptIds: readonly string[] = []): void {
	const root = state.rootClaimId === undefined ? undefined : latest(state, state.rootClaimId), rootAuthority = root === undefined ? undefined : activeAuthorityForClaim(state, root.claimId, root.revision), contract = state.rootObjectiveContract;
	const ordinaryDependenciesValid = rootAuthority !== undefined && rootAuthority.dependencies.every((dependency) => activeAuthorityForClaim(state, dependency, rootAuthority.dependencyRevisions[dependency]) !== undefined);
	const dischargesValid = rootAuthority !== undefined && rootAuthority.assumptionDischarges.every((edge) => isAssumptionDischargeDependencyActive(state, edge));
	const allowed = new Set((contract?.allowedAssumptions ?? []).map(normalize)), assumptionsValid = rootAuthority !== undefined && rootAuthority.assumptions.every((assumption) => allowed.has(normalize(assumption)) || rootAuthority.assumptionDischarges.some((edge) => edge.dependentClaimId === rootAuthority.claimId && edge.dependentClaimRevision === rootAuthority.claimRevision && edge.normalizedAssumption === normalize(assumption) && isAssumptionDischargeDependencyActive(state, edge)));
	const rootValid = contract?.status === "VALID" && root !== undefined && root.status === "PROVED" && contract.rootClaimId === root.claimId && contract.normalizedStatement === normalize(root.statement) && rootAuthority !== undefined && ordinaryDependenciesValid && dischargesValid && assumptionsValid;
	const previouslyProved = state.status === "PROVED", hadCurrentFinal = state.currentFinalProofAuthority?.status === "ACTIVE";
	if (rootValid) state.status = "PROVED";
	else if (previouslyProved) state.status = "PARTIAL";
	const lostAuthority = !rootValid && (previouslyProved || hadCurrentFinal || root?.status === "INVALIDATED" || root?.status === "NEEDS_REVALIDATION"); if (!lostAuthority || root === undefined) return;
	const now = new Date().toISOString(), obligations = { ...state.obligations }; for (const [id, obligation] of Object.entries(obligations)) if (obligation.claimId === root.claimId) obligations[id] = { ...obligation, status: "OPEN", updatedAt: now, causalReason: `Root authority revoked: ${reason}` }; if (!Object.values(obligations).some((item) => item.claimId === root.claimId)) { const obligationId = stableId("obligation", state.projectId, root.claimId, "root-authority-revocation"); obligations[obligationId] = { obligationId, claimId: root.claimId, kind: "AUDIT", statement: root.statement, status: "OPEN", priority: 110, createdAt: now, updatedAt: now, causalReason: `Root authority revoked: ${reason}` }; } state.obligations = obligations;
	if (state.currentFinalProofAuthority?.status === "ACTIVE") { const stale = { ...state.currentFinalProofAuthority, status: "STALE" as const, reason, changedAt: now }; state.currentFinalProofAuthority = stale; state.finalProofHistory = state.finalProofHistory.some((item) => item.finalProofAuthorityId === stale.finalProofAuthorityId) ? state.finalProofHistory.map((item) => item.finalProofAuthorityId === stale.finalProofAuthorityId ? stale : item) : [...state.finalProofHistory, stale]; const eventId = stableId("event", state.projectId, "final-proof-stale", stale.finalProofAuthorityId); if (!state.events.some((item) => item.eventId === eventId)) state.events = [...state.events, { eventId, type: "research/final_proof_stale", projectId: state.projectId, timestamp: now, detail: { finalProofAuthorityId: stale.finalProofAuthorityId, artifact: stale.artifact, rootAuthorityReceiptId: stale.rootAuthorityReceiptId, reason, causalAuthorityReceiptIds } }]; }
	const reopenEventId = stableId("event", state.projectId, "project-reopened", root.claimId, String(root.revision)); if (!state.events.some((item) => item.eventId === reopenEventId)) state.events = [...state.events, { eventId: reopenEventId, type: "research/project_reopened", projectId: state.projectId, timestamp: now, detail: { rootClaimId: root.claimId, rootRevision: root.revision, reason, causalAuthorityReceiptIds, frontier: researchFrontier(state).map((item) => item.obligationId) } }];
	state.lastError = `AUTHORITY_REVOKED: ${reason}`;
}

function authoritativeSource(outcome: ResearchOutcome): ArtifactRef | undefined { if (outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA") return outcome.candidate; if (outcome.type === "REFUTED_CLAIM") return outcome.counterexample; if (outcome.type === "REDUCTION" || outcome.type === "CASE_SPLIT" || outcome.type === "CASE_CLOSURE") return outcome.proof; return undefined; }
function authoritativeClaimId(outcome: ResearchOutcome): string | undefined { if (outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA" || outcome.type === "REFUTED_CLAIM" || outcome.type === "REDUCTION" || outcome.type === "CASE_SPLIT" || outcome.type === "CASE_CLOSURE") return outcome.claimId; return undefined; }
function outcomeReceipts(outcome: ResearchOutcome): readonly TrustReceipt[] { return "receipts" in outcome && Array.isArray(outcome.receipts) ? outcome.receipts : []; }
function outcomeScope(outcome: ResearchOutcome): string | undefined { if (outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA") return outcome.scope; if (outcome.type === "REFUTED_CLAIM") return outcome.targetScope; if (outcome.type === "REDUCTION" || outcome.type === "CASE_SPLIT") return outcome.scope; if (outcome.type === "CASE_CLOSURE") return `case:${outcome.claimId}; reason:${outcome.reason}`; return undefined; }
function replaceAuthoritativeSource(outcome: ResearchOutcome, promoted: ArtifactRef): ResearchOutcome { const receipts = outcomeReceipts(outcome).map((receipt) => ({ ...receipt, candidate: promoted })); if (outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA") return { ...outcome, candidate: promoted, receipts }; if (outcome.type === "REFUTED_CLAIM") return { ...outcome, counterexample: promoted, receipts }; if (outcome.type === "REDUCTION") return { ...outcome, proof: promoted, receipts }; if (outcome.type === "CASE_SPLIT") return { ...outcome, proof: promoted, receipts }; if (outcome.type === "CASE_CLOSURE") return { ...outcome, proof: promoted, receipts }; return outcome; }
function latest(state: ResearchProjectState, claimId: string): ClaimSnapshot | undefined { return state.claims[claimId]?.at(-1); }
function eventType(outcome: ResearchOutcome): string { if (outcome.type === "PROVED_CLAIM" || outcome.type === "NEW_LEMMA") return "research/claim_promoted"; if (outcome.type === "FAILED_ROUTE" || outcome.type === "ROUTE_EXHAUSTED") return "research/route_failed"; return "research/effect_applied"; }
function outcomeDetail(outcome: ResearchOutcome): Readonly<Record<string, unknown>> { return JSON.parse(JSON.stringify(outcome)) as Record<string, unknown>; }
function uniqueStrings(values: readonly string[]): string[] { return [...new Set(values.map((item) => item.trim()).filter(Boolean))]; }
function uniqueRefs(refs: readonly ArtifactRef[]): ArtifactRef[] { const seen = new Set<string>(); return refs.filter((ref) => { const key = refKey(ref); if (seen.has(key)) return false; seen.add(key); return true; }); }
function uniqueBy<T>(values: readonly T[], keyOf: (value: T) => string): T[] { const seen = new Set<string>(); return values.filter((value) => { const key = keyOf(value); if (seen.has(key)) return false; seen.add(key); return true; }); }
function dischargeKey(value: AssumptionDischargeDependency): string { return `${value.dependentClaimId}@${value.dependentClaimRevision}:${value.normalizedAssumption}:${value.witnessAuthorityReceiptId}`; }
function refKey(ref: ArtifactRef): string { return `${ref.artifactId}:${ref.contentHash}`; }
function normalize(value: string): string { return value.trim().replace(/\s+/gu, " "); }
type MutableState = { -readonly [K in keyof ResearchProjectState]: ResearchProjectState[K] };

export function isArtifactAuthorityError(error: unknown): error is ArtifactAuthorityError { return error instanceof ArtifactAuthorityError; }
