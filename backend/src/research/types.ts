export type ClaimStatus =
	| "OPEN" | "IN_PROGRESS" | "PROVISIONAL" | "PROVED" | "REFUTED"
	| "REDUCED" | "BLOCKED" | "ABANDONED" | "INVALIDATED" | "NEEDS_REVALIDATION";

export type ImportAuthority =
	| "VERIFIED_CURRENT" | "VERIFIED_IMPORTED" | "PROVISIONAL_IMPORTED"
	| "UNVERIFIED_NOTE" | "FAILED_HISTORICAL_ROUTE" | "OPEN_HISTORICAL_OBLIGATION"
	| "DEFINITION" | "LITERATURE_SOURCE" | "COMPUTATIONAL_EVIDENCE" | "FORMAL_CERTIFICATE";

export type ArtifactType =
	| "CORPUS_SOURCE" | "WORKER_CANDIDATE" | "CANDIDATE_PROOF" | "PROMOTED_PROOF" | "COUNTEREXAMPLE"
	| "LITERATURE_SOURCE" | "COMPUTATION_RESULT" | "FORMAL_PROOF" | "LEAN_SOURCE" | "LEAN_CERTIFICATE"
	| "BOOTSTRAP_ANALYSIS" | "BOOTSTRAP_REPORT" | "CONTEXT_MANIFEST" | "SYNTHESIS_MANIFEST" | "FINAL_PROOF"
	| "AUDIT_RECEIPT" | "AUTHORITY_RECEIPT" | "CHECKPOINT" | "STRUCTURAL_PROBE" | "TACTICAL_RESULT" | "EXECUTION_RECEIPT";

export interface ArtifactRef { readonly artifactId: string; readonly contentHash: string; }

export interface ResearchArtifact extends ArtifactRef {
	readonly artifactType: ArtifactType; readonly bodyPath: string; readonly provenance: string;
	readonly creationAttemptId?: string; readonly authority?: ImportAuthority; readonly references: readonly ArtifactRef[];
	readonly metadata?: Readonly<Record<string, unknown>>; readonly createdAt: string;
}

export interface CorpusRecord extends ArtifactRef {
	readonly relativePath: string; readonly resolvedPath: string; readonly format: "md" | "txt" | "tex" | "lean";
	readonly size: number; readonly provenance: string; readonly importedAt: string; readonly importVersion: number;
	readonly authority: ImportAuthority; readonly changedFromHash?: string;
}

export interface ClaimSnapshot {
	readonly claimId: string; readonly revision: number; readonly statement: string;
	readonly role: "ROOT" | "LEMMA" | "DEFINITION" | "CASE" | "CONJECTURE"; readonly status: ClaimStatus;
	readonly dependencies: readonly string[]; readonly dependencyRevisions?: Readonly<Record<string, number>>;
	readonly evidenceRefs: readonly ArtifactRef[]; readonly auditRefs: readonly ArtifactRef[]; readonly assumptions: readonly string[];
	readonly provenance: string; readonly createdAt: string; readonly invalidationReason?: string;
}

/**
 * Immutable mathematical contract established by the project owner.  This is
 * deliberately separate from ClaimSnapshot.assumptions, which records what a
 * particular research result requires.
 */
export interface RootObjectiveContract {
	readonly contractId: string;
	readonly version: number;
	readonly rootClaimId: string;
	readonly statement: string;
	readonly normalizedStatement: string;
	readonly allowedAssumptions: readonly string[];
	readonly status: "VALID" | "NEEDS_REVALIDATION";
	readonly provenance: {
		readonly source: "USER_API" | "MIGRATED_USER_OBJECTIVE" | "LEGACY_AMBIGUOUS";
		readonly eventId?: string;
		readonly detail?: string;
	};
	readonly createdAt: string;
}

export interface ResearchObligation {
	readonly obligationId: string; readonly claimId: string;
	readonly kind: "PROVE" | "REFUTE" | "CLOSE_SCOPE" | "ESTABLISH_REDUCTION" | "VERIFY_COVERAGE" | "AUDIT";
	readonly statement: string; readonly status: "OPEN" | "IN_PROGRESS" | "BLOCKED" | "CLOSED" | "CANCELLED";
	readonly priority: number; readonly createdAt: string; readonly updatedAt: string; readonly causalReason?: string;
}

