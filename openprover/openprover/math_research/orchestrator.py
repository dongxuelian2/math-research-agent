"""Outer orchestration and audit gate around OpenProver's core Prover."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from .canonical_artifacts import (
    CanonicalArtifactResolver,
    CanonicalSourceRequirement,
    authority_promotion_decision,
)
from .checkpoint_migration import (
    CURRENT_RUN_STATE_SCHEMA,
    LegacyCheckpointMigrator,
    checkpoint_policy_fingerprint,
)
from .campaign import (
    FailureMap,
    ReplayPolicy,
    classify_provider_exception,
)
from .gemini_provider import GeminiProviderError
from .codex_cli_provider import CodexCLIProviderError
from .openai_provider import OpenAIProviderError
from .candidate_engine import CandidateEngine
from .audit_coordinator import AuditCoordinator
from .project import ProjectError, ProjectStore, utc_now
from .pipelines import AsyncDAGScheduler, AsynchronousPipelineRuntime
from .literature import ExternalAuthorityRegistry, LiteratureTaskExecutor
from .providers import (
    create_client,
    is_mock_config,
    load_model_config,
)
from .retrieval import ContextBuilder
from .routing import ModelRouter, RoutedLLMClient
from .schemas import (
    PipelineResultSchema,
    SchemaError,
    parse_structured_response,
)
from .scheduler import (
    StopController,
    StrategyFingerprint,
    StrategyFingerprintStore,
)
from .state_machine import AuditGate


PHASES = ("CREATED", "CONTEXT_READY", "CANDIDATE_READY", "AUDITS_READY", "COMPLETE")
CHECKPOINT_PHASE = "CHECKPOINT"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _usage_metrics(client: object | None) -> dict:
    if client is None:
        return {}
    usage = getattr(client, "total_usage", None)
    return dict(usage) if isinstance(usage, dict) else {}


def _sum_usage(clients: list[object]) -> dict:
    result: dict[str, int | bool] = {}
    for client in clients:
        for key, value in _usage_metrics(client).items():
            if isinstance(value, bool):
                result[key] = bool(result.get(key, False) or value)
            else:
                result[key] = int(result.get(key, 0)) + int(value)
    return result


def _api_request_count(client: object | None) -> int:
    if client is None:
        return 0
    if hasattr(client, "api_request_count"):
        return int(getattr(client, "api_request_count"))
    return int(getattr(client, "request_count", 0))


def build_run_preview(
    project: ProjectStore,
    target_id: str,
    *,
    config_path: str | Path,
    worker_count: int = 3,
    expand_context: bool = False,
) -> dict:
    """Build a read-only dry-run report without creating a run or client."""
    if worker_count < 1:
        raise ProjectError("worker_count must be positive")
    target = project.load_theorem(target_id)
    config_path = Path(config_path).resolve()
    config = load_model_config(config_path)
    router = ModelRouter(config)
    package = ContextBuilder(project).build(target_id, expand=expand_context)
    role_names = [
        "planner",
        "worker",
        "counterexample_hunter",
        "dependency_auditor",
        "exhaustiveness_auditor",
        "boundary_auditor",
        "final_proof_auditor",
        "literature_lead",
        "literature_searcher",
        "literature_reader",
        "literature_synthesizer",
        "literature_authority_auditor",
        "architecture_audit",
    ]
    assignments = {}
    credentials = {}
    for role_name in role_names:
        route = router.resolve(role_name, obligation_id=target_id, reserve=False)
        role = route.config
        provider = role.get("provider")
        assignments[role_name] = {
            key: role[key]
            for key in (
                "provider",
                "model",
                "reasoning_effort",
                "timeout_seconds",
                "max_retries",
                "max_output_tokens",
                "answer_reserve",
                "sandbox",
                "executable",
            )
            if key in role
        }
        assignments[role_name].update(
            {
                "default_tier": router.default_tier(role_name),
                "resolved_tier": route.tier,
                "fallback": route.fallback,
                "fallback_reason": route.fallback_reason,
            }
        )
        if provider == "gemini":
            credentials[role_name] = {
                "environment_variable": "GEMINI_API_KEY",
                "present": bool(os.environ.get("GEMINI_API_KEY")),
            }
        elif provider == "vertex_gemini":
            credentials[role_name] = {
                "environment_variables": [
                    "GOOGLE_CLOUD_PROJECT",
                    "GOOGLE_CLOUD_ACCESS_TOKEN",
                ],
                "project_configured": bool(
                    role.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT")
                ),
                "access_token_configured": bool(
                    role.get("access_token") or os.environ.get("GOOGLE_CLOUD_ACCESS_TOKEN")
                ),
            }
    return {
        "dry_run": True,
        "request_sent": False,
        "project": str(project.root),
        "target": {
            "id": target["id"],
            "title": target["title"],
            "status": target["status"],
        },
        "worker_count": worker_count,
        "config_path": str(config_path),
        "isolation": bool(config.get("isolation", True)),
        "context": {
            "expanded": expand_context,
            "allowed_dependencies": [item["id"] for item in package.data["allowed_dependencies"]],
            "blocked_dependencies": [item["id"] for item in package.data["blocked_dependencies"]],
            "dependency_cycles": package.data["dependency_cycles"],
            "source_count": len(package.data["sources"]),
            "failed_route_count": len(package.data["failed_routes"]),
            "character_count": len(package.markdown),
            "utf8_bytes": len(package.markdown.encode("utf-8")),
        },
        "roles": assignments,
        "credentials": credentials,
    }


class ResearchOrchestrator:
    """Drive a durable, resumable project run around the upstream engine."""

    def __init__(
        self,
        project: ProjectStore,
        target_id: str,
        *,
        config_path: str | Path,
        worker_count: int = 3,
        dry_run: bool = False,
        resume: str | Path | None = None,
        expand_context: bool = False,
        run_id: str | None = None,
        campaign_id: str | None = None,
        parent_run_id: str | None = None,
        repair_cycle: int = 0,
        hard_submit_gate: bool = False,
        replay_policy: ReplayPolicy | None = None,
        infrastructure_retries: int = 0,
        budget_limit_seconds: int | None = None,
        initial_worker_count: int | None = None,
        role_scheduling: bool = False,
        secondary_verification: bool = False,
        stop_controller: StopController | None = None,
        campaign_routing_override: dict | None = None,
        pipeline_state: dict | None = None,
        pipeline_handlers: dict | None = None,
    ):
        if worker_count < 1:
            raise ProjectError("worker_count must be positive")
        self.project = project
        self.target_id = target_id
        self.target = project.load_theorem(target_id)
        self.config_path = Path(config_path).resolve()
        self.config = load_model_config(self.config_path)
        self.worker_count = worker_count
        self.dry_run = dry_run
        self.expand_context = expand_context
        self.campaign_id = campaign_id
        self.parent_run_id = parent_run_id
        self.repair_cycle = int(repair_cycle)
        self.hard_submit_gate = bool(hard_submit_gate)
        self.replay_policy = replay_policy
        self.canonical_authority: list[dict] = []
        self.infrastructure_retries = max(0, int(infrastructure_retries))
        self.budget_limit_seconds = (
            int(budget_limit_seconds) if budget_limit_seconds is not None else None
        )
        self.initial_worker_count = int(initial_worker_count or worker_count)
        self.role_scheduling = bool(role_scheduling or self.config.get("tiers"))
        self.secondary_verification = bool(secondary_verification)
        self.stop_controller = stop_controller
        self.campaign_routing_override = copy.deepcopy(
            campaign_routing_override or self.config.get("campaign_override") or {}
        )
        self.pipeline_handlers_override = dict(pipeline_handlers or {})
        self.pipeline_runtime: AsynchronousPipelineRuntime | None = None
        self._pipeline_monitor_stop = threading.Event()
        self._pipeline_monitor_thread: threading.Thread | None = None
        self.candidate_engine = CandidateEngine(self)
        self.audit_coordinator = AuditCoordinator(self)
        if self.initial_worker_count < 1 or self.initial_worker_count > self.worker_count:
            raise ProjectError("initial_worker_count must be between 1 and worker_count")
        if dry_run:
            self.run_dir = None
            self.state_path = None
            self.state = {}
            self.started = time.perf_counter()
            self.metrics = {}
            self.model_router = ModelRouter(self.config)
            self.pipeline_scheduler = AsyncDAGScheduler(config=self.config)
            return
        self.run_dir = self._resolve_run_dir(resume, run_id=run_id)
        self.state_path = self.run_dir / "state.json"
        self.state = self._load_or_initialize_state()
        self.started = time.perf_counter()
        self.metrics: dict[str, dict] = self.state.get("metrics", {})
        project_override = self.project.load_project().get("model_routing", {})
        immutable_complete = self.state.get("phase") == "COMPLETE"
        self.model_router = ModelRouter(
            self.config,
            state_path=(None if immutable_complete else self.run_dir / "routing_state.json"),
            campaign_override=self.campaign_routing_override,
            project_override=project_override,
        )
        self.pipeline_scheduler = AsyncDAGScheduler(
            state=(pipeline_state if pipeline_state is not None else None),
            state_path=(None if immutable_complete else self.run_dir / "pipeline_state.json"),
            config=self.config,
        )
        if pipeline_state is not None and not immutable_complete:
            self.pipeline_scheduler.state_path = self.run_dir / "pipeline_state.json"
            self.pipeline_scheduler._save()
        self._refresh_canonical_authority()
        if not immutable_complete:
            self._configure_pipeline_runtime()
        if not immutable_complete:
            self._ensure_target_pipeline_obligation()

    def _resolve_run_dir(self, resume: str | Path | None, *, run_id: str | None = None) -> Path:
        runs_dir = self.project.root / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        if resume and run_id:
            raise ProjectError("Specify either resume or run_id, not both")
        if run_id:
            self.project.validate_id(run_id)
            candidate = runs_dir / run_id
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        if resume:
            if str(resume).lower() == "latest":
                candidates = sorted(
                    (path for path in runs_dir.glob(f"{self.target_id}-*") if path.is_dir()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if not candidates:
                    raise ProjectError(f"No run found to resume for {self.target_id}")
                return candidates[0]
            candidate = Path(resume)
            if not candidate.is_absolute():
                candidate = runs_dir / candidate
            candidate = candidate.resolve()
            try:
                candidate.relative_to(runs_dir.resolve())
            except ValueError as exc:
                raise ProjectError(
                    "Resume directory must be inside the project's runs directory"
                ) from exc
            if not candidate.is_dir():
                raise ProjectError(f"Resume directory not found: {candidate}")
            source_state_path = candidate / "state.json"
            if source_state_path.is_file():
                try:
                    source_state = json.loads(source_state_path.read_text(encoding="utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    source_state = None
                if (
                    not isinstance(source_state, dict)
                    or source_state.get("schema_version") != CURRENT_RUN_STATE_SCHEMA
                ):
                    migration = LegacyCheckpointMigrator(
                        self.project,
                        config_path=self.config_path,
                        target_policy_fingerprint=checkpoint_policy_fingerprint(self.config),
                    ).prepare(
                        candidate,
                        expected_target_id=self.target_id,
                        expected_campaign_id=self.campaign_id,
                    )
                    if not migration.resumable:
                        raise ProjectError(
                            f"Legacy checkpoint classified {migration.classification}: "
                            f"{migration.reason}; provenance: {migration.provenance_path}"
                        )
                    return migration.runtime_run_dir
            return candidate
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = runs_dir / f"{self.target_id}-{timestamp}"
        candidate = base
        counter = 1
        while candidate.exists():
            counter += 1
            candidate = runs_dir / f"{base.name}-{counter}"
        candidate.mkdir(parents=True)
        return candidate

    def _load_or_initialize_state(self) -> dict:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("target_id") != self.target_id:
                raise ProjectError("Resume run target does not match --target")
            if state.get("campaign_id") != self.campaign_id:
                raise ProjectError("Run belongs to a different campaign")
            if state.get("schema_version") != CURRENT_RUN_STATE_SCHEMA:
                raise ProjectError("Unsupported run state schema after checkpoint migration")
            if state.get("phase") == "COMPLETE":
                return state
            required = {"routing_state_file", "pipeline_state_file"}
            if not required <= set(state):
                raise ProjectError(
                    "Run state is incomplete; start a new run instead of migrating it"
                )
            return state
        state = {
            "schema_version": 2,
            "run_id": self.run_dir.name,
            "target_id": self.target_id,
            "phase": "CREATED",
            "status": "RUNNING",
            "dry_run": self.dry_run,
            "config_path": str(self.config_path),
            "worker_count": self.worker_count,
            "created_at": utc_now(),
            "last_updated": utc_now(),
            "metrics": {},
            "failure_reasons": [],
            "campaign_id": self.campaign_id,
            "parent_run_id": self.parent_run_id,
            "repair_cycle": self.repair_cycle,
            "hard_submit_gate": self.hard_submit_gate,
            "replay_policy_hash": (self.replay_policy.policy_hash if self.replay_policy else None),
            "budget_limit_seconds": self.budget_limit_seconds,
            "initial_worker_count": self.initial_worker_count,
            "role_scheduling": self.role_scheduling,
            "secondary_verification": self.secondary_verification,
            "routing_state_file": "routing_state.json",
            "pipeline_state_file": "pipeline_state.json",
        }
        _write_json(self.state_path, state)
        return state

    def _ensure_target_pipeline_obligation(self) -> None:
        snapshot = self.pipeline_scheduler.snapshot()
        all_requirements = (
            self.replay_policy.canonical_source_requirements
            if self.replay_policy
            else tuple(
                CanonicalSourceRequirement.from_dict(item)
                for item in self.state.get("canonical_source_requirements", [])
            )
        )
        if self.target_id in snapshot["obligations"]:
            self.pipeline_scheduler.bind_canonical_authority(
                self.target_id,
                requirements=[
                    item.to_dict()
                    for item in all_requirements
                    if item.requesting_obligation_id == self.target_id
                ],
                resolutions=[
                    item
                    for item in self.canonical_authority
                    if item.get("requesting_obligation_id") == self.target_id
                ],
            )
            return
        requirements = [
            item for item in all_requirements if item.requesting_obligation_id == self.target_id
        ]
        self.pipeline_scheduler.add_obligation(
            self.target_id,
            target_statement=self.target["statement"],
            branch_id=self.target.get("branch", "main"),
            dependencies=[],
            # The target already existed before this run.  Literature-first is
            # scoped to newly created obligations, not a global campaign stop.
            literature_first=False,
            dual_track=False,
            context={"existing_campaign_target": True},
            canonical_source_requirements=[item.to_dict() for item in requirements],
            canonical_authority=[
                item
                for item in self.canonical_authority
                if item.get("requesting_obligation_id") == self.target_id
            ],
        )

    def _refresh_canonical_authority(self) -> None:
        """Resolve or revalidate every declared body on new and resumed runs."""

        requirements = tuple(
            self.replay_policy.canonical_source_requirements if self.replay_policy else ()
        )
        if not requirements:
            persisted_requirements = self.state.get("canonical_source_requirements", [])
            if isinstance(persisted_requirements, list):
                requirements = tuple(
                    CanonicalSourceRequirement.from_dict(item) for item in persisted_requirements
                )
        roots = (
            list(self.replay_policy.canonical_source_roots)
            if self.replay_policy
            else list(self.state.get("canonical_source_roots", []))
        )
        project_roots = self.project.load_project().get("canonical_source_roots", [])
        if isinstance(project_roots, list):
            roots.extend(project_roots)
        resolver = CanonicalArtifactResolver(
            self.project,
            configured_roots=roots,
            run_dir=self.run_dir,
        )
        previous_resolutions = self.state.get("canonical_authority", [])
        self.canonical_authority = [
            item.to_dict()
            for item in resolver.resolve_all(
                requirements,
                previous_resolutions=(
                    previous_resolutions if isinstance(previous_resolutions, list) else []
                ),
            )
        ]
        self.state["canonical_source_requirements"] = [item.to_dict() for item in requirements]
        self.state["canonical_source_roots"] = list(dict.fromkeys(str(item) for item in roots))
        self.state["canonical_authority"] = copy.deepcopy(self.canonical_authority)
        self.state["canonical_authority_revalidated_at"] = utc_now()
        _write_json(
            self.run_dir / "canonical_authority" / "resolution.json",
            {
                "schema_version": 1,
                "revalidated_at": self.state["canonical_authority_revalidated_at"],
                "resolutions": self.canonical_authority,
            },
        )
        _write_json(self.state_path, self.state)
        snapshot = self.pipeline_scheduler.snapshot()
        for obligation_id in snapshot.get("obligations", {}):
            scoped_requirements = [
                item.to_dict()
                for item in requirements
                if item.requesting_obligation_id == obligation_id
            ]
            scoped_resolutions = [
                item
                for item in self.canonical_authority
                if item.get("requesting_obligation_id") == obligation_id
            ]
            if scoped_requirements or snapshot["obligations"][obligation_id].get(
                "canonical_source_requirements"
            ):
                self.pipeline_scheduler.bind_canonical_authority(
                    obligation_id,
                    requirements=scoped_requirements,
                    resolutions=scoped_resolutions,
                )

    def _configure_pipeline_runtime(self) -> None:
        """Create the single runtime that owns async task futures for this run."""
        if self.pipeline_runtime is not None:
            return
        scholarly_adapter = None
        document_retriever = None
        literature_config = self.config.get("literature", {})
        if isinstance(literature_config, dict) and literature_config.get(
            "scholarly_metadata_adapter", False
        ):
            from .scholarly import FullTextRetriever, OpenAlexProvider, ScholarlySearchAdapter

            scholarly_adapter = ScholarlySearchAdapter(
                [OpenAlexProvider(cache_dir=self.run_dir / "literature" / "scholarly-cache")]
            )
            if literature_config.get("full_text_retrieval", False):
                document_retriever = FullTextRetriever(
                    self.run_dir / "literature" / "fulltext-cache"
                )
        authority_registry = ExternalAuthorityRegistry(self.run_dir / "literature")
        literature_handler = LiteratureTaskExecutor(
            self.pipeline_scheduler,
            self.model_router,
            client_factory=create_client,
            archive_dir=self.run_dir / "archive" / "literature",
            working_dir=self.run_dir / "gemini" / "literature",
            external_transmission_approved=bool(
                self.config.get("literature", {}).get("external_transmission_approved", False)
            ),
            authority_registry=authority_registry,
            scholarly_adapter=scholarly_adapter,
            document_retriever=document_retriever,
        )
        handlers = {
            "proof": self._pipeline_proof_handler,
            "literature": literature_handler,
            "verification": self._pipeline_verification_handler,
        }
        handlers.update(self.pipeline_handlers_override)
        self.pipeline_runtime = AsynchronousPipelineRuntime(
            self.pipeline_scheduler,
            handlers,
            max_workers=max(3, self.worker_count),
        )

    def _pipeline_llm_handler(
        self,
        task: dict,
        context,
        *,
        role: str,
        prompt_payload: dict | None = None,
        system_prompt: str | None = None,
        minimum_tier: str | None = None,
    ) -> dict:
        obligation = self.pipeline_scheduler.snapshot()["obligations"][task["obligation_id"]]
        if minimum_tier:
            self.model_router.escalate(
                task["obligation_id"],
                reason="external_theorem_applicability",
                minimum_tier=minimum_tier,
            )
        client = RoutedLLMClient(
            self.model_router,
            client_factory=create_client,
            default_role=role,
            archive_dir=self.run_dir / "archive" / "pipeline" / task["task_id"],
            working_dir=self.run_dir / "gemini" / "pipeline" / task["task_id"],
        )
        context.set_handle(client)
        prompt = f"[Worker role: {role}]\n[Obligation ID: {task['obligation_id']}]\n" + json.dumps(
            prompt_payload
            or {
                "target_statement": obligation.get("target_statement"),
                "task": task,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            response = client.call(
                prompt,
                system_prompt or f"You are the bounded production {role}. Return one JSON object.",
                label=f"{role}_{task['task_id']}",
                archive_path=self.run_dir / "archive" / "pipeline" / f"{task['task_id']}.md",
                response_schema=PipelineResultSchema,
            )
            if context.cancel_event.is_set():
                raise RuntimeError("task-scoped cancellation requested")
            try:
                value = parse_structured_response(response, PipelineResultSchema).model_dump(
                    mode="python"
                )
            except SchemaError as exc:
                raise ProjectError(f"{role} returned invalid structured output: {exc}") from exc
            value.setdefault("routing", response.get("routing", {}))
            return value
        finally:
            client.cleanup()

    def _pipeline_proof_handler(self, task: dict, context) -> dict:
        return self._pipeline_llm_handler(task, context, role="constructive")

    def _pipeline_verification_handler(self, task: dict, context) -> dict:
        payload = task.get("payload", {})
        if task.get("role") == "reconstruction" and payload.get(
            "applicability_reconstruction_required"
        ):
            return self._external_applicability_reconstruction(task, context)
        if task.get("role") == "theorem_verifier" and payload.get(
            "independent_applicability_verification"
        ):
            return self._independent_applicability_verification(task, context)
        value = self._pipeline_llm_handler(task, context, role="theorem_verifier")
        if "verdict" not in value:
            value["verdict"] = "UNCERTAIN"
        value.setdefault("all_required_gates", value.get("verdict") == "CORRECT")
        return value

    def _external_applicability_reconstruction(self, task: dict, context) -> dict:
        """Use a research-tier model to build a structured mathematical mapping."""

        snapshot = self.pipeline_scheduler.snapshot()
        obligation = snapshot["obligations"].get(task["obligation_id"], {})
        candidate = obligation.get("authority_candidate") or {}
        authority_id = candidate.get("authority_id")
        registry = ExternalAuthorityRegistry(self.run_dir / "literature")
        authority = registry.require_verified_source(
            str(authority_id or ""), obligation_id=task["obligation_id"]
        )
        app_context = self.pipeline_scheduler.applicability_context(task["obligation_id"])
        payload = {
            "exact_current_obligation": app_context["current_target"],
            "authorized_current_assumptions": app_context["current_assumptions"],
            "authorized_local_lemmas": app_context["authorized_local_lemmas"],
            "assumption_snapshot_hash": app_context["assumption_snapshot_hash"],
            "verified_source_theorem": {
                key: authority.get(key)
                for key in (
                    "authority_id",
                    "theorem_number",
                    "page_or_section",
                    "exact_statement",
                    "hypotheses",
                    "notation_map",
                    "statement_hash",
                )
            },
            "required_output": {
                "external_hypotheses": ["string"],
                "notation_map": {"paper_symbol": "project_symbol"},
                "hypothesis_mapping": [
                    {
                        "external_hypothesis": "string",
                        "satisfied_by": "string",
                        "status": "PROVED|NOT_APPLICABLE|UNRESOLVED|FAILED",
                        "evidence": ["string"],
                    }
                ],
                "conclusion_mapping": {
                    "external_conclusion": "string",
                    "target": "string",
                    "bridge_steps": ["string"],
                    "status": "PROVED|UNRESOLVED|FAILED",
                },
                "exception_analysis": {
                    "excluded_cases": ["string"],
                    "analysis": "string",
                    "status": "PROVED|NOT_APPLICABLE|UNRESOLVED|FAILED",
                },
                "direction_analysis": {
                    "direction": "external conclusion => target",
                    "analysis": "string",
                    "status": "PROVED|UNRESOLVED|FAILED",
                },
                "normalization_analysis": {
                    "analysis": "string",
                    "status": "PROVED|UNRESOLVED|FAILED",
                },
                "required_local_lemmas": ["authorized lemma id only"],
                "unresolved_conditions": ["string"],
            },
        }
        value = self._pipeline_llm_handler(
            task,
            context,
            role="reconstruction",
            prompt_payload=payload,
            minimum_tier="research",
            system_prompt=(
                "Construct, but do not approve, an external-theorem applicability mapping. "
                "Use only the supplied authorized assumptions and CLOSED local lemmas. Check each "
                "hypothesis, notation convention, implication direction, conclusion bridge, and "
                "exception. Treat restrictions embedded in a theorem's grammatical subject (for "
                "example 'an orthonormal basis' and 'an m-dimensional subspace') as external "
                "hypotheses and emit at least one hypothesis_mapping item. When the exact current "
                "obligation is textually identical to the complete verified theorem, record each "
                "embedded restriction as NOT_APPLICABLE to ambient assumptions with evidence that "
                "it remains inside the conditional/universal theorem statement. Ambiguity must be "
                "UNRESOLVED. Return exactly one JSON object matching "
                "required_output; never emit a single hypotheses_match boolean."
            ),
        )
        routing = value.get("routing") if isinstance(value.get("routing"), dict) else {}
        reconstruction = {
            "obligation_id": task["obligation_id"],
            "authority_id": authority["authority_id"],
            "source_theorem_id": authority["authority_id"],
            "current_target": app_context["current_target"],
            "current_assumptions": app_context["current_assumptions"],
            "external_statement": authority["exact_statement"],
            "external_hypotheses": value.get("external_hypotheses")
            or authority.get("hypotheses")
            or [],
            "notation_map": value.get("notation_map")
            if isinstance(value.get("notation_map"), dict)
            else {},
            "hypothesis_mapping": value.get("hypothesis_mapping"),
            "conclusion_mapping": value.get("conclusion_mapping"),
            "exception_analysis": value.get("exception_analysis"),
            "direction_analysis": value.get("direction_analysis"),
            "normalization_analysis": value.get("normalization_analysis"),
            "required_local_lemmas": value.get("required_local_lemmas") or [],
            "authorized_local_lemmas": app_context["authorized_local_lemmas"],
            "unresolved_conditions": value.get("unresolved_conditions") or [],
            "reconstructor_call_id": routing.get("call_id"),
            "reconstructor_model": routing.get("actual_model") or routing.get("model"),
            "reconstructor_tier": routing.get("actual_tier") or routing.get("tier"),
            "assumption_snapshot_hash": app_context["assumption_snapshot_hash"],
        }
        registered = registry.register_applicability_reconstruction(reconstruction)
        return {
            "verdict": "APPLICABILITY_CANDIDATE",
            "applicability_status": registered["status"],
            "authority_status": authority["status"],
            "authority_id": authority["authority_id"],
            "applicability_id": registered["applicability_id"],
            "assumption_snapshot_hash": registered["assumption_snapshot_hash"],
            "result_artifact": str(registry.root / registered["reconstruction_artifact_path"]),
            "routing": routing,
        }

    def _independent_applicability_verification(self, task: dict, context) -> dict:
        """Independently review the reconstruction and deterministically promote it."""

        registry = ExternalAuthorityRegistry(self.run_dir / "literature")
        app_id = str(task.get("payload", {}).get("applicability_id") or "")
        record = registry.load()["applicability_records"].get(app_id)
        if not record:
            raise ProjectError(f"Missing applicability reconstruction: {app_id}")
        authority = registry.require_verified_source(str(record["authority_id"]))
        app_context = self.pipeline_scheduler.applicability_context(task["obligation_id"])
        payload = {
            "exact_current_obligation": app_context["current_target"],
            "authorized_current_assumptions": app_context["current_assumptions"],
            "authorized_local_lemmas": app_context["authorized_local_lemmas"],
            "verified_source_theorem": authority,
            "reconstruction_artifact": record,
            "allowed_verdicts": [
                "APPLICABLE",
                "NOT_APPLICABLE",
                "UNCERTAIN",
                "INCOMPLETE_RECONSTRUCTION",
                "WRONG_DIRECTION",
                "HYPOTHESIS_MISMATCH",
                "EXCEPTION_MISMATCH",
                "UNAUTHORIZED_DEPENDENCY",
            ],
        }
        value = self._pipeline_llm_handler(
            task,
            context,
            role="theorem_verifier",
            prompt_payload=payload,
            minimum_tier="research",
            system_prompt=(
                "Independently verify the supplied external-theorem applicability reconstruction. "
                "Recheck every hypothesis, notation/normalization convention, exception, implication "
                "direction, conclusion bridge, and local dependency authorization. Do not trust the "
                "reconstructor's verdict. Return one JSON object with verdict from allowed_verdicts "
                "and a nonempty detail/evidence explanation. Any ambiguity is UNCERTAIN."
            ),
        )
        routing = value.get("routing") if isinstance(value.get("routing"), dict) else {}
        verification = {
            "verdict": str(value.get("verdict") or "UNCERTAIN").upper(),
            "detail": value.get("detail") or value.get("evidence"),
            "verifier_call_id": routing.get("call_id"),
            "verifier_model": routing.get("actual_model") or routing.get("model"),
            "verifier_tier": routing.get("actual_tier") or routing.get("tier"),
        }
        promoted = registry.verify_applicability(app_id, verification)
        return {
            "verdict": verification["verdict"],
            "authority_status": promoted["status"],
            "applicability_status": promoted["status"],
            "applicability_id": app_id,
            "assumption_snapshot_hash": promoted["assumption_snapshot_hash"],
            "applicability_verification_errors": promoted.get(
                "applicability_verification_errors", []
            ),
            "deterministic_applicability_promotion": promoted["status"]
            == "APPLICABLE_EXTERNAL_AUTHORITY",
            "result_artifact": str(registry.root / promoted["verifier_artifact_path"]),
            "routing": routing,
        }

    def _start_async_pipeline_monitor(self, *, include_proof: bool = False) -> None:
        if self.pipeline_runtime is None or self._pipeline_monitor_thread is not None:
            return
        self._pipeline_monitor_stop.clear()
        self.pipeline_runtime.start_window(
            {
                "proof": self.worker_count if include_proof else 0,
                "literature": self.worker_count,
                "verification": self.worker_count,
            }
        )

        def monitor() -> None:
            while not self._pipeline_monitor_stop.is_set():
                self.pipeline_runtime.start_window(
                    {
                        "proof": self.worker_count if include_proof else 0,
                        "literature": self.worker_count,
                        "verification": self.worker_count,
                    }
                )
                self.pipeline_runtime.poll()
                self._pipeline_monitor_stop.wait(0.05)

        self._pipeline_monitor_thread = threading.Thread(
            target=monitor,
            name=f"pipeline-runtime-{self.run_dir.name}",
            daemon=True,
        )
        self._pipeline_monitor_thread.start()

    def _stop_async_pipeline_monitor(self) -> None:
        self._pipeline_monitor_stop.set()
        thread = self._pipeline_monitor_thread
        if thread is not None:
            thread.join(timeout=5)
        self._pipeline_monitor_thread = None
        if self.pipeline_runtime is not None:
            self.pipeline_runtime.poll()

    def _drain_async_pipeline_tasks(self, *, max_rounds: int = 100) -> None:
        if self.pipeline_runtime is None:
            return
        for _ in range(max_rounds):
            self.pipeline_runtime.start_window(
                {
                    "proof": 0,
                    "literature": self.worker_count,
                    "verification": self.worker_count,
                }
            )
            completed = self.pipeline_runtime.poll()
            snapshot = self.pipeline_scheduler.snapshot()
            ready = any(
                snapshot["tasks"].get(task_id, {}).get("status") in {"READY", "RETRY_READY"}
                for queue in snapshot["queues"].values()
                for task_id in queue
            )
            if not self.pipeline_runtime.pending() and not ready:
                break
            if not completed:
                time.sleep(0.01)

    def close(self) -> None:
        self._stop_async_pipeline_monitor()
        if self.pipeline_runtime is not None:
            self.pipeline_runtime.shutdown(wait=True)
            self.pipeline_runtime = None

    def _checkpoint(self, phase: str, **updates) -> None:
        if phase not in PHASES and phase != CHECKPOINT_PHASE:
            raise ProjectError(f"Unknown run phase: {phase}")
        self.state.update(updates)
        self.state["phase"] = phase
        self.state["last_updated"] = utc_now()
        self.state["metrics"] = self.metrics
        _write_json(self.state_path, self.state)

    def _phase_at_least(self, phase: str) -> bool:
        current = self.state.get("phase", "CREATED")
        if current == CHECKPOINT_PHASE:
            current = self.state.get("resume_phase", "CREATED")
        return PHASES.index(current) >= PHASES.index(phase)

    def run(self, *, stop_after: str | None = None) -> dict:
        if self.dry_run:
            return build_run_preview(
                self.project,
                self.target_id,
                config_path=self.config_path,
                worker_count=self.worker_count,
                expand_context=self.expand_context,
            )
        if self.state.get("phase") == "COMPLETE":
            return self.state
        if self.state.get("phase") == CHECKPOINT_PHASE:
            self.state["phase"] = self.state.get("resume_phase", "CREATED")
            self.state["status"] = "RUNNING"
            self.state["resumptions"] = int(self.state.get("resumptions", 0)) + 1
            self.state["last_updated"] = utc_now()
            _write_json(self.state_path, self.state)
        if self.stop_controller is not None and self.stop_controller.requested():
            return self._graceful_stop_checkpoint(self.state.get("phase", "CREATED"))
        if self.target["status"] == "UNCLASSIFIED":
            raise ProjectError("UNCLASSIFIED imports require human classification before research")
        if self.target["status"] == "FROZEN":
            raise ProjectError("Target is FROZEN; explicitly unfreeze it before research")
        if self.target["status"] == "PROVED":
            reaudit = self.state.get("reaudit") or self.project.consume_reaudit_request()
            if not reaudit:
                raise ProjectError(
                    "Target is already PROVED; request re-audit explicitly instead of rerunning research"
                )
            self.state["reaudit"] = True
            self.target = self.project.transition(
                self.target_id,
                "IN_RESEARCH",
                actor="Human",
                reason=f"Explicit re-audit requested for run {self.run_dir.name}",
            )
            _write_json(self.state_path, self.state)

        self.project.set_current_target(self.target_id)
        if self.target["status"] in {"OPEN", "PARTIAL", "REJECTED"}:
            self.target = self.project.transition(
                self.target_id,
                "IN_RESEARCH",
                actor="MasterPlanner",
                reason=f"Research run {self.run_dir.name} started",
            )

        if not self._phase_at_least("CONTEXT_READY"):
            package = ContextBuilder(self.project).build(
                self.target_id,
                expand=self.expand_context,
                canonical_authority=self.canonical_authority,
            )
            ContextBuilder.write(package, self.run_dir / "context")
            if self.hard_submit_gate:
                self._append_hard_gate_contract()
            if self.parent_run_id:
                self._write_repair_context()
            self._checkpoint(
                "CONTEXT_READY",
                blocked_dependencies=[item["id"] for item in package.data["blocked_dependencies"]],
                dependency_cycles=package.data["dependency_cycles"],
            )
        if stop_after == "context":
            return self.state

        target_obligation = self.pipeline_scheduler.snapshot()["obligations"].get(
            self.target_id, {}
        )
        if str(target_obligation.get("status", "")).startswith("BLOCKED_AUTHORITY_"):
            # Only the dependent target is blocked.  Any independent tasks
            # already present in a resumed campaign remain dispatchable.
            self._drain_async_pipeline_tasks()
            status = str(target_obligation["status"])
            self._checkpoint(
                CHECKPOINT_PHASE,
                status=status,
                checkpoint_reason=status,
                resume_phase="CONTEXT_READY",
                canonical_authority_blockers=target_obligation.get("authority_blockers", []),
            )
            return self.state

        if not self._phase_at_least("CANDIDATE_READY"):
            try:
                self._run_openprover_candidate()
            except (GeminiProviderError, CodexCLIProviderError, OpenAIProviderError) as exc:
                status, details = classify_provider_exception(exc)
                self._checkpoint(
                    CHECKPOINT_PHASE,
                    status=status,
                    checkpoint_reason=status,
                    provider_error=details,
                    resume_phase="CONTEXT_READY",
                )
                return self.state
            candidate_path = self.run_dir / "CANDIDATE_PROOF.md"
            if not candidate_path.exists():
                if self.stop_controller is not None and self.stop_controller.requested():
                    return self._graceful_stop_checkpoint("CONTEXT_READY")
                if self.hard_submit_gate or self.campaign_id:
                    gate_path = self.run_dir / "pre_submit_gate.json"
                    pre_submit = (
                        json.loads(gate_path.read_text(encoding="utf-8"))
                        if gate_path.exists()
                        else {}
                    )
                    self._checkpoint(
                        CHECKPOINT_PHASE,
                        status="TIME_BUDGET_EXHAUSTED",
                        checkpoint_reason="TIME_BUDGET_EXHAUSTED",
                        resume_phase="CONTEXT_READY",
                        pre_submit_gate=pre_submit,
                    )
                    return self.state
                self.target = self.project.transition(
                    self.target_id,
                    "PARTIAL",
                    actor="MasterPlanner",
                    reason="OpenProver ended without a candidate proof",
                    audit_status="NOT_AUDITED",
                )
                self._checkpoint(
                    "COMPLETE",
                    status="PARTIAL",
                    failure_reasons=["OpenProver ended without PROOF.md"],
                    completed_at=utc_now(),
                )
                return self.state
            self.target = self.project.transition(
                self.target_id,
                "CANDIDATE_PROOF",
                actor="MasterPlanner",
                reason=f"OpenProver candidate produced in {self.run_dir.name}; not yet audited",
                audit_status="PENDING",
            )
            self._checkpoint("CANDIDATE_READY", candidate_file="CANDIDATE_PROOF.md")
        if stop_after == "candidate":
            return self.state
        if self.stop_controller is not None and self.stop_controller.requested():
            return self._graceful_stop_checkpoint("CANDIDATE_READY")

        # A full candidate is always a strategic-review event even if its
        # underlying local lemmas were produced at lower tiers.
        self.model_router.promote_high_value(
            self.target_id, theorem_level=True, proof_candidate=True
        )
        if not self._phase_at_least("AUDITS_READY"):
            if self.target["status"] == "CANDIDATE_PROOF":
                self.target = self.project.transition(
                    self.target_id,
                    "AUDITING",
                    actor="AuditCoordinator",
                    reason=f"Independent audit suite started for {self.run_dir.name}",
                    audit_status="RUNNING",
                )
            verification_task_id = self._claim_target_pipeline_task("verification")
            if verification_task_id:
                self.state["active_verification_task_id"] = verification_task_id
            audits, gate = self._run_audits_with_retry()
            _write_json(self.run_dir / "audits" / "gate.json", gate.to_dict())
            self._checkpoint(
                "AUDITS_READY",
                audit_verdicts={
                    name: data.get("domain_verdict", "INCONCLUSIVE")
                    for name, data in audits.items()
                },
                gate=gate.to_dict(),
            )
        else:
            gate_data = json.loads(
                (self.run_dir / "audits" / "gate.json").read_text(encoding="utf-8")
            )
            gate = AuditGate(
                **{
                    key: value
                    for key, value in gate_data.items()
                    if key in AuditGate.__dataclass_fields__
                }
            )
        if stop_after == "audits":
            return self.state

        if self.stop_controller is not None and self.stop_controller.requested():
            return self._graceful_stop_checkpoint("AUDITS_READY")

        if gate.passed and self.secondary_verification:
            secondary = self._run_secondary_verification()
            self.state["secondary_verification"] = secondary
            if not secondary["passed"]:
                gate.failure_reasons.extend(secondary["failure_reasons"])
                gate.execution_errors.extend(secondary["execution_errors"])
                gate.inconclusive_audits.extend(secondary["inconclusive_checks"])
                gate.final_auditor_pass = False
                _write_json(self.run_dir / "audits" / "gate.json", gate.to_dict())
            _write_json(self.state_path, self.state)

        verification_task_id = self.state.get("active_verification_task_id")
        if verification_task_id:
            task = self.pipeline_scheduler.snapshot()["tasks"].get(verification_task_id, {})
            if task.get("status") == "ACTIVE":
                self.pipeline_scheduler.complete_task(
                    verification_task_id,
                    {
                        "verdict": "CORRECT" if gate.passed else "FLAWED",
                        "all_required_gates": gate.passed,
                        "detail": gate.to_dict(),
                    },
                )
            self.state.pop("active_verification_task_id", None)
            _write_json(self.state_path, self.state)

        self._finalize(gate)
        return self.state

    def _claim_target_pipeline_task(self, pipeline: str) -> str | None:
        snapshot = self.pipeline_scheduler.snapshot()
        for task_id in snapshot["active"].get(pipeline, []):
            task = snapshot["tasks"].get(task_id, {})
            if task.get("obligation_id") == self.target_id:
                return task_id
        dispatched = self.pipeline_scheduler.dispatch_window(
            {
                "proof": 1 if pipeline == "proof" else 0,
                "literature": 1 if pipeline == "literature" else 0,
                "verification": 1 if pipeline == "verification" else 0,
            }
        )
        for task in dispatched[pipeline]:
            if task.get("obligation_id") == self.target_id:
                return task["task_id"]
        return None

    def _graceful_stop_checkpoint(self, resume_phase: str) -> dict:
        self._checkpoint(
            CHECKPOINT_PHASE,
            status="STOPPED_AT_CHECKPOINT",
            checkpoint_reason="HUMAN_GRACEFUL_STOP",
            resume_phase=resume_phase,
        )
        if self.stop_controller is not None:
            self.stop_controller.acknowledge(
                run_id=self.run_dir.name,
                checkpoint=resume_phase,
            )
        return self.state

    def _append_hard_gate_contract(self) -> None:
        context_path = self.run_dir / "context" / "CONTEXT.md"
        contract = r"""

