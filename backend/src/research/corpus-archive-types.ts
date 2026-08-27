import type { AcceptedEffect, ArtifactRef, FinalProofAuthority, ResearchOutcome, ResearchProjectState, RouteReopenPredicate } from "./types.js";

export const CORPUS_ARCHIVE_CLASSES = ["NO_ARCHIVE", "ATTEMPT", "RESULT", "FAILURE", "COMPUTATION", "LITERATURE", "STATE_UPDATE"] as const;
export type CorpusArchiveClass = (typeof CORPUS_ARCHIVE_CLASSES)[number];

export const CORPUS_ARCHIVE_STATUSES = ["PENDING", "CLAIMED", "PROJECTING", "COMMITTED_LOCAL", "PUSHED", "COMPLETE", "RETRYABLE_FAILURE", "MANUAL_REVIEW", "PERMANENT_FAILURE"] as const;
export type CorpusArchiveStatus = (typeof CORPUS_ARCHIVE_STATUSES)[number];

export interface CorpusPublishingConfig {
	readonly enabled: boolean;
	readonly repositoryUrl: string;
	readonly localCheckout: string;
	readonly branch: string;
	readonly autoPush: boolean;
	readonly indexCommand: readonly string[];
	readonly nodePath?: string;
}

export const DEFAULT_CORPUS_PUBLISHING_CONFIG: CorpusPublishingConfig = {
	enabled: false,
	repositoryUrl: "https://github.com/dongxuelian2/three-term-decimal-concatenation-square-sum.git",
	localCheckout: "",
	branch: "main",
	autoPush: false,
	indexCommand: ["python", "tools/update-research-index.py"],
};

export interface CorpusArchiveEffectSource {
	readonly projectId: string;
	readonly cycleId: string;
	readonly logicalJobId: string;
	readonly effectSlot: string;
	readonly outcome: ResearchOutcome;
}

export interface CorpusFailureSnapshot {
	readonly routeFamily: string;
	readonly mechanism: string;
	readonly strategy: string;
	readonly mathematicalScope: string;
	readonly failurePoint: string;
	readonly obtainedProgress: string;
	readonly whatIsRuledOut: string;
	readonly whatIsNotRuledOut: string;
	readonly reopenPredicate?: RouteReopenPredicate;
}

export interface CorpusArchiveSemanticSnapshot {
	readonly title: string;
	readonly statement: string;
	readonly scope: string;
	readonly sourceOutcomeType: ResearchOutcome["type"] | "FINAL_PROOF_AUTHORITY";
	readonly authoritativeArtifact?: ArtifactRef;
	readonly claimStatus?: string;
	readonly strictResult: boolean;
	readonly failure?: CorpusFailureSnapshot;
}

export interface CorpusArchiveIntent {
	readonly schemaVersion: 1;
	readonly intentId: string;
	readonly projectId: string;
	readonly runId?: string;
	readonly sourceId: string;
	readonly sourceEffectId?: string;
	readonly sourceEventId?: string;
	readonly finalProofAuthorityId?: string;
	readonly theoremId?: string;
	readonly claimSnapshotHash?: string;
	readonly researchMapId: string;
	readonly researchMapVersion: number;
	readonly obligationId?: string;
	readonly sessionClosureId?: string;
	readonly routeFailureId?: string;
	readonly classificationHint: Exclude<CorpusArchiveClass, "NO_ARCHIVE">;
	readonly semanticSummaryRef?: ArtifactRef;
	readonly evidenceRefs: readonly ArtifactRef[];
	readonly createdFromAuthoritativeState: true;
	readonly canonicalKey: string;
	readonly artifactSlug: string;
	readonly requestedNodePath?: string;
	readonly semantic: CorpusArchiveSemanticSnapshot;
	readonly status: CorpusArchiveStatus;
	readonly statusCode?: string;
	readonly statusDetail?: string;
	readonly localCommit?: string;
	readonly claimedAt?: string;
	readonly createdAt: string;
	readonly updatedAt: string;
}

export interface CorpusValidationResult {
	readonly ok: boolean;
	readonly checks: readonly string[];
	readonly errors: readonly string[];
}

export interface CorpusPushResult {
	readonly status: "SKIPPED" | "PUSHED" | "ALREADY_PRESENT";
	readonly remote?: string;
	readonly branch: string;
}

export interface ArchiveReceipt {
	readonly schemaVersion: 1;
	readonly receiptId: string;
	readonly intentId: string;
	readonly sourceEffectId?: string;
	readonly finalProofAuthorityId?: string;
	readonly corpusRepository: string;
	readonly corpusBaseCommit: string;
	readonly corpusResultCommit: string;
	readonly classification: Exclude<CorpusArchiveClass, "NO_ARCHIVE">;
	readonly nodePath: string;
	readonly filesCreated: readonly string[];
	readonly filesUpdated: readonly string[];
	readonly filesMoved: readonly { readonly from: string; readonly to: string }[];
	readonly indexRegenerated: boolean;
	readonly validationResult: CorpusValidationResult;
	readonly pushResult: CorpusPushResult;
	readonly contentHashes: Readonly<Record<string, string>>;
	readonly completedAt: string;
}

export interface CorpusArchiveOutboxState {
	readonly schemaVersion: 1;
	readonly projectId: string;
	readonly activatedAt: string;
	readonly intents: Readonly<Record<string, CorpusArchiveIntent>>;
	readonly receipts: Readonly<Record<string, ArchiveReceipt>>;
}

export interface CorpusArchiveDisposition {
	readonly classification: CorpusArchiveClass;
	readonly reason: string;
	readonly intent?: CorpusArchiveIntent;
}

export interface CorpusNodeResolutionRequest {
	readonly checkout: string;
	readonly projectId: string;
	readonly theoremId?: string;
	readonly obligationId?: string;
	readonly researchMapId?: string;
	readonly requestedNodePath?: string;
}

export type CorpusNodeResolution =
	| { readonly status: "RESOLVED"; readonly nodePath: string; readonly reason: string }
	| { readonly status: "BLOCKED_PLACEMENT"; readonly reason: string };

export interface CorpusArchiveReconcileResult {
	readonly projectId: string;
	readonly recoveredIntentIds: readonly string[];
	readonly completedIntentIds: readonly string[];
	readonly failedIntentIds: readonly string[];
}

/** Narrow post-commit projection seam; implementations never own Research truth. */
export interface CorpusArchiveSink {
	recordAcceptedEffect(source: CorpusArchiveEffectSource, effect: AcceptedEffect, committed: ResearchProjectState): Promise<void>;
	recordPromotionClosure(committed: ResearchProjectState, authority: FinalProofAuthority): Promise<void>;
	reconcile(projectId: string, intentId?: string): Promise<CorpusArchiveReconcileResult>;
}
