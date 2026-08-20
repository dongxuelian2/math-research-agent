"""Long-horizon natural-language mathematics project layer for OpenProver."""

from .audit_protocol import AuditResult
from .architecture_critic import (
    ArchitectureCritic,
    ArchitectureCriticIndependenceReceipt,
    ArchitectureCriticVerdict,
    evaluate_patch,
)
from .architecture_patch import (
    ArchitecturePatch,
    ArchitecturePatchApplication,
    PatchAuthorization,
    PatchAuthorizationStatus,
    PatchClassification,
    PatchObligationAddition,
    PatchOperationKind,
    ScopeTransfer,
    ScopeTransferDisposition,
    classify_patch,
)
from .architecture_review import (
    ArchitectureDimension,
    ArchitectureDimensionFinding,
    ArchitectureReview,
    ArchitectureReviewVerdict,
    GovernanceActor,
)
from .canonical_artifacts import (
    CanonicalArtifactResolver,
    CanonicalPurpose,
    CanonicalResolution,
    CanonicalResolutionStatus,
    CanonicalSourceRequirement,
)
from .checkpoint_migration import (
    CheckpointClassification,
    CheckpointInspection,
    CheckpointMigrationResult,
    LegacyCheckpointMigrator,
    MigrationProvenance,
    checkpoint_policy_fingerprint,
    inspect_legacy_checkpoint,
)
from .claim_snapshot import (
    ClaimSnapshot,
    SnapshotComparison,
    SnapshotComparisonStatus,
    SnapshotDisposition,
    compare_claim_snapshots,
    validate_claim_snapshot_for_root_synthesis,
)
from .gemini_provider import GeminiClient, GeminiProviderError
from .formalization import run_formalization
from .governance import (
    ArchitectureReviewClock,
    ArchitectureReviewTrigger,
    GovernanceController,
    GovernanceThresholds,
)
from .observatory import build_snapshot, run_server
from .schemas import (
    AuditCriteriaSchema,
    AuditResultSchema,
    FormalizationResultSchema,
    LiteratureResultSchema,
    PipelineResultSchema,
    WorkerEventSchema,
)
from .showcase_demo import run_showcase
from .campaign import (
    CampaignEngine,
    CampaignStore,
    FailureMap,
    PreSubmitGate,
    ReplayPolicy,
)
from .project import ProjectStore
from .providers import ProviderCapabilities, provider_capabilities
from .literature import (
    ExternalAuthorityRegistry,
    LiteratureMemory,
    LiteratureSynthesis,
    LiteratureTaskExecutor,
    NegativeLiteratureMemory,
)
from .scholarly import (
    CrossrefProvider,
    DocumentArtifact,
    FullTextRetriever,
    OpenAlexProvider,
    ScholarlyProviderError,
    ScholarlyRecord,
    ScholarlySearchAdapter,
)
from .pipelines import (
    AsyncDAGScheduler,
    AsynchronousPipelineRuntime,
    AtomicResourceBudget,
    TaskExecutionContext,
)
from .retrieval import ContextBuilder
from .routing import ModelRouter, RoutedLLMClient
from .scheduler import (
    ResearchProfile,
    RoleScheduler,
    StopController,
    StrategyFingerprint,
    StrategyFingerprintStore,
    resolve_profile,
)
from .state_machine import AuditGate, InvalidTransition
from .trust_kernel import FoundationRegistry, SemanticRegistry, TrustKernel
from .truth_identity import (
    AssertionIdentity,
    AssertionKind,
    AssumptionSnapshot,
    AuthorityBinding,
    AuthorityKind,
    DependencyEntry,
    DependencySnapshot,
)
from .truth_mutation import TruthMutationIntent, TruthMutationReceipt
from .structural_effect import (
    StructuralEffect,
    StructuralEffectKind,
    StructuralEffectLevel,
    StructuralEffectValidation,
    classify_structural_effect,
)
from .structural_probe import (
    ProbeBudget,
    StructuralProbe,
    StructuralProbePlan,
    StructuralProbeResult,
)
from .truth_store import (
    CurrentTruth,
    TruthMutationBlocked,
    TruthStoreFacade,
    TruthValidationError,
)