## Code-level pre-submit contract

The harness will reject `submit_proof` unless the candidate ends with a
machine-readable authority manifest of this exact form:

```text
<!-- OPENPROVER_AUTHORITY_MANIFEST
{
  "all_external_claims_classified": true,
  "branches_resolved": true,
  "unresolved": [],
  "authority_uses": [
    {
      "claim": "exact externally used claim",
      "claim_class": "FOUNDATIONAL_THEOREM | SEMANTIC_DEFINITION | PROJECT_THEOREM | LOCAL_PROOF | COMPUTATIONAL_CERTIFICATE",
      "authority_id": "exact registry or theorem ID; empty only for LOCAL_PROOF",
      "authority_type": "foundation | semantic | project_theorem | local_proof | computational_certificate",
      "proof_location": "candidate section for LOCAL_PROOF"
    }
  ],
  "source_paths": []
}
-->
```

Any unresolved hard blocker, missing/invalid authority, dependency cycle,
blocked project dependency, or replay source policy violation forbids
submission regardless of remaining time.
"""
        context_path.write_text(
            context_path.read_text(encoding="utf-8") + contract,
            encoding="utf-8",
        )

    def _write_repair_context(self) -> None:
        """Build a bounded successor package from the immutable parent run."""

        runs_root = (self.project.root / "runs").resolve()
        parent_dir = (runs_root / str(self.parent_run_id)).resolve()
        try:
            parent_dir.relative_to(runs_root)
        except ValueError as exc:
            raise ProjectError("Parent run escapes the project runs directory") from exc
        parent_state_path = parent_dir / "state.json"
        if not parent_state_path.exists():
            raise ProjectError(f"Missing parent run state: {self.parent_run_id}")
        parent_state = json.loads(parent_state_path.read_text(encoding="utf-8"))
        if parent_state.get("phase") != "COMPLETE":
            raise ProjectError("Repair successor requires an immutable COMPLETE parent run")
        candidate_path = parent_dir / "CANDIDATE_PROOF.md"
        failure_map_path = parent_dir / "FAILURE_MAP.json"
        if not candidate_path.exists() or not failure_map_path.exists():
            raise ProjectError("Repair successor requires the parent candidate and failure map")
        candidate = candidate_path.read_text(encoding="utf-8")
        failure_map = failure_map_path.read_text(encoding="utf-8")
        verified_lemmas_path = parent_dir / "verified_local_lemmas.json"
        verified_lemmas = (
            json.loads(verified_lemmas_path.read_text(encoding="utf-8"))
            if verified_lemmas_path.exists()
            else []
        )
        frozen_strategies = StrategyFingerprintStore(self.project).frozen_for_theorem(
            self.target_id
        )
        changed_dependencies = []
        repair_record = self.run_dir / "dependency_repair.json"
        if repair_record.exists():
            changed_dependencies = json.loads(repair_record.read_text(encoding="utf-8")).get(
                "materialized_authorities", []
            )
        repair_text = f"""# Bounded Repair Context

