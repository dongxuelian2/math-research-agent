"""Long-horizon natural-language mathematics project layer for OpenProver."""

from .audit_protocol import AuditResult
from .campaign import (
    CampaignEngine,
    CampaignStore,
    FailureMap,
    PreSubmitGate,
    ReplayPolicy,
)
from .project import ProjectStore
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
    "AuditResult",
    "AsyncDAGScheduler",
    "AsynchronousPipelineRuntime",
    "AtomicResourceBudget",
    "CampaignEngine",
    "CampaignStore",
    "ContextBuilder",
    "FoundationRegistry",
    "FailureMap",
    "ExternalAuthorityRegistry",
    "InvalidTransition",
    "LiteratureMemory",
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
    "resolve_profile",
]
