import type { JsonObject } from "../models/json.js";

export type ProofMode = "prove" | "prove_and_formalize" | "formalize_only";

/** Whether the model owns task decomposition or a legacy fixed prompt does. */
export type ProofWorkflowMode = "dynamic" | "legacy";

export type ProofStatus =
	| "OPEN"
	| "RUNNING"
	| "CANDIDATE_READY"
	| "PROVED"
	| "PARTIAL"
	| "FAILED"
	| "BLOCKED_FORMAL"
	| "BLOCKED_PROVIDER"
	| "CANCELLED";

export type ProofTaskKind = "MATHEMATICAL" | "FORMALIZATION";

export type ProofTaskStatus =
	| "PENDING"
	| "RUNNING"
	| "COMPLETED"
	| "PARTIAL"
	| "FAILED_RETRYABLE"
	| "FAILED_TERMINAL"
	| "BLOCKED";

/** Logical agent metadata selected by the workflow controller. */
export type ProofAgentSpec = {
	/** Stable identity used to resume the same logical agent across steps. */
	readonly agentId: string;
	/** Why this agent exists; the runtime may map it to an approved model/profile. */
	readonly purpose: string;
	/** Capabilities are descriptive and do not grant tools or permissions. */
	readonly capabilities?: readonly string[];
	/** Selects an approved configured role; it never grants extra tools. */
	readonly role?: "worker" | "formalizer";
};

export type ProofWorkflowSpec = {
	/** The controller's current decomposition strategy. */
	readonly strategy: string;
	readonly rationale?: string;
	readonly successCriteria?: readonly string[];
};

/** A problem-derived unit that the workflow controller can assign independently. */
export type ProofDecompositionUnit = {
	readonly unitId: string;
	readonly label: string;
	readonly description: string;
};

/**
 * A lightweight decomposition signal derived from the obligation shape. It is
 * advisory for the model, but the dynamic runtime also uses it to prevent an
 * obviously composite first plan from collapsing into one broad worker task.
 */
export type ProofDecomposition = {
	readonly complexity: "SIMPLE" | "COMPOSITE";
	readonly unitCount: number;
	readonly recommendedMinimumTasks: number;
	readonly signals: readonly string[];
	readonly units: readonly ProofDecompositionUnit[];
};

/**
 * The first three verdicts mirror OpenProver's verifier vocabulary. The
 * legacy names remain accepted so existing providers can be migrated without
 * changing their response format in one step.
 */
export type ProofVerdict =
	| "CORRECT"
	| "CRITICALLY_FLAWED"
	| "NEEDS_MINOR_FIXES"
	| "UNFINISHED"
	| "INCORRECT"
	| "INCONCLUSIVE";

export type ProofObligation = {
	readonly obligationId: string;
	readonly theorem: string;
	readonly context?: string;
};

/** The concrete Lean environment selected for one session/run. */
export type ProofLeanProjectContext = {
	readonly projectDirectory: string;
	readonly toolchain: string;
	readonly packages: readonly string[];
	readonly imports: readonly string[];
	readonly packageSources?: Readonly<Record<string, string>>;
};

export type ProofTaskInput = {
	readonly taskId?: string;
	readonly summary: string;
	readonly description: string;
	/** A stable route identity supplied by the planner when wording may vary. */
	readonly routeKey?: string;
	/** Only a Planner may designate a task as an attempt on the exact research target. */
	readonly scope?: "TARGET" | "CONTRIBUTION";
	readonly targetClaimId?: string;
	readonly contributionKind?: ProofContributionKind;
	/** Stable ids of tasks whose completed results are prerequisites. */
	readonly dependsOn?: readonly string[];
	/** Optional logical agent selected by the dynamic workflow controller. */
	readonly agent?: ProofAgentSpec;
	/** What the worker must establish before the task can be considered complete. */
	readonly successCriteria?: string;
	/** Links a new task to a previous partial task so the worker can continue it. */
	readonly continuationOf?: string;
	/** Formalization tasks are process-gated and bypass model-only acceptance. */
	readonly kind?: ProofTaskKind;
};

