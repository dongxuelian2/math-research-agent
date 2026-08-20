"""Long-horizon natural-language mathematics project layer for OpenProver."""

from .audit_protocol import AuditResult
from .canonical_artifacts import (
    CanonicalArtifactResolver,
    CanonicalPurpose,
    CanonicalResolution,
    CanonicalResolutionStatus,
    CanonicalSourceRequirement,
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
    "ContextBuilder",
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
    "ModelRouter",
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
    "StrategyFingerprint",
    "StrategyFingerprintStore",
    "TaskExecutionContext",
    "TrustKernel",
    "WorkerEventSchema",
    "run_formalization",
    "build_snapshot",
    "resolve_profile",
    "provider_capabilities",
    "run_server",
    "run_showcase",
]