export interface CoverageRecord {
	readonly coverageId: string; readonly parentClaimId: string; readonly scope: string; readonly coverageAssertion: string; readonly childClaimIds: readonly string[];
	readonly disposition: "OPEN" | "CLOSED" | "TRANSFERRED" | "SUBSUMED" | "INVALIDATED"; readonly provenanceArtifact?: ArtifactRef;
}
export interface ClaimSupportEdge { readonly edgeId: string; readonly fromClaimId: string; readonly toClaimId: string; readonly contributionId: string; readonly createdAt: string; }

export type RouteReopenPredicate =
	| { readonly type: "CLAIM_PROVED" | "CLAIM_REFUTED" | "NEW_EVIDENCE_FOR"; readonly claimId: string }
	| { readonly type: "PARAMETER_DOMAIN_REDUCED"; readonly domainId: string }
	| { readonly type: "LITERATURE_AVAILABLE"; readonly sourceClass: string }
	| { readonly type: "ASSUMPTION_CHANGED"; readonly assumption: string; readonly value?: string }
	/** Operator/API-only permission. It is never satisfied by a model Director or automatic state transition. */
	| { readonly type: "MANUAL_REOPEN" };

export type RouteReopenActor = "SYSTEM" | "MODEL_DIRECTOR" | "OPERATOR";

export interface ResearchRoute {
	readonly routeId: string; readonly targetObligationId: string; readonly family: string; readonly mechanism: string;
	readonly strategyDescription: string; readonly assumptions: readonly string[]; readonly dependencySnapshot: readonly string[];
	readonly artifactRefs: readonly ArtifactRef[]; readonly status: "ACTIVE" | "FAILED" | "EXHAUSTED" | "SUSPENDED" | "SUCCESS" | "SUPERSEDED";
	readonly attemptIds: readonly string[]; readonly failureMechanism?: string; readonly failureDomain?: string;
	readonly reopenPredicate?: RouteReopenPredicate; readonly reopenedBecause?: string; readonly supersedes?: string; readonly supersededBy?: string;
	readonly createdAt: string; readonly updatedAt: string;
}

export interface LogicalJob {
	readonly logicalJobId: string; readonly projectId: string; readonly cycleId: string; readonly obligationId: string;
	readonly status: "PENDING" | "RUNNING" | "COMPLETED" | "INTERRUPTED" | "FAILED"; readonly directive?: TacticalDirective; readonly createdAt: string;
}

export interface Attempt {
	readonly attemptId: string; readonly logicalJobId: string; readonly ordinal: number; readonly scratchPath: string;
	readonly status: "RUNNING" | "COMPLETED" | "INTERRUPTED" | "FAILED"; readonly artifactRefs: readonly ArtifactRef[];
	readonly startedAt: string; readonly completedAt?: string;
	readonly executorInstanceId?: string; readonly processInstanceId?: number; readonly acquiredAt?: string; readonly heartbeatAt?: string;
}

export type ExecutionTaskKind = "PLANNER" | "WORKER" | "VERIFIER" | "MERGE" | "TARGET_SUBMISSION" | "RESULT_CONVERSION";
export type ExecutionTaskStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED_RETRYABLE" | "FAILED_TERMINAL" | "INTERRUPTED";
export interface ExecutionTask {
	readonly executionTaskId: string; readonly logicalJobId: string; readonly attemptId: string; readonly kind: ExecutionTaskKind;
	readonly logicalTaskId: string; readonly status: ExecutionTaskStatus; readonly inputHash: string; readonly resultArtifact?: ArtifactRef;
	readonly errorKind?: ResearchFailureKind; readonly error?: string; readonly startedAt: string; readonly completedAt?: string;
	readonly executorInstanceId?: string; readonly processInstanceId?: number; readonly acquiredAt?: string; readonly heartbeatAt?: string;
}