export type ProofTask = {
	readonly taskId: string;
	readonly summary: string;
	readonly description: string;
	readonly routeFingerprint: string;
	readonly routeKey?: string;
	readonly scope: "TARGET" | "CONTRIBUTION";
	readonly targetClaimId?: string;
	readonly contributionKind?: ProofContributionKind;
	readonly dependsOn: readonly string[];
	readonly agent?: ProofAgentSpec;
	readonly successCriteria?: string;
	readonly continuationOf?: string;
	readonly status: ProofTaskStatus;
	readonly attempt: number;
	readonly updatedAt: string;
	readonly lastError?: string;
	readonly kind: ProofTaskKind;
};

export type ProofContributionKind =
	| "LEMMA" | "REDUCTION" | "CASE_SPLIT" | "CASE_CLOSURE" | "COUNTEREXAMPLE"
	| "CONSTRUCTION" | "BOUND" | "OBSTRUCTION" | "STRUCTURAL_OBSERVATION" | "LITERATURE_APPLICATION";

export type ProofContributionDraft = {
	readonly kind: ProofContributionKind;
	readonly statement: string;
	readonly relationshipToTarget: string;
	readonly claimId?: string;
	readonly assumptions?: readonly string[];
	readonly dependencyClaims?: readonly string[];
	readonly childClaims?: readonly { readonly claimId: string; readonly statement: string }[];
	readonly coverageScope?: string;
	readonly coverageAssertion?: string;
	readonly closedCaseClaimId?: string;
	readonly closureReason?: string;
	readonly targetScope?: string;
	readonly counterexampleScope?: string;
};

export type ProofCandidateDraft = {
	readonly taskId: string;
	readonly content: string;
	readonly strategy: string;
	readonly candidateId?: string;
	/** Optional semantic claim supplied by a structured researcher. */
	readonly claim?: string;
	/** Optional precomputed semantic fingerprint supplied by a trusted adapter. */
	readonly claimFingerprint?: string;
	/** Model-declared evidence is advisory; runtime evidence is attached separately. */
	readonly evidence?: readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[];
	/** Exact artifact ids the Worker declares it mathematically relied upon. */
	readonly reliedOnArtifactIds?: readonly string[];
	readonly scope?: "TARGET" | "CONTRIBUTION";
	readonly assumptions?: readonly string[];
	readonly dependencyClaims?: readonly string[];
	readonly contribution?: ProofContributionDraft;
};

export type ProofCandidate = {
	readonly candidateId: string;
	readonly taskId: string;
	readonly content: string;
	readonly strategy: string;
	readonly routeFingerprint: string;
	readonly claimFingerprint: string;
	readonly candidateFingerprint: string;
	readonly claim?: string;
	readonly evidence: readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[];
	readonly discoveredEvidence: readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[];
	readonly bodyReadEvidence: readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[];
	readonly declaredEvidence: readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[];
	readonly reliedOnArtifactIds: readonly string[];
	readonly scope: "TARGET" | "CONTRIBUTION";
	readonly targetClaimId?: string;
	readonly assumptions: readonly string[];
	readonly dependencyClaims: readonly string[];
	readonly contribution?: ProofContributionDraft;
};

export type ResearchResult =
	| {
			readonly kind: "candidate";
			readonly candidate: ProofCandidateDraft;
			readonly notes?: string;
		}
	| {
			readonly kind: "observation";
			readonly content: string;
			readonly suggestedNext?: string;
		}
	| {
			readonly kind: "blocked";
			readonly reason: string;
		}
	| {
			readonly kind: "partial";
			readonly content: string;
			readonly reason: string;
			readonly suggestedNext?: string;
		};

export type VerificationResult = {
	readonly verdict: ProofVerdict;
	readonly feedback: string;
	readonly checks?: readonly string[];
	/** Filled by runtime instrumentation, never trusted from model JSON. */
	readonly evidence?: readonly { readonly artifactId: string; readonly contentHash: string; readonly ranges?: readonly string[] }[];
};

export type FormalVerificationResult = {
	readonly ok: boolean;
	readonly feedback: string;
	readonly failureKind?: "REJECTED" | "UNAVAILABLE" | "TIMEOUT" | "ABORTED";
	readonly command?: string;
	readonly artifactPath?: string;
};

