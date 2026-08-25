import type { JsonObject } from "../models/json.js";

export type ProofMode = "prove" | "prove_and_formalize" | "formalize_only";

export type ProofStatus =
	| "OPEN"
	| "RUNNING"
	| "CANDIDATE_READY"
	| "PROVED"
	| "PARTIAL"
	| "FAILED"
	| "BLOCKED_PROVIDER"
	| "CANCELLED"

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

export type ProofTaskInput = {
	readonly taskId?: string;
	readonly summary: string;
	readonly description: string;
	/** A stable route identity supplied by the planner when wording may vary. */
	readonly routeKey?: string;
};

export type ProofTask = {
	readonly taskId: string;
	readonly summary: string;
	readonly description: string;
	readonly routeFingerprint: string;
	readonly routeKey?: string;
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
		};

export type VerificationResult = {
	readonly verdict: ProofVerdict;
	readonly feedback: string;
	readonly checks?: readonly string[];
};

export type FormalVerificationResult = {
	readonly ok: boolean;
	readonly feedback: string;
	readonly command?: string;
	readonly artifactPath?: string;
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
	readonly maxWorkerCalls?: number;
	readonly maxVerifierCalls?: number;
	readonly maxLiteratureSearches?: number;
	readonly maxToolCalls?: number;
	readonly maxWallTimeMs?: number;
};

export type ProofBudgetState = {
	readonly workerCalls: number;
	readonly verifierCalls: number;
	readonly literatureSearches: number;
	readonly toolCalls: number;
	readonly startedAt: number;
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
	readonly status: ProofStatus;
	readonly whiteboard: string;
	readonly repository: readonly ProofRepositoryItem[];
	/** OpenProver-style one-line repository index. */
	readonly repositoryIndex?: string;
	readonly candidates: readonly ProofCandidate[];
	readonly failedRoutes: readonly ProofRouteFailure[];
	readonly recentOutputs: readonly ProofOutput[];
	readonly stepHistory?: readonly ProofStepRecord[];
	readonly budget?: ProofBudgetState;
	readonly artifacts?: ProofArtifactStatus;
};

export type ProofResearchContext = {
	readonly runId: string;
	readonly step: number;
	readonly obligation: ProofObligation;
	readonly whiteboard: string;
	readonly task: ProofTask;
	readonly referencedMaterials: string;
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
			readonly action: "submit_lean_proof";
			readonly proofSlug?: string;
			readonly leanProofSlug?: string;
			readonly summary?: string;
		}
	| { readonly action: "stop"; readonly reason?: string; readonly summary?: string };

export type ProofPlan = {
	readonly actions: readonly ProofAction[];
	readonly summary?: string;
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
	readonly budget: ProofBudgetState;
	readonly submittedCandidateId?: string;
	readonly submittedProofSlug?: string;
	readonly proofLeanPath?: string;
	readonly lastError?: string;
};

export type ProofRunResult = {
	readonly runId: string;
	readonly status: ProofStatus;
	readonly mode?: ProofMode;
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
			readonly proofSlug: string;
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