export interface AcceptedEffect {
	readonly effectId: string; readonly logicalJobId: string; readonly effectSlot: string; readonly outcomeType: ResearchOutcome["type"];
	readonly appliedAt: string; readonly eventId: string;
}

export type AuthoritativeEffectKind = "PROVED_CLAIM" | "NEW_LEMMA" | "REFUTED_CLAIM" | "REDUCTION" | "CASE_SPLIT" | "CASE_CLOSURE";
export interface AssumptionDischargeWitness {
	readonly assumption: string; readonly normalizedAssumption: string;
	readonly claimId: string; readonly claimRevision: number; readonly authorityReceiptId: string;
	readonly proofArtifactId: string; readonly proofArtifactHash: string; readonly acceptedEffectId: string;
	readonly statement: string; readonly contentHash: string;
}

/** Exact authority edge explaining why one assumption was eliminated. */
export interface AssumptionDischargeDependency {
	readonly assumption: string; readonly normalizedAssumption: string;
	readonly dependentClaimId: string; readonly dependentClaimRevision: number;
	readonly witnessClaimId: string; readonly witnessClaimRevision: number;
	readonly witnessAuthorityReceiptId: string; readonly witnessArtifactId: string; readonly witnessArtifactHash: string;
	readonly acceptedEffectId: string; readonly witnessStatement: string;
}

export type AssumptionAssessment =
	| { readonly status: "ALLOWED_BY_ROOT_CONTRACT"; readonly assumption: string; readonly normalizedAssumption: string }
	| { readonly status: "DISCHARGED"; readonly assumption: string; readonly normalizedAssumption: string; readonly witnesses: readonly AssumptionDischargeWitness[] }
	| { readonly status: "UNRESOLVED"; readonly assumption: string; readonly normalizedAssumption: string; readonly previousWitnesses: readonly AssumptionDischargeWitness[]; readonly reason: "NO_AUTHORITATIVE_WITNESS" | "WITNESS_STALE" };

export interface AuthorityReceipt {
	readonly authorityReceiptId: string; readonly effectId: string; readonly effectKind: AuthoritativeEffectKind;
	readonly claimId: string; readonly claimRevision: number; readonly statement: string; readonly artifact: ArtifactRef; readonly sourceArtifact: ArtifactRef;
	readonly producerAttemptId?: string; readonly trustReceiptIds: readonly string[]; readonly evidenceRefs: readonly ArtifactRef[];
	readonly assumptions: readonly string[]; readonly dependencies: readonly string[]; readonly dependencyRevisions: Readonly<Record<string, number>>;
	readonly assumptionDischarges: readonly AssumptionDischargeDependency[]; readonly scope?: string; readonly createdAt: string;
}

export interface AuthorityValidationState {
	readonly authorityReceiptId: string; readonly status: "ACTIVE" | "SUPERSEDED" | "STALE" | "INVALIDATED";
	readonly reason?: string; readonly causalAuthorityReceiptIds: readonly string[]; readonly changedAt: string;
}

export interface FinalProofAuthority {
	readonly finalProofAuthorityId: string; readonly artifact: ArtifactRef; readonly rootClaimId: string; readonly rootClaimRevision: number;
	readonly rootAuthorityReceiptId: string; readonly status: "ACTIVE" | "STALE"; readonly reason?: string;
	readonly createdAt: string; readonly changedAt: string;
}

export type EvidenceRole = "research_director" | "corpus_bootstrapper" | "planner" | "worker" | "verifier" | "secondary_auditor" | "synthesizer" | "formalizer" | "literature_researcher";
export interface ToolEvidenceReceipt {
	readonly receiptId: string; readonly attemptId: string; readonly role: EvidenceRole; readonly logicalTaskId?: string;
	readonly toolCallId: string; readonly operation: "SEARCH" | "READ" | "METADATA" | "COMPUTE"; readonly artifact: ArtifactRef;
	readonly ranges: readonly string[]; readonly timestamp: string;
}

