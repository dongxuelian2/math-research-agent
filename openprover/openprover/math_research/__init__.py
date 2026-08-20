"""Long-horizon natural-language mathematics project layer for OpenProver."""

from .audit_protocol import AuditResult
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
from .truth_store import CurrentTruth, TruthStoreFacade, TruthValidationError

__all__ = [
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
    "WorkerEventSchema",
    "run_formalization",
    "build_snapshot",
    "checkpoint_policy_fingerprint",
    "compare_claim_snapshots",
    "inspect_legacy_checkpoint",
    "resolve_profile",
    "provider_capabilities",
    "run_server",
    "run_showcase",
    "validate_claim_snapshot_for_root_synthesis",
]