__all__ = [
    "ArchitectureCritic",
    "ArchitectureCriticIndependenceReceipt",
    "ArchitectureCriticVerdict",
    "ArchitectureDimension",
    "ArchitectureDimensionFinding",
    "ArchitecturePatch",
    "ArchitecturePatchApplication",
    "ArchitectureReview",
    "ArchitectureReviewClock",
    "ArchitectureReviewTrigger",
    "ArchitectureReviewVerdict",
    "GovernanceActor",
    "GovernanceController",
    "GovernanceThresholds",
    "PatchAuthorization",
    "PatchAuthorizationStatus",
    "PatchClassification",
    "PatchObligationAddition",
    "PatchOperationKind",
    "ProbeBudget",
    "ScopeTransfer",
    "ScopeTransferDisposition",
    "StructuralEffect",
    "StructuralEffectKind",
    "StructuralEffectLevel",
    "StructuralEffectValidation",
    "StructuralProbe",
    "StructuralProbePlan",
    "StructuralProbeResult",
    "AuditGate",
    "AuditCriteriaSchema",
    "AuditResult",
    "AuditResultSchema",
    "AsyncDAGScheduler",
    "AsynchronousPipelineRuntime",
    "AtomicResourceBudget",
    "CampaignEngine",
    "CampaignStore",
    "CanonicalArtifactResolver",
    "CanonicalPurpose",
    "CanonicalResolution",
    "CanonicalResolutionStatus",
    "CanonicalSourceRequirement",
    "ClaimSnapshot",
    "CheckpointClassification",
    "CheckpointInspection",
    "CheckpointMigrationResult",
    "ContextBuilder",
    "CurrentTruth",
    "AssertionIdentity",
    "AssertionKind",
    "AssumptionSnapshot",
    "AuthorityBinding",
    "AuthorityKind",
    "DependencyEntry",
    "DependencySnapshot",
    "FoundationRegistry",
    "GeminiClient",
    "GeminiProviderError",
    "FailureMap",
    "FormalizationResultSchema",
    "ExternalAuthorityRegistry",
    "InvalidTransition",
    "LiteratureMemory",
    "LiteratureResultSchema",
    "LiteratureSynthesis",
    "LiteratureTaskExecutor",
    "LegacyCheckpointMigrator",
    "ModelRouter",
    "MigrationProvenance",
    "NegativeLiteratureMemory",
    "CrossrefProvider",
    "DocumentArtifact",
    "FullTextRetriever",
    "OpenAlexProvider",
    "ScholarlyProviderError",
    "ScholarlyRecord",
    "ScholarlySearchAdapter",
    "ProjectStore",
    "ProviderCapabilities",
    "PipelineResultSchema",
    "PreSubmitGate",
    "ReplayPolicy",
    "ResearchProfile",
    "RoleScheduler",
    "RoutedLLMClient",
    "SemanticRegistry",
    "StopController",
    "SnapshotComparison",
    "SnapshotComparisonStatus",
    "SnapshotDisposition",
    "StrategyFingerprint",
    "StrategyFingerprintStore",
    "TaskExecutionContext",
    "TrustKernel",
    "TruthStoreFacade",
    "TruthValidationError",
    "TruthMutationBlocked",
    "TruthMutationIntent",
    "TruthMutationReceipt",
    "WorkerEventSchema",
    "run_formalization",
    "build_snapshot",
    "checkpoint_policy_fingerprint",
    "classify_patch",
    "classify_structural_effect",
    "compare_claim_snapshots",
    "inspect_legacy_checkpoint",
    "evaluate_patch",
    "resolve_profile",
    "provider_capabilities",
    "run_server",
    "run_showcase",
    "validate_claim_snapshot_for_root_synthesis",
]