This is repair cycle `{self.repair_cycle}` for campaign `{self.campaign_id}`.
Do not reopen the whole theorem. Repair only the obligations in the failure map.

## Theorem statement

{self.target["statement"]}

## Previous candidate

{candidate}

## Failure map

```json
{failure_map.rstrip()}
```

## Changed dependencies

{json.dumps(changed_dependencies, ensure_ascii=False, indent=2)}

## Verified local lemmas

{json.dumps(verified_lemmas, ensure_ascii=False, indent=2)}

## Relevant FAILED_ROUTE memory

Use only the failed routes already present in the normal context package.

## Frozen strategy fingerprints

{json.dumps(frozen_strategies, ensure_ascii=False, indent=2)}

Do not retry a frozen strategy unless a new dependency, a new lemma, or a
changed failure condition is explicitly recorded.
"""
        (self.run_dir / "context" / "REPAIR_CONTEXT.md").write_text(repair_text, encoding="utf-8")

    def _run_audits_with_retry(self) -> tuple[dict[str, dict], AuditGate]:
        return self.audit_coordinator.run_with_retry()

    def _run_secondary_verification(self) -> dict:
        return self.audit_coordinator.run_secondary_verification()

    def _run_openprover_candidate(self) -> None:
        self.candidate_engine.run()

    def _run_audits(self) -> tuple[dict[str, dict], AuditGate]:
        return self.audit_coordinator.run_audits()

    def _finalize(self, gate: AuditGate) -> None:
        if gate.passed:
            self._refresh_canonical_authority()
            authority_ready, authority_blockers = authority_promotion_decision(
                self.state.get("canonical_authority", [])
            )
            if not authority_ready:
                status = authority_blockers[0]["type"]
                _write_json(
                    self.run_dir / "audits" / "canonical_authority_promotion_guard.json",
                    {
                        "passed": False,
                        "blockers": authority_blockers,
                        "mathematical_audit_gate": gate.to_dict(),
                        "checked_at": utc_now(),
                    },
                )
                self._checkpoint(
                    CHECKPOINT_PHASE,
                    status=status,
                    checkpoint_reason=status,
                    resume_phase="AUDITS_READY",
                    canonical_authority_blockers=authority_blockers,
                )
                return
            report_path = self._write_resolution_report(gate)
            theorem = self.project.load_theorem(self.target_id)
            theorem["proof_file"] = report_path.relative_to(self.project.root).as_posix()
            theorem["proof_type"] = (
                "MOCKED_DEMO" if is_mock_config(self.config) else "NATURAL_LANGUAGE"
            )
            self.project.update_theorem(theorem)
            self.target = self.project.transition(
                self.target_id,
                "PROVED",
                actor="Archivist",
                reason=f"All audit gates passed in {self.run_dir.name}",
                gate=gate,
                audit_status="PASS",
            )
            project_meta = self.project.load_project()
            branch = self.target.get("branch", "main")
            project_meta.setdefault("branches", {})[branch] = "CLOSED"
            self.project.save_project(project_meta)
            status = "PROVED"
        else:
            reasons = (
                list(gate.failure_reasons)
                + list(gate.execution_errors)
                + [f"Inconclusive auditor: {role}" for role in gate.inconclusive_audits]
            ) or ["One or more audit criteria failed"]
            (self.run_dir / "FAILURE_REPORT.md").write_text(
                "# Audit Failure\n\n" + "\n".join(f"- {reason}" for reason in reasons) + "\n",
                encoding="utf-8",
            )
            audit_results = {}
            audits_dir = self.run_dir / "audits"
            if audits_dir.exists():
                for path in sorted(audits_dir.glob("*.json")):
                    if path.name not in {
                        "gate.json",
                        "dependency_report.json",
                    } and not path.name.startswith("infrastructure_retry_"):
                        audit_results[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            failure_map = FailureMap.from_gate(
                run_id=self.run_dir.name,
                target_id=self.target_id,
                gate=gate,
                audits=audit_results,
                affected_branch=self.target.get("branch", "main"),
            )
            failure_json, failure_md = failure_map.write(self.run_dir)
            if self.campaign_id:
                fingerprint_store = StrategyFingerprintStore(self.project)
                fingerprint_records = []
                for item in failure_map.items:
                    strategy = StrategyFingerprint(
                        theorem=self.target_id,
                        branch=item.affected_branch,
                        target_lemma=item.exact_rejected_claim,
                        method=item.auditor,
                        key_dependency=item.authority_expected,
                        failure_point=item.exact_rejected_claim,
                    )
                    fingerprint_records.append(fingerprint_store.record_failure(strategy))
                _write_json(
                    self.run_dir / "strategy_fingerprints.json",
                    {
                        "schema_version": 1,
                        "records": fingerprint_records,
                    },
                )
            current = self.project.load_theorem(self.target_id)["status"]
            if gate.execution_errors or gate.inconclusive_audits:
                if current == "AUDITING":
                    self.target = self.project.transition(
                        self.target_id,
                        "PARTIAL",
                        actor="AuditCoordinator",
                        reason="; ".join(reasons),
                        audit_status="ERROR" if gate.execution_errors else "INCONCLUSIVE",
                    )
                error_text = " ".join(gate.execution_errors).casefold()
                if gate.execution_errors and any(
                    marker in error_text
                    for marker in (
                        "quota_exceeded",
                        "usage_limit_reached",
                        "quota exceeded",
                        "usage limit",
                    )
                ):
                    status = "BLOCKED_PROVIDER_QUOTA"
                elif gate.execution_errors:
                    status = "BLOCKED_INFRASTRUCTURE"
                else:
                    status = "HUMAN_REQUIRED"
                self.metrics["total"] = {
                    "wall_clock_seconds": round(time.perf_counter() - self.started, 3),
                    "success": False,
                }
                _write_json(self.run_dir / "usage.json", self.metrics)
                terminal_phase = CHECKPOINT_PHASE if self.campaign_id else "COMPLETE"
                checkpoint_updates = {
                    "status": status,
                    "checkpoint_reason": status,
                    "failure_reasons": reasons,
                    "failure_map": failure_json.name,
                }
                if terminal_phase == CHECKPOINT_PHASE:
                    checkpoint_updates["resume_phase"] = "CANDIDATE_READY"
                else:
                    checkpoint_updates["completed_at"] = utc_now()
                self._checkpoint(terminal_phase, **checkpoint_updates)
                return
            if current == "AUDITING":
                self.target = self.project.transition(
                    self.target_id,
                    "REJECTED",
                    actor="Final Proof Auditor",
                    reason="; ".join(reasons),
                    audit_status="FAIL",
                )
            status = "REJECTED"
        self.metrics["total"] = {
            "wall_clock_seconds": round(time.perf_counter() - self.started, 3),
            "success": gate.passed,
        }
        _write_json(self.run_dir / "usage.json", self.metrics)
        self._checkpoint(
            "COMPLETE",
            status=status,
            failure_reasons=gate.failure_reasons,
            failure_map=("FAILURE_MAP.json" if not gate.passed else None),
            completed_at=utc_now(),
        )

    def _write_resolution_report(self, gate: AuditGate) -> Path:
        context_data = json.loads(
            (self.run_dir / "context" / "context.json").read_text(encoding="utf-8")
        )
        candidate = (self.run_dir / "CANDIDATE_PROOF.md").read_text(encoding="utf-8")
        audits = {}
        for path in sorted((self.run_dir / "audits").glob("*.json")):
            if path.name not in {"gate.json", "dependency_report.json"}:
                audits[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = self.project.root / "reports" / f"{self.target_id}-resolution-{timestamp}.md"
        allowed = (
            "\n".join(
                f"- `{item['id']}`: {item['title']}"
                for item in context_data["allowed_dependencies"]
            )
            or "- (none)"
        )
        frozen = "\n".join(f"- {item}" for item in context_data["frozen_branches"]) or "- (none)"
        audit_lines = []
        for name, audit in audits.items():
            audit_lines.append(
                f"- **{name}**: {audit.get('domain_verdict', audit.get('verdict', 'INCONCLUSIVE'))} "
                f"/ {audit.get('execution_status', 'OK')} — "
                f"{audit.get('summary') or '; '.join(audit.get('findings', [])) or 'No summary'}"
            )
        report = f"""# {self.target["title"]} — Resolution

## Scope

Target `{self.target_id}` only; generated by run `{self.run_dir.name}`.

## Allowed dependencies

{allowed}

## Frozen branches

{frozen}

## Statement

{self.target["statement"]}

## Definitions

See the candidate proof below; no definition outside the dependency slice is implicitly authorized.

## Lemmas

Only the PROVED dependencies listed above may be invoked.

## Proof

{candidate}

## Converse / reconstruction

Audited according to claim type `{self.target.get("claim_type", "implication")}`.

## Boundary cases

Checked by the Boundary Auditor; see audit results.

## Computational evidence

Any experiments are evidence only. The gate confirms they were kept separate from the proof.

## Audit results

{chr(10).join(audit_lines)}

Gate: `PASS`

## Remaining obstruction

None within the stated scope.

## Final classification

PROVED

## Dependencies added

None automatically.

## Status

PROVED — archived only after Final Proof Auditor PASS.
"""
        report_path.write_text(report, encoding="utf-8")
        return report_path