export interface TrustReceipt {
	readonly receiptId: string; readonly claimId: string; readonly candidate: ArtifactRef; readonly verifierProfile: string;
	readonly evidenceInspected: readonly ArtifactRef[]; readonly availableEvidence?: readonly ArtifactRef[];
	readonly workerReadEvidence?: readonly ArtifactRef[]; readonly workerDeclaredEvidence?: readonly ArtifactRef[];
	readonly verifierReadEvidence?: readonly ArtifactRef[]; readonly toolReceiptIds?: readonly string[];
	readonly verdict: "CORRECT" | "MINOR_FIX" | "UNFINISHED" | "CRITICALLY_FLAWED" | "INCORRECT" | "INCONCLUSIVE";
	readonly independentContext: boolean; readonly stale: boolean; readonly createdAt: string;
}

export interface ContextManifest {
	readonly manifestId: string; readonly projectId: string; readonly logicalJobId: string; readonly obligationId: string;
	readonly claimIds: readonly string[]; readonly artifactRefs: readonly ArtifactRef[]; readonly routeIds: readonly string[]; readonly createdAt: string;
}

export interface TacticalDirective {
	readonly directiveId: string; readonly directorDecisionId: string; readonly action: DirectorAction;
	readonly targetObligationId?: string; readonly targetClaimId?: string; readonly routeId?: string; readonly routeFamily?: string;
	readonly mechanism?: string; readonly desiredContributionKind?: ResearchContributionKind; readonly reason: string;
	readonly relevantFailedRoutes: readonly { readonly routeId: string; readonly family: string; readonly mechanism: string; readonly status: string; readonly failureMechanism?: string; readonly reopenPredicate?: RouteReopenPredicate }[];
	readonly computationIntent?: string; readonly literatureIntent?: string; readonly counterexampleIntent?: string;
	readonly reductionIntent?: string; readonly caseSplitIntent?: string; readonly budgetAllocation: number; readonly createdAt: string;
}

export type DirectorAction =
	| "ATTACK_OBLIGATION" | "CREATE_AUXILIARY_OBLIGATION" | "SPLIT_OBLIGATION" | "REQUEST_REDUCTION"
	| "REQUEST_COUNTEREXAMPLE" | "REQUEST_COMPUTATION" | "REQUEST_LITERATURE" | "RUN_STRUCTURAL_PROBE"
	| "CHANGE_ROUTE" | "REOPEN_ROUTE" | "SUSPEND_ROUTE" | "MARK_ROUTE_EXHAUSTED"
	| "RESTRUCTURE_RESEARCH_MAP" | "TRIGGER_SYNTHESIS" | "CREATE_CHECKPOINT" | "STOP_PROJECT";

export interface DecisionBasis {
	readonly decisionId: string; readonly cycleId: string; readonly action: DirectorAction;
	readonly direction: "MODEL_DIRECTED" | "FALLBACK_DIRECTED"; readonly targetObligationId?: string; readonly targetClaimId?: string;
	readonly routeId?: string; readonly routeFamily?: string; readonly auxiliaryStatement?: string; readonly literatureQuery?: string;
	readonly frontier: readonly string[]; readonly relevantClaims: readonly string[]; readonly failedRoutes: readonly string[];
	readonly reason: string; readonly budgetAllocated: number; readonly protocolError?: string;
}

export type ResearchContributionKind = "LEMMA" | "REDUCTION" | "CASE_SPLIT" | "CASE_CLOSURE" | "COUNTEREXAMPLE" | "CONSTRUCTION" | "BOUND" | "OBSTRUCTION" | "STRUCTURAL_OBSERVATION" | "LITERATURE_APPLICATION";
export interface ContributionChildClaim { readonly claimId: string; readonly statement: string; }
export interface VerifiedResearchContribution {
	readonly contributionId: string; readonly kind: ResearchContributionKind; readonly statement: string; readonly relationshipToTarget: string;
	readonly targetObligationId: string; readonly targetClaimId: string; readonly claimId?: string; readonly assumptions: readonly string[];
	readonly dependencyClaims: readonly string[]; readonly evidenceArtifacts: readonly ArtifactRef[]; readonly candidate: ArtifactRef;
	readonly producer: { readonly role: "worker" | "planner" | "literature_researcher" | "formalizer"; readonly identity: string; readonly taskId?: string };
	readonly verification: TrustReceipt; readonly childClaims?: readonly ContributionChildClaim[]; readonly coverageScope?: string;
	readonly coverageAssertion?: string; readonly closedCaseClaimId?: string; readonly closureReason?: string;
	readonly targetScope?: string; readonly counterexampleScope?: string;
}