export type FormalVerificationAttempt = {
	readonly attempt: number;
	readonly step: number;
	readonly sourceId: string;
	readonly taskId?: string;
	readonly candidateId?: string;
	readonly proofSlug?: string;
	readonly result: FormalVerificationResult;
	readonly timestamp: number;
};

export type ProofFormalVerifier = {
	verify(
		content: string,
		context: {
			readonly runId: string;
			readonly step: number;
			readonly obligation: ProofObligation;
			readonly theoremText?: string;
			readonly workDirectory?: string;
		},
		signal?: AbortSignal,
	): Promise<FormalVerificationResult>;
};

export type ProofRepositoryItem = {
	readonly slug: string;
	readonly summary: string;
	readonly content: string;
	readonly format?: "text" | "lean";
};

export type ProofItemInput = {
	readonly slug: string;
	readonly content?: string;
	readonly summary?: string;
	readonly format?: "text" | "lean";
};

export type ProofOutput = {
	readonly step: number;
	readonly action: string;
	readonly summary: string;
	readonly content: string;
};

export type ProofBudgetOptions = {
	readonly maxPlannerCalls?: number;
	readonly maxWorkerCalls?: number;
	readonly maxVerifierCalls?: number;
	readonly maxLiteratureSearches?: number;
	readonly maxToolCalls?: number;
	readonly maxWallTimeMs?: number;
};

export type ProofBudgetState = {
	readonly plannerCalls: number;
	readonly workerCalls: number;
	readonly verifierCalls: number;
	readonly literatureSearches: number;
	readonly toolCalls: number;
	readonly startedAt: number;
	readonly maxPlannerCalls?: number;
	readonly maxWorkerCalls?: number;
	readonly maxVerifierCalls?: number;
	readonly maxLiteratureSearches?: number;
	readonly maxToolCalls?: number;
	readonly maxWallTimeMs?: number;
};

export type ProofPlannerContext = {
	readonly runId: string;
	readonly step: number;
	readonly obligation: ProofObligation;
	readonly mode?: ProofMode;
	readonly workflowMode: ProofWorkflowMode;
	readonly status: ProofStatus;
	readonly whiteboard: string;
	readonly repository: readonly ProofRepositoryItem[];
	/** OpenProver-style one-line repository index. */
	readonly repositoryIndex?: string;
	readonly candidates: readonly ProofCandidate[];
	readonly tasks: readonly ProofTask[];
	readonly failedRoutes: readonly ProofRouteFailure[];
	readonly recentOutputs: readonly ProofOutput[];
	readonly stepHistory?: readonly ProofStepRecord[];
	readonly budget?: ProofBudgetState;
	readonly artifacts?: ProofArtifactStatus;
	readonly formalAttempts: readonly FormalVerificationAttempt[];
	readonly tacticalDirective?: Readonly<Record<string, unknown>>;
	readonly decomposition?: ProofDecomposition;
};

export type ProofResearchContext = {
	readonly runId: string;
	readonly step: number;
	readonly obligation: ProofObligation;
	readonly whiteboard: string;
	readonly task: ProofTask;
	readonly referencedMaterials: string;
	/** Available only to formalization tasks; ordinary workers do not need it. */
	readonly leanProject?: ProofLeanProjectContext;
	/** Text of the pinned upstream Lean skill used by the Formalizer. */
	readonly formalizerSkill?: string;
};

export type ProofVerifierContext = {
	readonly runId: string;
	readonly step: number;
	readonly obligation: ProofObligation;
	readonly task: ProofTask;
	/** The original task is kept separate from the candidate by design. */
	readonly referencedMaterials?: string;
};

export interface ProofPlanner {
	plan(context: ProofPlannerContext, signal?: AbortSignal): Promise<ProofPlan>;
}

export type ProofPlannerTrace = {
	readonly prompt?: string;
	readonly response?: string;
	readonly attempts: number;
	readonly parseError?: string;
};

export interface ProofPlannerWithTrace {
	readonly lastTrace?: ProofPlannerTrace;
}

export interface ProofResearcher {
	research(context: ProofResearchContext, signal?: AbortSignal): Promise<ResearchResult>;
}

