"""Outer orchestration and audit gate around OpenProver's core Prover."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from openprover.budget import Budget
from openprover.prover import Prover
from openprover.tui import HeadlessTUI

from .audit_prompts import AUDITOR_ROLES, auditor_prompt, final_auditor_prompt
from .audit_protocol import AuditResult, normalize_audit_result
from .campaign import (
    FailureMap,
    PreSubmitGate,
    ReplayPolicy,
    classify_provider_exception,
)
from .codex_cli_provider import (
    BILLING_MODE,
    CodexCLIProviderError,
    resolve_codex_executable,
)
from .openai_provider import OpenAIProviderError
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
from .scheduler import (
    RoleScheduler,
    StopController,
    StrategyFingerprint,
    StrategyFingerprintStore,
)
from .state_machine import AuditGate
from .trust_kernel import DependencyAuthorityResolver, TrustKernel


PHASES = ("CREATED", "CONTEXT_READY", "CANDIDATE_READY", "AUDITS_READY", "COMPLETE")
CHECKPOINT_PHASE = "CHECKPOINT"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ProjectError("Auditor did not return a JSON object")
    try:
        value = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid auditor JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectError("Auditor JSON must be an object")
    return value


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
    if getattr(client, "billing_mode", None) == BILLING_MODE:
        return 0
    return int(getattr(client, "request_count", 0))


def _codex_process_count(client: object | None) -> int:
    if client is None:
        return 0
    if hasattr(client, "codex_process_count"):
        return int(getattr(client, "codex_process_count"))
    if getattr(client, "billing_mode", None) != BILLING_MODE:
        return 0
    return int(getattr(client, "request_count", 0))


class SubmissionGuardedProver(Prover):
    """Apply the harness hard gate before the core writes ``PROOF.md``."""

    def __init__(self, *args, pre_submit_gate: PreSubmitGate | None = None,
                 pre_submit_gate_path: Path | None = None,
                 role_scheduler: RoleScheduler | None = None,
                 stop_controller: StopController | None = None, **kwargs):
        self._harness_pre_submit_gate = pre_submit_gate
        self._harness_pre_submit_gate_path = pre_submit_gate_path
        self._harness_role_scheduler = role_scheduler
        self._harness_stop_controller = stop_controller
        self._harness_pipeline_scheduler = kwargs.pop("pipeline_scheduler", None)
        self._harness_model_router = kwargs.pop("model_router", None)
        self._harness_root_obligation_id = kwargs.pop("root_obligation_id", None)
        super().__init__(*args, **kwargs)

    def _handle_submit_proof(self, plan: dict, step_dir: Path) -> str:
        proof_slug = plan.get("proof_slug", "")
        content = self.repo.read_item(proof_slug) if proof_slug else None
        if not content:
            return super()._handle_submit_proof(plan, step_dir)
        if self._harness_pre_submit_gate is None:
            return super()._handle_submit_proof(plan, step_dir)
        decision = self._harness_pre_submit_gate.evaluate(content)
        if self._harness_pre_submit_gate_path is not None:
            _write_json(self._harness_pre_submit_gate_path, decision.to_dict())
        if not decision.allowed:
            blocker_text = "; ".join(
                f"{item['type']}: {item['detail']}"
                for item in decision.blockers
            )
            self.tui.log(
                f"submit_proof blocked by research harness: {blocker_text}",
                color="red",
            )
            self._push_output(
                "submit_proof forbidden by the code-level pre-submit gate. "
                + blocker_text
            )
            return "continue"
        return super()._handle_submit_proof(plan, step_dir)

    def _handle_spawn(self, plan: dict, step_dir: Path,
                      planner_resp: dict | None = None) -> str:
        if (
            self._harness_stop_controller is not None
            and self._harness_stop_controller.requested()
        ):
            _write_json(self.work_dir / "graceful_stop.json", {
                "status": "STOPPED_BEFORE_NEW_WORKER",
                "step": self.step_num,
                "created_at": utc_now(),
            })
            self._push_output(
                "Graceful stop requested: no new Worker was started; checkpoint now."
            )
            return "stop"
        if self._harness_role_scheduler is not None:
            assignments = self._harness_role_scheduler.assign_tasks(
                list(plan.get("tasks", []))
            )
            plan = dict(plan)
            plan["tasks"] = [
                {
                    **dict(plan.get("tasks", [])[item.index]),
                    "summary": f"[{item.role}] {item.summary}",
                    "description": item.description,
                    "worker_role": item.role,
                }
                for item in assignments
            ]
            _write_json(
                step_dir / "worker_assignments.json",
                {
                    "schema_version": 1,
                    "capacity": len(assignments),
                    "assignments": [item.to_dict() for item in assignments],
                },
            )
        result = super()._handle_spawn(plan, step_dir, planner_resp)
        self._record_worker_events(plan, step_dir)
        return result

    def _record_worker_events(self, plan: dict, step_dir: Path) -> None:
        """Bridge Worker requests/verdicts into durable routing and DAG state."""

        tasks = list(plan.get("tasks", []))
        workers_dir = step_dir / "workers"
        for index, task in enumerate(tasks):
            obligation_id = str(
                task.get("obligation_id") or task.get("obligation") or self.work_dir.name
            )
            result_path = workers_dir / f"result_{index}.md"
            verifier_path = workers_dir / f"verifier_result_{index}.md"
            worker_text = (
                result_path.read_text(encoding="utf-8") if result_path.exists() else ""
            )
            verifier_text = (
                verifier_path.read_text(encoding="utf-8") if verifier_path.exists() else ""
            )
            request = self._extract_literature_request(worker_text)
            if request and self._harness_pipeline_scheduler is not None:
                request.setdefault("obligation_id", obligation_id)
                try:
                    self._harness_pipeline_scheduler.add_literature_request(request)
                except ProjectError:
                    # A malformed/unknown request remains visible in Worker output;
                    # it never becomes silent authority or freezes sibling work.
                    pass
            if self._harness_model_router is None:
                continue
            worker_verdict = self._worker_verdict(worker_text)
            verifier_verdict = self._verifier_verdict(verifier_text)
            if (
                worker_verdict == "CORRECT"
                and verifier_verdict in {"FLAWED", "CRITICALLY_FLAWED", "UNCERTAIN"}
            ):
                self._harness_model_router.record_verifier_disagreement(
                    obligation_id,
                    worker_verdict=worker_verdict,
                    verifier_verdict=verifier_verdict,
                )
            failure_markers = {
                "NO_PROGRESS": "NO_PROGRESS",
                "REPEATED_FAILED_ROUTE": "REPEATED_FAILED_ROUTE",
                "MALFORMED_RESULT": "MALFORMED_RESULT",
                "AUTHORITY_FAILURE": "AUTHORITY_FAILURE",
                "MATHEMATICAL_OBSTRUCTION": "MATHEMATICAL_OBSTRUCTION",
            }
            upper = (worker_text + "\n" + verifier_text).upper()
            for marker, failure_kind in failure_markers.items():
                if marker in upper:
                    self._harness_model_router.record_failure(
                        obligation_id, failure_kind, detail=marker
                    )
                    break
            high_value = any(marker in upper for marker in (
                "ENTIRE BRANCH CLOSURE", "EXACT CLASSIFICATION", "MASTER LEMMA",
                "INFINITE FAMILY EXCLUSION", "MULTI-BRANCH COLLAPSE",
                "NEW GLOBAL INVARIANT",
            ))
            if high_value:
                branch = str(task.get("branch_id") or task.get("branch") or "main")
                main_impact = branch in {"main", "global"} or any(
                    marker in upper for marker in (
                        "EXACT CLASSIFICATION", "MULTI-BRANCH COLLAPSE",
                        "NEW GLOBAL INVARIANT",
                    )
                )
                self._harness_model_router.promote_high_value(
                    obligation_id, theorem_level=main_impact
                )
        if self._harness_model_router is not None and tasks:
            combined = "\n".join(
                (workers_dir / f"result_{index}.md").read_text(encoding="utf-8")
                for index in range(len(tasks))
                if (workers_dir / f"result_{index}.md").exists()
            ).upper()
            self._harness_model_router.record_frontier_cycle(
                self._harness_root_obligation_id or self.work_dir.name,
                progress={
                    "branch_closure": "BRANCH CLOSURE" in combined,
                    "parameter_reduction": "PARAMETER REDUCTION" in combined,
                    "stronger_invariant": "STRONGER INVARIANT" in combined
                        or "NEW GLOBAL INVARIANT" in combined,
                    "verified_lemma": "VERIFIED LEMMA" in combined,
                    "dependency_simplification": "DEPENDENCY SIMPLIFICATION" in combined,
                },
            )

    @staticmethod
    def _extract_literature_request(text: str) -> dict | None:
        marker = text.find("LITERATURE_REQUEST")
        if marker < 0:
            return None
        tail = text[marker + len("LITERATURE_REQUEST"):]
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", tail, re.DOTALL)
        candidate = fenced.group(1) if fenced else None
        if candidate is None:
            start = tail.find("{")
            if start >= 0:
                depth = 0
                in_string = False
                escaped = False
                for offset, character in enumerate(tail[start:], start=start):
                    if in_string:
                        if escaped:
                            escaped = False
                        elif character == "\\":
                            escaped = True
                        elif character == '"':
                            in_string = False
                        continue
                    if character == '"':
                        in_string = True
                    elif character == "{":
                        depth += 1
                    elif character == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = tail[start:offset + 1]
                            break
        if not candidate:
            return None
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _worker_verdict(text: str) -> str:
        upper = text.upper()
        if "CRITICALLY_FLAWED" in upper:
            return "CRITICALLY_FLAWED"
        if "FLAWED" in upper:
            return "FLAWED"
        if "CORRECT" in upper or "COMPLETE PROOF" in upper:
            return "CORRECT"
        return "UNCERTAIN"

    @staticmethod
    def _verifier_verdict(text: str) -> str:
        match = re.search(
            r"(?:VERDICT\s*:\s*)?(CRITICALLY_FLAWED|FLAWED|CORRECT|UNCERTAIN)",
            text,
            re.IGNORECASE,
        )
        return match.group(1).upper() if match else "UNCERTAIN"


def build_run_preview(project: ProjectStore, target_id: str, *,
                      config_path: str | Path, worker_count: int = 3,
                      expand_context: bool = False) -> dict:
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
        "final_auditor",
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
                "provider", "model", "reasoning_effort", "timeout_seconds",
                "max_retries", "max_output_tokens", "answer_reserve",
                "sandbox", "executable",
            )
            if key in role
        }
        assignments[role_name].update({
            "default_tier": router.default_tier(role_name),
            "resolved_tier": route.tier,
            "fallback": route.fallback,
            "fallback_reason": route.fallback_reason,
        })
        if provider == "openai":
            credentials[role_name] = {
                "environment_variable": "OPENAI_API_KEY",
                "present": bool(os.environ.get("OPENAI_API_KEY")),
            }
        elif provider == "codex_cli":
            executable = resolve_codex_executable(role.get("executable"))
            assignments[role_name].update({
                "requested_model": role.get("model"),
                "model_source": "configured" if role.get("model") else "codex_cli_default",
                "requested_reasoning_effort": role.get("reasoning_effort"),
                "resolved_executable": executable,
                "working_directory": str(
                    project.root / "runs" / "<run-id>" / "codex" / role_name
                ),
                "prompt_transport": "stdin",
                "output_mode": "jsonl+output-last-message",
            })
            credentials[role_name] = {
                "source": "codex_cli_login",
                "status_check_performed": False,
                "openai_api_key_forwarded": False,
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
            "allowed_dependencies": [
                item["id"] for item in package.data["allowed_dependencies"]
            ],
            "blocked_dependencies": [
                item["id"] for item in package.data["blocked_dependencies"]
            ],
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

    def __init__(self, project: ProjectStore, target_id: str, *,
                 config_path: str | Path, worker_count: int = 3,
                 dry_run: bool = False, resume: str | Path | None = None,
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
                 pipeline_handlers: dict | None = None):
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
                raise ProjectError("Resume directory must be inside the project's runs directory") from exc
            if not candidate.is_dir():
                raise ProjectError(f"Resume directory not found: {candidate}")
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
            # Completed run evidence is byte-immutable.  Migration occurs only
            # when creating/resuming an active successor, never in-place here.
            if state.get("phase") == "COMPLETE":
                return state
            version = int(state.get("schema_version", 1))
            if version > 2:
                raise ProjectError(f"Unsupported run state schema: {version}")
            state["schema_version"] = 2
            state.setdefault("routing_state_file", "routing_state.json")
            state.setdefault("pipeline_state_file", "pipeline_state.json")
            state.setdefault("checkpoint_migrations", [])
            if version < 2:
                state["checkpoint_migrations"].append({
                    "from": version,
                    "to": 2,
                    "migration": "add heterogeneous routing and async pipeline state",
                    "at": utc_now(),
                })
                _write_json(self.state_path, state)
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
            "replay_policy_hash": (
                self.replay_policy.policy_hash if self.replay_policy else None
            ),
            "budget_limit_seconds": self.budget_limit_seconds,
            "initial_worker_count": self.initial_worker_count,
            "role_scheduling": self.role_scheduling,
            "secondary_verification": self.secondary_verification,
            "routing_state_file": "routing_state.json",
            "pipeline_state_file": "pipeline_state.json",
            "checkpoint_migrations": [],
        }
        _write_json(self.state_path, state)
        return state

    def _ensure_target_pipeline_obligation(self) -> None:
        snapshot = self.pipeline_scheduler.snapshot()
        if self.target_id in snapshot["obligations"]:
            return
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

            scholarly_adapter = ScholarlySearchAdapter([
                OpenAlexProvider(
                    cache_dir=self.run_dir / "literature" / "scholarly-cache"
                )
            ])
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
            working_dir=self.run_dir / "codex" / "literature",
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
        self, task: dict, context, *, role: str,
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
            working_dir=self.run_dir / "codex" / "pipeline" / task["task_id"],
        )
        context.set_handle(client)
        prompt = (
            f"[Worker role: {role}]\n[Obligation ID: {task['obligation_id']}]\n"
            + json.dumps(prompt_payload or {
                "target_statement": obligation.get("target_statement"),
                "task": task,
            }, ensure_ascii=False, indent=2)
        )
        try:
            response = client.call(
                prompt,
                system_prompt or f"You are the bounded production {role}. Return one JSON object.",
                label=f"{role}_{task['task_id']}",
                archive_path=self.run_dir / "archive" / "pipeline" / f"{task['task_id']}.md",
            )
            if context.cancel_event.is_set():
                raise RuntimeError("task-scoped cancellation requested")
            try:
                value = _extract_json(response.get("result", ""))
            except ProjectError:
                value = {"success": False, "verdict": "UNCERTAIN", "raw": response.get("result", "")}
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
                key: authority.get(key) for key in (
                    "authority_id", "theorem_number", "page_or_section",
                    "exact_statement", "hypotheses", "notation_map", "statement_hash",
                )
            },
            "required_output": {
                "external_hypotheses": ["string"],
                "notation_map": {"paper_symbol": "project_symbol"},
                "hypothesis_mapping": [{
                    "external_hypothesis": "string", "satisfied_by": "string",
                    "status": "PROVED|NOT_APPLICABLE|UNRESOLVED|FAILED", "evidence": ["string"],
                }],
                "conclusion_mapping": {
                    "external_conclusion": "string", "target": "string",
                    "bridge_steps": ["string"], "status": "PROVED|UNRESOLVED|FAILED",
                },
                "exception_analysis": {"excluded_cases": ["string"], "analysis": "string", "status": "PROVED|NOT_APPLICABLE|UNRESOLVED|FAILED"},
                "direction_analysis": {"direction": "external conclusion => target", "analysis": "string", "status": "PROVED|UNRESOLVED|FAILED"},
                "normalization_analysis": {"analysis": "string", "status": "PROVED|UNRESOLVED|FAILED"},
                "required_local_lemmas": ["authorized lemma id only"],
                "unresolved_conditions": ["string"],
            },
        }
        value = self._pipeline_llm_handler(
            task, context, role="reconstruction", prompt_payload=payload,
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
            "external_hypotheses": value.get("external_hypotheses") or authority.get("hypotheses") or [],
            "notation_map": value.get("notation_map") if isinstance(value.get("notation_map"), dict) else {},
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
                "APPLICABLE", "NOT_APPLICABLE", "UNCERTAIN",
                "INCOMPLETE_RECONSTRUCTION", "WRONG_DIRECTION",
                "HYPOTHESIS_MISMATCH", "EXCEPTION_MISMATCH", "UNAUTHORIZED_DEPENDENCY",
            ],
        }
        value = self._pipeline_llm_handler(
            task, context, role="theorem_verifier", prompt_payload=payload,
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
            "applicability_verification_errors": promoted.get("applicability_verification_errors", []),
            "deterministic_applicability_promotion": promoted["status"] == "APPLICABLE_EXTERNAL_AUTHORITY",
            "result_artifact": str(registry.root / promoted["verifier_artifact_path"]),
            "routing": routing,
        }

    def _start_async_pipeline_monitor(self, *, include_proof: bool = False) -> None:
        if self.pipeline_runtime is None or self._pipeline_monitor_thread is not None:
            return
        self._pipeline_monitor_stop.clear()
        self.pipeline_runtime.start_window({
            "proof": self.worker_count if include_proof else 0,
            "literature": self.worker_count,
            "verification": self.worker_count,
        })

        def monitor() -> None:
            while not self._pipeline_monitor_stop.is_set():
                self.pipeline_runtime.start_window({
                    "proof": self.worker_count if include_proof else 0,
                    "literature": self.worker_count,
                    "verification": self.worker_count,
                })
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
            self.pipeline_runtime.start_window({
                "proof": 0,
                "literature": self.worker_count,
                "verification": self.worker_count,
            })
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
            return self._graceful_stop_checkpoint(
                self.state.get("phase", "CREATED")
            )
        if self.target["status"] == "UNCLASSIFIED":
            raise ProjectError("UNCLASSIFIED imports require human classification before research")
        if self.target["status"] == "FROZEN":
            raise ProjectError("Target is FROZEN; explicitly unfreeze it before research")
        if self.target["status"] == "PROVED":
            reaudit = self.state.get("reaudit") or self.project.consume_reaudit_request()
            if not reaudit:
                raise ProjectError("Target is already PROVED; request re-audit explicitly instead of rerunning research")
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
                self.target_id, expand=self.expand_context,
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

        if not self._phase_at_least("CANDIDATE_READY"):
            try:
                self._run_openprover_candidate()
            except (OpenAIProviderError, CodexCLIProviderError) as exc:
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
                        if gate_path.exists() else {}
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
            gate_data = json.loads((self.run_dir / "audits" / "gate.json").read_text(encoding="utf-8"))
            gate = AuditGate(**{
                key: value for key, value in gate_data.items()
                if key in AuditGate.__dataclass_fields__
            })
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
                _write_json(
                    self.run_dir / "audits" / "gate.json", gate.to_dict()
                )
            _write_json(self.state_path, self.state)

        verification_task_id = self.state.get("active_verification_task_id")
        if verification_task_id:
            task = self.pipeline_scheduler.snapshot()["tasks"].get(
                verification_task_id, {}
            )
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
        dispatched = self.pipeline_scheduler.dispatch_window({
            "proof": 1 if pipeline == "proof" else 0,
            "literature": 1 if pipeline == "literature" else 0,
            "verification": 1 if pipeline == "verification" else 0,
        })
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
        contract = r'''

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
'''
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
            if verified_lemmas_path.exists() else []
        )
        frozen_strategies = StrategyFingerprintStore(
            self.project
        ).frozen_for_theorem(self.target_id)
        changed_dependencies = []
        repair_record = self.run_dir / "dependency_repair.json"
        if repair_record.exists():
            changed_dependencies = json.loads(
                repair_record.read_text(encoding="utf-8")
            ).get("materialized_authorities", [])
        repair_text = f"""# Bounded Repair Context