export interface VerifiedTargetSubmission {
	readonly submissionId: string; readonly targetObligationId: string; readonly targetClaimId: string; readonly scope: "TARGET";
	readonly statement: string; readonly candidate: ArtifactRef; readonly assumptions: readonly string[]; readonly dependencies: readonly string[];
	readonly evidenceArtifacts: readonly ArtifactRef[]; readonly primaryReceipt: TrustReceipt; readonly secondaryReceipt?: TrustReceipt;
}

export interface RouteObservation {
	readonly observationId: string; readonly targetObligationId: string; readonly routeFamily: string; readonly mechanism: string;
	readonly strategy: string; readonly status: "FAILED" | "EXHAUSTED" | "SUSPENDED" | "VIABLE";
	readonly failureMechanism?: string; readonly failureDomain?: string; readonly evidence: readonly ArtifactRef[]; readonly reopenPredicate?: RouteReopenPredicate;
}

export type ResearchFailureKind = "MATHEMATICAL_FAILURE" | "PROVIDER_ERROR" | "QUOTA_ERROR" | "PROTOCOL_ERROR" | "TOOL_ERROR" | "LITERATURE_ERROR" | "FORMAL_ERROR" | "CANCELLED" | "BUDGET_EXHAUSTED";
export interface ExecutionReceipt {
	readonly executionReceiptId: string; readonly logicalJobId: string; readonly attemptId: string; readonly taskIds: readonly string[];
	readonly evidenceReceiptIds: readonly string[]; readonly failureKind?: ResearchFailureKind; readonly startedAt: string; readonly completedAt: string;
}

export interface TacticalResearchResult {
	readonly obligationId: string; readonly targetClaimId: string;
	readonly targetStatus: "TARGET_PROVED" | "TARGET_REFUTED" | "TARGET_UNRESOLVED" | "EXECUTION_FAILED";
	readonly targetSubmission?: VerifiedTargetSubmission; readonly contributions: readonly VerifiedResearchContribution[];
	readonly routeObservations: readonly RouteObservation[]; readonly executionReceipt: ExecutionReceipt; readonly feedback: string;
}