/** Creates a logical worker selected by the dynamic workflow controller. */
export type ProofAgentFactory = (
	spec: ProofAgentSpec,
	context: ProofResearchContext,
) => ProofResearcher | Promise<ProofResearcher>;

export interface ProofVerifier {
	verify(
		candidate: ProofCandidate,
		context: ProofVerifierContext,
		signal?: AbortSignal,
	): Promise<VerificationResult>;
}

export interface ProofLiteratureSearcher {
	search(
		query: string,
		context: ProofPlannerContext,
		signal?: AbortSignal,
	): Promise<{ readonly content: string; readonly sources?: readonly string[] }>;
}

export interface ProofTool<TInput extends JsonObject = JsonObject, TResult = unknown> {
	readonly name: string;
	readonly description: string;
	execute(input: TInput, context: ProofPlannerContext, signal?: AbortSignal): Promise<TResult>;
}

export type ProofAction =
	| { readonly action: "read_theorem"; readonly summary?: string }
	| { readonly action: "read_items"; readonly slugs: readonly string[]; readonly summary?: string }
	| { readonly action: "write_items"; readonly items: readonly ProofItemInput[]; readonly summary?: string }
	| { readonly action: "write_whiteboard"; readonly content: string; readonly summary?: string }
	| { readonly action: "spawn"; readonly tasks: readonly ProofTaskInput[]; readonly summary?: string }
	| { readonly action: "literature_search"; readonly query: string; readonly context?: string; readonly summary?: string }
	| { readonly action: "use_tool"; readonly toolName: string; readonly input: JsonObject; readonly summary?: string }
	| {
			readonly action: "submit_proof";
			readonly candidateId?: string;
			readonly proofSlug?: string;
			readonly summary?: string;
		}
	| {
			readonly action: "submit_target_proof";
			readonly candidateId: string;
			readonly targetObligationId: string;
			readonly targetClaimId: string;
			readonly summary?: string;
		}
	| {
			readonly action: "submit_lean_proof";
			readonly proofSlug?: string;
			readonly leanProofSlug?: string;
			readonly summary?: string;
		}
	| { readonly action: "stop"; readonly reason?: string; readonly summary?: string };

export type ProofPlan = {
	readonly actions: readonly ProofAction[];
	readonly summary?: string;
	/** Controller-authored description of this round's dynamic decomposition. */
	readonly workflow?: ProofWorkflowSpec;
};

export type ProofExecutionPlan = {
	readonly planId: string;
	readonly step: number;
	readonly inputHash: string;
	readonly plan: ProofPlan;
	readonly taskIds: readonly string[];
	readonly dependencyRefs: readonly { readonly artifactId: string; readonly contentHash: string }[];
	readonly actionExecutions: readonly ProofPlanActionExecution[];
	readonly status: "RUNNING" | "COMPLETED" | "STALE";
	readonly staleReason?: string;
	readonly createdAt: string;
	readonly completedAt?: string;
};

export type ProofPlanActionExecutionStatus =
	| "PENDING" | "RUNNING" | "COMPLETED" | "FAILED_RETRYABLE" | "FAILED_TERMINAL" | "INTERRUPTED" | "STALE";

export type ProofPlanActionExecution = {
	readonly actionId: string;
	readonly planId: string;
	readonly ordinal: number;
	readonly action: ProofAction;
	readonly status: ProofPlanActionExecutionStatus;
	readonly resultArtifactIds: readonly string[];
	readonly effectIds: readonly string[];
	readonly result?: Readonly<Record<string, unknown>>;
	readonly startedAt?: string;
	readonly completedAt?: string;
	readonly error?: string;
};

export type ProofRouteFailure = {
	readonly routeFingerprint: string;
	readonly taskId: string;
	readonly reason: string;
	readonly candidateFingerprint?: string;
	readonly claimFingerprint?: string;
	readonly step: number;
};

export type ProofRejectedCandidate = {
	readonly candidateFingerprint: string;
	readonly claimFingerprint: string;
	readonly routeFingerprint: string;
	readonly reason: string;
	readonly step: number;
};