This is repair cycle `{self.repair_cycle}` for campaign `{self.campaign_id}`.
Do not reopen the whole theorem. Repair only the obligations in the failure map.

## Theorem statement

{self.target['statement']}

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
        (self.run_dir / "context" / "REPAIR_CONTEXT.md").write_text(
            repair_text, encoding="utf-8"
        )

    def _run_audits_with_retry(self) -> tuple[dict[str, dict], AuditGate]:
        attempts = 0
        while True:
            audits, gate = self._run_audits()
            if not gate.execution_errors or attempts >= self.infrastructure_retries:
                self.state["infrastructure_retry_count"] = attempts
                return audits, gate
            attempts += 1
            _write_json(
                self.run_dir / "audits" / f"infrastructure_retry_{attempts}.json",
                {
                    "attempt": attempts,
                    "errors": gate.execution_errors,
                    "created_at": utc_now(),
                },
            )

    def _run_secondary_verification(self) -> dict:
        """Run five bounded checks after the primary gate first passes."""

        checks = {
            "independent_reconstruction": (
                "Reconstruct the proof independently from the theorem statement and authorized sources. "
                "Do not trust the primary audit summaries."
            ),
            "adversarial_review": (
                "Attack the candidate for a concrete mathematical gap, omitted branch, or counterexample."
            ),
            "certificate_rerun": (
                "Recheck every cited computational certificate and its finite-reduction claim. "
                "If none is used, verify that none is silently required."
            ),
            "dependency_coverage": (
                "Reconstruct every external claim and confirm exact Foundation, Semantic, Project, "
                "Local Proof, or Computational Certificate coverage."
            ),
            "statement_scope_reconstruction": (
                "Reconstruct the theorem statement, notation scope, parameter ranges, converse, and branches "
                "from source, then compare them with the candidate."
            ),
        }
        secondary_dir = self.run_dir / "secondary_verification"
        secondary_dir.mkdir(parents=True, exist_ok=True)
        context = (self.run_dir / "context" / "CONTEXT.md").read_text(
            encoding="utf-8"
        )
        candidate = (self.run_dir / "CANDIDATE_PROOF.md").read_text(
            encoding="utf-8"
        )
        clients: dict[str, object] = {}

        def execute(name: str, directive: str) -> tuple[str, dict, object]:
            system = (
                "You are an independent secondary verifier. Return one JSON object with "
                "domain_verdict PASS/FAIL/INCONCLUSIVE, execution_status OK/ERROR, "
                "failure_reasons, findings, and cross_audit_notes. Mathematical doubts are "
                "not infrastructure errors."
            )
            prompt = f"""# Secondary check: {name}

{directive}

# Authorized context

{context}

# Candidate

{candidate}
"""
            client = None
            try:
                client = RoutedLLMClient(
                    self.model_router,
                    client_factory=create_client,
                    default_role="secondary_verifier",
                    archive_dir=self.run_dir / "archive" / "secondary" / name,
                    working_dir=self.run_dir / "codex" / "secondary" / name,
                )
                response = client.call(
                    prompt=prompt,
                    system_prompt=system,
                    label=f"secondary_{name}",
                    archive_path=secondary_dir / f"{name}_call.md",
                )
                result = normalize_audit_result(
                    name, _extract_json(response.get("result", ""))
                ).to_dict()
            except Exception as exc:
                result = AuditResult.from_exception(name, exc).to_dict()
            return name, result, client

        results = {}
        with ThreadPoolExecutor(max_workers=len(checks)) as pool:
            futures = [
                pool.submit(execute, name, directive)
                for name, directive in checks.items()
            ]
            for future in as_completed(futures):
                name, result, client = future.result()
                results[name] = result
                if client is not None:
                    clients[name] = client
                _write_json(secondary_dir / f"{name}.json", result)

        deterministic_failures = []
        dependency_path = self.run_dir / "audits" / "dependency_report.json"
        if not dependency_path.exists():
            deterministic_failures.append("secondary dependency coverage: dependency report is missing")
        else:
            dependency_report = json.loads(
                dependency_path.read_text(encoding="utf-8")
            )
            if not dependency_report.get("admissible", False):
                deterministic_failures.append(
                    "secondary dependency coverage: deterministic authority report is not admissible"
                )
            certificate_ids = dependency_report.get(
                "computational_certificates", []
            )
            for certificate_id in certificate_ids:
                candidates = [
                    self.project.root / "certificates" / f"{certificate_id}{suffix}"
                    for suffix in (".json", ".md", ".txt")
                ]
                if not any(path.is_file() for path in candidates):
                    deterministic_failures.append(
                        f"secondary certificate rerun: missing certificate {certificate_id}"
                    )
        if self.hard_submit_gate:
            pre_submit_path = self.run_dir / "pre_submit_gate.json"
            if not pre_submit_path.exists() or not json.loads(
                pre_submit_path.read_text(encoding="utf-8")
            ).get("allowed", False):
                deterministic_failures.append(
                    "secondary scope reconstruction: pre-submit hard gate was not PASS"
                )

        failure_reasons = list(deterministic_failures)
        execution_errors = []
        inconclusive = []
        for name, data in results.items():
            result = normalize_audit_result(name, data)
            if result.execution_status == "ERROR":
                execution_errors.append(
                    f"secondary {name}: {result.execution_error or 'execution failed'}"
                )
            elif result.domain_verdict == "INCONCLUSIVE":
                inconclusive.append(name)
            elif result.domain_verdict == "FAIL":
                failure_reasons.extend(
                    result.failure_reasons or [f"secondary {name} returned FAIL"]
                )
        for client in clients.values():
            client.cleanup()
        result = {
            "schema_version": 1,
            "passed": not failure_reasons and not execution_errors and not inconclusive,
            "checks": results,
            "failure_reasons": failure_reasons,
            "execution_errors": execution_errors,
            "inconclusive_checks": inconclusive,
            "completed_at": utc_now(),
        }
        _write_json(secondary_dir / "gate.json", result)
        self.metrics["secondary_verification"] = {
            "calls": sum(getattr(client, "call_count", 0) for client in clients.values()),
            "success": result["passed"],
            "api_request_count": sum(_api_request_count(client) for client in clients.values()),
            "codex_process_count": sum(_codex_process_count(client) for client in clients.values()),
            "usage": _sum_usage(list(clients.values())),
        }
        return result

    def _run_openprover_candidate(self) -> None:
        context_path = self.run_dir / "context" / "CONTEXT.md"
        context_text = context_path.read_text(encoding="utf-8")
        repair_context = self.run_dir / "context" / "REPAIR_CONTEXT.md"
        if repair_context.exists():
            context_text += "\n\n" + repair_context.read_text(encoding="utf-8")
        op_dir = self.run_dir / "openprover"
        op_dir.mkdir(parents=True, exist_ok=True)
        archive = self.run_dir / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        holders: dict[str, object] = {}

        def make_planner(_archive_dir):
            client = RoutedLLMClient(
                self.model_router,
                client_factory=create_client,
                default_role="planner",
                archive_dir=archive / "planner",
                working_dir=self.run_dir / "codex" / "planner",
            )
            holders["planner"] = client
            return client

        def make_worker(_archive_dir):
            client = RoutedLLMClient(
                self.model_router,
                client_factory=create_client,
                default_role="worker",
                archive_dir=archive / "worker",
                working_dir=self.run_dir / "codex" / "worker",
            )
            holders["worker"] = client
            return client

        planner_route = self.model_router.resolve(
            "planner", obligation_id=self.target_id, reserve=False
        )
        worker_route = self.model_router.resolve(
            "worker", obligation_id=self.target_id, reserve=False
        )

        budget_cfg = self.config.get("budget", {})
        budget_mode = budget_cfg.get("mode", "time")
        budget_limit = (
            self.budget_limit_seconds
            if self.budget_limit_seconds is not None
            else int(budget_cfg.get("limit", 900 if not self.dry_run else 120))
        )
        budget = Budget(
            mode=budget_mode,
            limit=budget_limit,
            conclude_after=float(budget_cfg.get("conclude_after", 0.99)),
        )
        resumed = (op_dir / "WHITEBOARD.md").exists() and not (op_dir / "PROOF.md").exists()
        started = time.perf_counter()
        use_harness_prover = bool(
            self.hard_submit_gate
            or self.role_scheduling
            or self.stop_controller
            or self.model_router
            or self.pipeline_scheduler
        )
        prover_type = SubmissionGuardedProver if use_harness_prover else Prover
        prover_kwargs = dict(
            work_dir=op_dir,
            theorem_text=context_text,
            mode="prove",
            make_llm=make_planner,
            make_worker_llm=make_worker,
            model_name=(
                f"{planner_route.model or 'provider-default'}/"
                f"{worker_route.model or 'provider-default'}"
            ),
            budget=budget,
            autonomous=True,
            verbose=False,
            tui=HeadlessTUI(),
            isolation=bool(self.config.get("isolation", True)),
            max_workers=self.worker_count,
            resumed=resumed,
            verifier=True,
            history_budget=int(self.config.get("history_budget", 0)),
            on_budget_out="exit",
            on_rate_limited="exit",
        )
        harness_kwargs = {}
        if self.hard_submit_gate:
            context_data = json.loads(
                (self.run_dir / "context" / "context.json").read_text(
                    encoding="utf-8"
                )
            )
            trust_kernel = TrustKernel.for_project(self.project)
            resolver = DependencyAuthorityResolver(
                foundations=trust_kernel.foundations,
                semantics=trust_kernel.semantics,
                project=self.project,
                notation_scope=context_data.get("notation_scope", ""),
            )
            harness_kwargs.update({
                "pre_submit_gate": PreSubmitGate(
                    resolver=resolver,
                    blocked_dependencies=self.state.get("blocked_dependencies", []),
                    dependency_cycles=self.state.get("dependency_cycles", []),
                    replay_policy=self.replay_policy,
                    require_manifest=True,
                ),
                "pre_submit_gate_path": self.run_dir / "pre_submit_gate.json",
            })
        if self.role_scheduling:
            harness_kwargs["role_scheduler"] = RoleScheduler(
                initial_workers=self.initial_worker_count,
                max_workers=self.worker_count,
            )
        if self.stop_controller is not None:
            harness_kwargs["stop_controller"] = self.stop_controller
        harness_kwargs["pipeline_scheduler"] = self.pipeline_scheduler
        harness_kwargs["model_router"] = self.model_router
        harness_kwargs["root_obligation_id"] = self.target_id
        prover_kwargs.update(harness_kwargs)
        active_proof_task = self._claim_target_pipeline_task("proof")
        # Literature and verification tasks belong to the same run lifetime as
        # the long-running OpenProver call.  They are monitored in parallel;
        # only the target proof task itself remains owned by OpenProver.
        self._start_async_pipeline_monitor(include_proof=False)
        prover = prover_type(**prover_kwargs)
        try:
            prover.run()
        finally:
            for client in holders.values():
                client.cleanup()
            self._stop_async_pipeline_monitor()
        proof_path = op_dir / "PROOF.md"
        if proof_path.exists():
            shutil.copy2(proof_path, self.run_dir / "CANDIDATE_PROOF.md")
        if active_proof_task:
            self.pipeline_scheduler.complete_task(
                active_proof_task,
                {
                    "success": proof_path.exists(),
                    "proof_candidate": proof_path.exists(),
                    "high_value": proof_path.exists(),
                },
            )
        self._drain_async_pipeline_tasks()
        self.metrics["planner"] = {
            "calls": getattr(holders.get("planner"), "call_count", 0),
            "cost_usd": getattr(holders.get("planner"), "total_cost", 0.0),
            "wall_clock_seconds": round(time.perf_counter() - started, 3),
            "success": proof_path.exists(),
            "retry_count": len(list(op_dir.glob("steps/*/planner_call_retry_*.md"))),
            "provider_retry_count": getattr(holders.get("planner"), "total_retries", 0),
            "api_request_count": _api_request_count(holders.get("planner")),
            "codex_process_count": _codex_process_count(holders.get("planner")),
            "billing_mode": getattr(holders.get("planner"), "billing_mode", None),
            "usage": _usage_metrics(holders.get("planner")),
        }
        workers = len(list(op_dir.glob("steps/*/workers/worker_*_call.md")))
        verifiers = len(list(op_dir.glob("steps/*/workers/verifier_*_call.md")))
        self.metrics["worker_and_upstream_verifier"] = {
            "calls": getattr(holders.get("worker"), "call_count", 0),
            "worker_calls": workers,
            "verifier_calls": verifiers,
            "output_tokens": budget.total_output_tokens,
            "cost_usd": getattr(holders.get("worker"), "total_cost", 0.0),
            "success": proof_path.exists(),
            "retry_count": 0,
            "provider_retry_count": getattr(holders.get("worker"), "total_retries", 0),
            "api_request_count": _api_request_count(holders.get("worker")),
            "codex_process_count": _codex_process_count(holders.get("worker")),
            "billing_mode": getattr(holders.get("worker"), "billing_mode", None),
            "usage": _usage_metrics(holders.get("worker")),
        }
        self.metrics["routing"] = self.model_router.snapshot()
        self.metrics["pipelines"] = self.pipeline_scheduler.snapshot()
        _write_json(self.run_dir / "usage.json", self.metrics)

    def _run_audits(self) -> tuple[dict[str, dict], AuditGate]:
        context = (self.run_dir / "context" / "CONTEXT.md").read_text(encoding="utf-8")
        candidate = (self.run_dir / "CANDIDATE_PROOF.md").read_text(encoding="utf-8")
        audits_dir = self.run_dir / "audits"
        audits_dir.mkdir(parents=True, exist_ok=True)
        clients = {}
        started = time.perf_counter()

        def execute(role: str) -> tuple[str, dict, object]:
            client = RoutedLLMClient(
                self.model_router,
                client_factory=create_client,
                default_role=role,
                archive_dir=self.run_dir / "archive" / role,
                working_dir=self.run_dir / "codex" / role,
            )
            system, prompt = auditor_prompt(role, context, candidate)
            try:
                response = client.call(
                    prompt=prompt,
                    system_prompt=system,
                    label=f"audit_{role}",
                    archive_path=audits_dir / f"{role}_call.md",
                )
                data = normalize_audit_result(
                    role, _extract_json(response.get("result", ""))
                ).to_dict()
            except Exception as exc:
                data = AuditResult.from_exception(role, exc).to_dict()
            return role, data, client

        audits: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(AUDITOR_ROLES)) as pool:
            futures = [pool.submit(execute, role) for role in AUDITOR_ROLES]
            for future in as_completed(futures):
                role, data, client = future.result()
                audits[role] = data
                clients[role] = client
                _write_json(audits_dir / f"{role}.json", data)

        context_data = json.loads(
            (self.run_dir / "context" / "context.json").read_text(encoding="utf-8")
        )
        dependency_audit = normalize_audit_result(
            "dependency_auditor", audits["dependency_auditor"]
        )
        trust_kernel = TrustKernel.for_project(self.project)
        resolver = DependencyAuthorityResolver(
            foundations=trust_kernel.foundations,
            semantics=trust_kernel.semantics,
            project=self.project,
            notation_scope=context_data.get("notation_scope", ""),
        )
        dependency_report = resolver.resolve(dependency_audit.authority_uses)
        if (
            dependency_audit.execution_status == "OK"
            and not dependency_report.admissible
        ):
            dependency_audit.domain_verdict = "FAIL"
            dependency_audit.failure_reasons.extend(dependency_report.errors)
        audits["dependency_auditor"] = dependency_audit.to_dict()
        _write_json(
            audits_dir / "dependency_auditor.json",
            audits["dependency_auditor"],
        )
        _write_json(audits_dir / "dependency_report.json", dependency_report.to_dict())

        final_client = RoutedLLMClient(
            self.model_router,
            client_factory=create_client,
            default_role="final_proof_auditor",
            archive_dir=self.run_dir / "archive" / "final_auditor",
            working_dir=self.run_dir / "codex" / "final_auditor",
        )
        system, prompt = final_auditor_prompt(context, candidate, audits)
        try:
            response = final_client.call(
                prompt=prompt,
                system_prompt=system,
                label="final_proof_auditor",
                archive_path=audits_dir / "final_proof_auditor_call.md",
            )
            final = normalize_audit_result(
                "final_proof_auditor",
                _extract_json(response.get("result", "")),
            ).to_dict()
        except Exception as exc:
            final = AuditResult.from_exception(
                "final_proof_auditor", exc
            ).to_dict()
        audits["final_proof_auditor"] = final
        _write_json(audits_dir / "final_proof_auditor.json", final)

        for client in list(clients.values()) + [final_client]:
            client.cleanup()
        normalized = {
            role: normalize_audit_result(role, data)
            for role, data in audits.items()
        }
        specialist_pass = all(normalized[role].passed for role in AUDITOR_ROLES)
        criteria = final.get("criteria", {})
        failure_reasons = []
        execution_errors = []
        inconclusive_audits = []
        for role, result in normalized.items():
            if result.execution_status == "ERROR":
                execution_errors.append(
                    f"{role}: {result.execution_error or 'auditor execution failed'}"
                )
            elif result.domain_verdict == "INCONCLUSIVE":
                inconclusive_audits.append(role)
            elif result.mathematically_failed:
                reasons = result.failure_reasons or [f"{role} returned FAIL"]
                failure_reasons.extend(str(reason) for reason in reasons)
        blocked = self.state.get("blocked_dependencies", [])
        cycles = self.state.get("dependency_cycles", [])
        if blocked:
            failure_reasons.append("Non-PROVED dependencies in slice: " + ", ".join(blocked))
        if cycles:
            failure_reasons.append("Dependency cycle detected")
        if is_mock_config(self.config) and not self.project.load_project().get("demo", False):
            failure_reasons.append("Mock auditors cannot promote a non-demo project to PROVED")

        gate = AuditGate(
            forward_implication=bool(criteria.get("forward_implication")),
            converse_if_applicable=bool(criteria.get("converse_if_applicable")),
            exhaustive_cases=bool(criteria.get("exhaustive_cases")),
            parameter_ranges=bool(criteria.get("parameter_ranges")),
            boundary_cases=bool(criteria.get("boundary_cases")),
            dependencies_valid=(
                bool(criteria.get("dependencies_valid"))
                and dependency_report.admissible
                and normalized["dependency_auditor"].passed
                and not blocked
                and not cycles
            ),
            no_counterexample=(
                bool(criteria.get("no_counterexample"))
                and normalized["counterexample_hunter"].passed
            ),
            auditors_pass=bool(criteria.get("auditors_pass")) and specialist_pass,
            final_auditor_pass=normalized["final_proof_auditor"].passed,
            computational_evidence_separated=bool(criteria.get("computational_evidence_separated")),
            failure_reasons=failure_reasons,
            execution_errors=execution_errors,
            inconclusive_audits=inconclusive_audits,
            dependency_report=dependency_report.to_dict(),
        )
        self.metrics["specialist_auditors"] = {
            "calls": sum(getattr(client, "call_count", 0) for client in clients.values()),
            "wall_clock_seconds": round(time.perf_counter() - started, 3),
            "success": specialist_pass,
            "retry_count": 0,
            "provider_retry_count": sum(
                getattr(client, "total_retries", 0) for client in clients.values()
            ),
            "api_request_count": sum(
                _api_request_count(client) for client in clients.values()
            ),
            "codex_process_count": sum(
                _codex_process_count(client) for client in clients.values()
            ),
            "billing_modes": sorted({
                mode for client in clients.values()
                if (mode := getattr(client, "billing_mode", None))
            }),
            "usage": _sum_usage(list(clients.values())),
        }
        self.metrics["final_auditor"] = {
            "calls": getattr(final_client, "call_count", 0),
            "success": normalized["final_proof_auditor"].passed,
            "retry_count": 0,
            "provider_retry_count": getattr(final_client, "total_retries", 0),
            "api_request_count": _api_request_count(final_client),
            "codex_process_count": _codex_process_count(final_client),
            "billing_mode": getattr(final_client, "billing_mode", None),
            "usage": _usage_metrics(final_client),
        }
        self.metrics["routing"] = self.model_router.snapshot()
        self.metrics["pipelines"] = self.pipeline_scheduler.snapshot()
        _write_json(self.run_dir / "usage.json", self.metrics)
        return audits, gate

    def _finalize(self, gate: AuditGate) -> None:
        if gate.passed:
            report_path = self._write_resolution_report(gate)
            theorem = self.project.load_theorem(self.target_id)
            theorem["proof_file"] = report_path.relative_to(self.project.root).as_posix()
            theorem["proof_type"] = "MOCKED_DEMO" if is_mock_config(self.config) else "NATURAL_LANGUAGE"
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
                    if path.name not in {"gate.json", "dependency_report.json"} and not path.name.startswith("infrastructure_retry_"):
                        audit_results[path.stem] = json.loads(
                            path.read_text(encoding="utf-8")
                        )
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
                    fingerprint_records.append(
                        fingerprint_store.record_failure(strategy)
                    )
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
        context_data = json.loads((self.run_dir / "context" / "context.json").read_text(encoding="utf-8"))
        candidate = (self.run_dir / "CANDIDATE_PROOF.md").read_text(encoding="utf-8")
        audits = {}
        for path in sorted((self.run_dir / "audits").glob("*.json")):
            if path.name not in {"gate.json", "dependency_report.json"}:
                audits[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = self.project.root / "reports" / f"{self.target_id}-resolution-{timestamp}.md"
        allowed = "\n".join(
            f"- `{item['id']}`: {item['title']}" for item in context_data["allowed_dependencies"]
        ) or "- (none)"
        frozen = "\n".join(f"- {item}" for item in context_data["frozen_branches"]) or "- (none)"
        audit_lines = []
        for name, audit in audits.items():
            audit_lines.append(
                f"- **{name}**: {audit.get('domain_verdict', audit.get('verdict', 'INCONCLUSIVE'))} "
                f"/ {audit.get('execution_status', 'OK')} — "
                f"{audit.get('summary') or '; '.join(audit.get('findings', [])) or 'No summary'}"
            )
        report = f"""# {self.target['title']} — Resolution

## Scope

Target `{self.target_id}` only; generated by run `{self.run_dir.name}`.

## Allowed dependencies

{allowed}

## Frozen branches

{frozen}

## Statement

{self.target['statement']}

## Definitions

See the candidate proof below; no definition outside the dependency slice is implicitly authorized.

## Lemmas

Only the PROVED dependencies listed above may be invoked.

## Proof

{candidate}

## Converse / reconstruction

Audited according to claim type `{self.target.get('claim_type', 'implication')}`.

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