export type ResearchOutcome =
	| { readonly type: "PROVED_CLAIM" | "NEW_LEMMA"; readonly claimId: string; readonly statement: string; readonly candidate: ArtifactRef; readonly receipts: readonly TrustReceipt[]; readonly dependencies: readonly string[]; readonly assumptions?: readonly string[]; readonly assumptionDischarges?: readonly AssumptionDischargeDependency[]; readonly scope?: string; readonly supportsClaimId?: string; readonly contributionId?: string }
	| { readonly type: "REFUTED_CLAIM"; readonly claimId: string; readonly counterexample: ArtifactRef; readonly receipts: readonly TrustReceipt[]; readonly assumptions: readonly string[]; readonly dependencies: readonly string[]; readonly assumptionDischarges?: readonly AssumptionDischargeDependency[]; readonly targetScope: string; readonly counterexampleScope: string }
	| { readonly type: "REDUCTION"; readonly claimId: string; readonly childClaims: readonly ContributionChildClaim[]; readonly proof: ArtifactRef; readonly receipts: readonly TrustReceipt[]; readonly assumptions: readonly string[]; readonly dependencies: readonly string[]; readonly assumptionDischarges?: readonly AssumptionDischargeDependency[]; readonly scope: string }
	| { readonly type: "CASE_SPLIT"; readonly claimId: string; readonly scope: string; readonly coverageAssertion: string; readonly cases: readonly ContributionChildClaim[]; readonly proof: ArtifactRef; readonly receipts: readonly TrustReceipt[]; readonly assumptions: readonly string[]; readonly dependencies: readonly string[]; readonly assumptionDischarges?: readonly AssumptionDischargeDependency[] }
	| { readonly type: "CASE_CLOSURE"; readonly claimId: string; readonly reason: string; readonly proof: ArtifactRef; readonly receipts: readonly TrustReceipt[]; readonly assumptions: readonly string[]; readonly dependencies: readonly string[]; readonly assumptionDischarges?: readonly AssumptionDischargeDependency[] }
	| { readonly type: "FAILED_ROUTE" | "ROUTE_EXHAUSTED"; readonly obligationId: string; readonly family: string; readonly mechanism: string; readonly strategy: string; readonly assumptions?: readonly string[]; readonly dependencySnapshot?: readonly string[]; readonly failureMechanism: string; readonly failureDomain?: string; readonly evidence: readonly ArtifactRef[]; readonly attemptId?: string; readonly reopenPredicate?: RouteReopenPredicate; readonly failureKind?: ResearchFailureKind }
	| { readonly type: "PARTIAL_PROGRESS" | "STRUCTURAL_DISCOVERY" | "VERIFIED_OBSERVATION"; readonly observation: ArtifactRef; readonly statement: string }
	| { readonly type: "NO_PROGRESS" | "BLOCKED"; readonly reason: string; readonly failureKind?: ResearchFailureKind };

export interface ResearchEvent { readonly eventId: string; readonly type: string; readonly projectId: string; readonly timestamp: string; readonly effectId?: string; readonly detail: Readonly<Record<string, unknown>>; }
export interface ResearchCheckpoint {
	readonly checkpointId: string; readonly projectId: string; readonly createdAt: string; readonly frontier: readonly string[];
	readonly provedClaims: readonly string[]; readonly blockedObligations: readonly string[]; readonly activeRoutes: readonly string[];
	readonly cyclesSinceStructuralProgress: number; readonly recommendedNextTargets: readonly string[];
	readonly authorityRevocations?: readonly { readonly claimId: string; readonly reason: string; readonly eventId: string }[];
	readonly currentFinalProofAuthority?: FinalProofAuthority;
}

export interface ResearchBudgetState { readonly cycles: number; readonly plannerCalls: number; readonly workerCalls: number; readonly verifierCalls: number; readonly secondaryAuditorCalls: number; readonly literatureCalls: number; readonly toolCalls: number; readonly startedAt: number; }

export interface PersistedProofExecutionPlan {
	readonly planId: string; readonly logicalJobId: string; readonly attemptId: string; readonly step: number; readonly inputHash: string;
	readonly taskIds: readonly string[]; readonly dependencyRefs: readonly ArtifactRef[]; readonly status: "RUNNING" | "COMPLETED" | "STALE";
	readonly actionExecutions: readonly PersistedProofPlanActionExecution[];
	readonly staleReason?: string; readonly createdAt: string; readonly completedAt?: string;
}

export interface PersistedProofPlanActionExecution {
	readonly actionId: string; readonly planId: string; readonly ordinal: number; readonly action: Readonly<Record<string, unknown>>;
	readonly status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED_RETRYABLE" | "FAILED_TERMINAL" | "INTERRUPTED" | "STALE";
	readonly resultArtifactIds: readonly string[]; readonly effectIds: readonly string[]; readonly result?: Readonly<Record<string, unknown>>;
	readonly startedAt?: string; readonly completedAt?: string; readonly error?: string;
}

export interface StateMigrationReport {
	readonly fromVersion: number; readonly toVersion: number; readonly migratedAt: string; readonly downgradedClaimIds: readonly string[];
	readonly preservedArtifactCount: number; readonly preservedEventCount: number; readonly warnings: readonly string[];
}