export type ProofArtifactStatus = {
	readonly theoremPath: string;
	readonly whiteboardPath: string;
	readonly statePath: string;
	readonly proofPath?: string;
	readonly leanTheoremPath?: string;
	readonly proofLeanPath?: string;
};

export type ProofStepRecord = {
	readonly step: number;
	readonly status: "started" | "completed" | "failed" | "interrupted";
	readonly action?: string;
	readonly summary?: string;
	readonly plannerResponse?: string;
	readonly error?: string;
	readonly outputCount?: number;
};

export type ProofState = {
	readonly runId: string;
	readonly mode: ProofMode;
	readonly workflowMode: ProofWorkflowMode;
	readonly status: ProofStatus;
	readonly step: number;
	readonly obligation: ProofObligation;
	readonly whiteboard: string;
	readonly tasks: readonly ProofTask[];
	readonly candidates: readonly ProofCandidate[];
	readonly verifications: Readonly<Record<string, VerificationResult>>;
	readonly failedRoutes: readonly ProofRouteFailure[];
	readonly rejectedCandidates: readonly ProofRejectedCandidate[];
	readonly recentOutputs: readonly ProofOutput[];
	readonly stepHistory: readonly ProofStepRecord[];
	readonly executionPlans: readonly ProofExecutionPlan[];
	readonly formalAttempts: readonly FormalVerificationAttempt[];
	readonly budget: ProofBudgetState;
	readonly submittedCandidateId?: string;
	readonly submittedProofSlug?: string;
	readonly targetSubmission?: {
		readonly candidateId: string;
		readonly targetObligationId: string;
		readonly targetClaimId: string;
		readonly scope: "TARGET";
	};
	readonly proofLeanPath?: string;
	readonly lastError?: string;
};

export type ProofRunResult = {
	readonly runId: string;
	readonly status: ProofStatus;
	readonly mode?: ProofMode;
	readonly workflowMode?: ProofWorkflowMode;
	readonly steps: number;
	readonly candidateId?: string;
	readonly proofPath?: string;
	readonly proofLeanPath?: string;
	readonly reason?: string;
};

export type ProofEvent =
	| {
			readonly type: "proof/obligation_created";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly obligation: ProofObligation;
		}
	| {
			readonly type: "proof/status_changed";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly status: ProofStatus;
			readonly reason?: string;
		}
	| {
			readonly type: "proof/task_dispatched";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly task: ProofTask;
		}
	| {
			readonly type: "proof/task_status_changed";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly taskId: string;
			readonly previousStatus: ProofTaskStatus;
			readonly status: ProofTaskStatus;
			readonly task: ProofTask;
			readonly reason?: string;
		}
	| {
			readonly type: "proof/research_result";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly taskId: string;
			readonly result: ResearchResult;
		}
	| {
			readonly type: "proof/verification_result";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly candidateId: string;
			readonly result: VerificationResult;
		}
	| {
			readonly type: "proof/formal_verification_result";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly attempt: FormalVerificationAttempt;
			readonly proofSlug?: string;
			readonly taskId?: string;
			readonly candidateId?: string;
			readonly result: FormalVerificationResult;
		}
	| {
			readonly type: "proof/route_failed";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly failure: ProofRouteFailure;
		}
	| {
			readonly type: "proof/candidate_ready";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly candidate: ProofCandidate;
		}
	| {
			readonly type: "proof/whiteboard_updated";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly content: string;
		}
	| {
			readonly type: "proof/repository_updated";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly items: readonly ProofItemInput[];
		}
	| {
			readonly type: "proof/tool_result";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly toolName: string;
			readonly ok: boolean;
			readonly content: string;
		}
	| {
			readonly type: "proof/planner_output";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly response: string;
			readonly plan: ProofPlan;
		}
	| {
			readonly type: "proof/step_started";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
		}
	| {
			readonly type: "proof/step_finished";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly summary: string;
		}
	| {
			readonly type: "proof/submitted";
			readonly eventId: string;
			readonly runId: string;
			readonly timestamp: number;
			readonly step: number;
			readonly candidateId: string;
			readonly proofPath: string;
		};