export interface ResearchProjectState {
	readonly schemaVersion: 4; readonly projectId: string; readonly name: string; readonly createdAt: string; readonly updatedAt: string;
	readonly status: "CREATED" | "BOOTSTRAPPED" | "RUNNING" | "PARTIAL" | "BLOCKED" | "PROVED" | "EXHAUSTED";
	readonly rootObjective?: string; readonly rootClaimId?: string; readonly rootObjectiveContract?: RootObjectiveContract; readonly corpusRoots: readonly string[];
	readonly corpus: Readonly<Record<string, CorpusRecord>>; readonly artifacts: Readonly<Record<string, ResearchArtifact>>;
	readonly claims: Readonly<Record<string, readonly ClaimSnapshot[]>>; readonly obligations: Readonly<Record<string, ResearchObligation>>;
	readonly coverage: Readonly<Record<string, CoverageRecord>>; readonly routes: Readonly<Record<string, ResearchRoute>>;
	readonly supportEdges: readonly ClaimSupportEdge[];
	readonly jobs: Readonly<Record<string, LogicalJob>>; readonly attempts: Readonly<Record<string, Attempt>>;
	readonly executionTasks: Readonly<Record<string, ExecutionTask>>; readonly executionPlans: Readonly<Record<string, PersistedProofExecutionPlan>>;
	readonly acceptedEffects: Readonly<Record<string, AcceptedEffect>>; readonly authorityReceipts: Readonly<Record<string, AuthorityReceipt>>;
	readonly authorityValidation: Readonly<Record<string, AuthorityValidationState>>;
	readonly trustReceipts: Readonly<Record<string, TrustReceipt>>; readonly toolEvidenceReceipts: Readonly<Record<string, ToolEvidenceReceipt>>;
	readonly contextManifests: Readonly<Record<string, ContextManifest>>; readonly decisions: readonly DecisionBasis[];
	readonly events: readonly ResearchEvent[]; readonly checkpoints: readonly ResearchCheckpoint[]; readonly bootstrapReports: readonly BootstrapReport[];
	readonly bootstrapRuns: Readonly<Record<string, BootstrapRunState>>;
	readonly cycle: number; readonly cyclesSinceStructuralProgress: number; readonly activeCycleId?: string; readonly budget: ResearchBudgetState;
	readonly effectiveConfig: Readonly<Record<string, unknown>>; readonly configRevision: string; readonly migrationReports: readonly StateMigrationReport[]; readonly finalProofArtifact?: ArtifactRef;
	readonly finalProofHistory: readonly FinalProofAuthority[]; readonly currentFinalProofAuthority?: FinalProofAuthority;
	readonly formalizationStatus?: "NOT_REQUESTED" | "PENDING" | "VERIFIED" | "BLOCKED_FORMAL" | "FAILED"; readonly lastError?: string;
}

export interface BootstrapDependencyProposal { readonly fromEntity: string; readonly toEntity: string; readonly confidence: "EXPLICIT" | "INFERRED"; readonly confidenceScore?: number; }
export interface BootstrapProposal {
	readonly entityKey: string; readonly source: ArtifactRef;
	readonly kind: "DEFINITION" | "CLAIM" | "OPEN_PROBLEM" | "FAILED_ROUTE" | "REDUCTION" | "CASE_SPLIT" | "COMPUTATIONAL_EVIDENCE";
	readonly statement: string; readonly authority: ImportAuthority; readonly dependencyHints: readonly string[]; readonly targetHint?: string;
	readonly routeFamily?: string; readonly mechanism?: string; readonly failureMechanism?: string; readonly cases?: readonly string[];
	readonly sourceRange?: { readonly startLine: number; readonly endLine: number };
}
export interface BootstrapReport {
	readonly bootstrapRunId?: string;
	readonly projectId: string; readonly stages: readonly string[]; readonly inspectedArtifactIds: readonly string[];
	readonly proposals: readonly BootstrapProposal[]; readonly dependencies: readonly BootstrapDependencyProposal[];
	readonly createdClaimIds: readonly string[]; readonly createdObligationIds: readonly string[]; readonly createdRouteIds: readonly string[];
	readonly frontierObligationIds: readonly string[]; readonly warnings: readonly string[]; readonly modelDirected: boolean;
	readonly analyzedRanges: readonly { readonly artifactId: string; readonly startLine: number; readonly endLine: number }[];
	readonly reconstructedReductions: readonly { readonly parentClaimId: string; readonly childClaimIds: readonly string[]; readonly provisional: true }[];
	readonly reconstructedCoverage: readonly { readonly parentClaimId: string; readonly childClaimIds: readonly string[]; readonly coverageId: string; readonly provisional: true }[];
	readonly createdAt: string;
}

export type BootstrapFailureType =
	| "STRUCTURED_OUTPUT_PARSE_FAILURE" | "SCHEMA_VALIDATION_FAILURE" | "EMPTY_RESPONSE" | "NO_ENTITY_RESPONSE"
	| "PROVIDER_FAILURE" | "TIMEOUT" | "CONTEXT_LIMIT" | "SEMANTIC_REJECTION" | "CONSISTENCY_REVIEW_FAILURE"
	| "TRANSPORT_FAILURE" | "OTHER";

export interface BootstrapRangeFailure {
	readonly type: BootstrapFailureType; readonly message: string; readonly fallbackOccurred: boolean;
}

export interface BootstrapRangeWorkRecord {
	readonly rangeId: string; readonly sourceArtifactId: string; readonly sourceContentHash: string;
	readonly startLine: number; readonly endLine: number; readonly status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED_RETRYABLE" | "STALE";
	readonly executorInstanceId?: string; readonly processInstanceId?: number; readonly acquiredAt?: string; readonly heartbeatAt?: string;
	readonly modelSessionId?: string; readonly attemptId?: string; readonly provider?: string; readonly model?: string;
	readonly parsedResult?: { readonly proposals: readonly Omit<BootstrapProposal, "source" | "authority">[]; readonly dependencies: readonly BootstrapDependencyProposal[]; readonly warnings?: readonly string[] };
	readonly rawResponse?: string; readonly failure?: BootstrapRangeFailure; readonly fallbackResult?: { readonly proposals: readonly Omit<BootstrapProposal, "source" | "authority">[]; readonly dependencies: readonly BootstrapDependencyProposal[]; readonly warnings?: readonly string[] };
	readonly durationMs?: number; readonly completedAt?: string;
}

export interface BootstrapProvisionalImportResult {
	readonly createdClaimIds: readonly string[]; readonly createdObligationIds: readonly string[]; readonly createdRouteIds: readonly string[];
	readonly reconstructedReductions: readonly { readonly parentClaimId: string; readonly childClaimIds: readonly string[]; readonly provisional: true }[];
	readonly reconstructedCoverage: readonly { readonly parentClaimId: string; readonly childClaimIds: readonly string[]; readonly coverageId: string; readonly provisional: true }[];
	readonly warnings: readonly string[]; readonly completedAt: string;
}

export interface BootstrapRunState {
	readonly bootstrapRunId: string; readonly projectId: string; readonly configRevision: string; readonly corpusManifestHash: string;
	readonly schemaVersion: string; readonly modelDirected: boolean; readonly status: "RUNNING" | "COMPLETED" | "STALE";
	readonly currentStage: "PER_FILE_SEMANTIC_ANALYSIS" | "ENTITY_MERGE" | "DEPENDENCY_RECONSTRUCTION" | "ROUTE_RECONSTRUCTION" | "CONSISTENCY_REVIEW" | "PROVISIONAL_IMPORT" | "COMPLETED";
	readonly rangeWork: Readonly<Record<string, BootstrapRangeWorkRecord>>;
	readonly mergeStatus: "PENDING" | "COMPLETED"; readonly dependencyReconstructionStatus: "PENDING" | "COMPLETED";
	readonly routeReconstructionStatus: "PENDING" | "COMPLETED"; readonly consistencyReviewStatus: "PENDING" | "COMPLETED" | "FAILED";
	readonly provisionalImportStatus: "PENDING" | "COMPLETED"; readonly provisionalImportResult?: BootstrapProvisionalImportResult; readonly reportCreatedAt?: string;
	readonly createdAt: string; readonly updatedAt: string;
}
