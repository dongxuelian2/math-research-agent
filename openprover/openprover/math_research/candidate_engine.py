"""Candidate search component for the research run."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from openprover.budget import Budget
from openprover.prover import Prover
from openprover.tui import HeadlessTUI

from .campaign import PreSubmitGate
from .openprover_adapter import ResearchPolicy
from .providers import create_client
from .routing import RoutedLLMClient
from .scheduler import RoleScheduler
from .trust_kernel import DependencyAuthorityResolver, TrustKernel


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _usage_metrics(client: object | None) -> dict:
    if client is None:
        return {}
    usage = getattr(client, "total_usage", None)
    return dict(usage) if isinstance(usage, dict) else {}


def _api_request_count(client: object | None) -> int:
    if client is None:
        return 0
    if hasattr(client, "api_request_count"):
        return int(getattr(client, "api_request_count"))
    return int(getattr(client, "request_count", 0))


class _OwnerComponent:
    def __init__(self, owner):
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name):
        return getattr(self._owner, name)

    def __setattr__(self, name, value):
        if name == "_owner":
            object.__setattr__(self, name, value)
        else:
            setattr(self._owner, name, value)


class CandidateEngine(_OwnerComponent):
    """Run the upstream engine through a narrow black-box adapter."""

    def run(self) -> None:
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
                working_dir=self.run_dir / "gemini" / "planner",
            )
            holders["planner"] = client
            return client

        def make_worker(_archive_dir):
            client = RoutedLLMClient(
                self.model_router,
                client_factory=create_client,
                default_role="worker",
                archive_dir=archive / "worker",
                working_dir=self.run_dir / "gemini" / "worker",
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
        policy_kwargs = {}
        if self.hard_submit_gate:
            context_data = json.loads(
                (self.run_dir / "context" / "context.json").read_text(encoding="utf-8")
            )
            trust_kernel = TrustKernel.for_project(self.project)
            resolver = DependencyAuthorityResolver(
                foundations=trust_kernel.foundations,
                semantics=trust_kernel.semantics,
                project=self.project,
                notation_scope=context_data.get("notation_scope", ""),
            )
            policy_kwargs.update(
                {
                    "pre_submit_gate": PreSubmitGate(
                        resolver=resolver,
                        blocked_dependencies=self.state.get("blocked_dependencies", []),
                        dependency_cycles=self.state.get("dependency_cycles", []),
                        replay_policy=self.replay_policy,
                        require_manifest=True,
                    ),
                    "pre_submit_gate_path": self.run_dir / "pre_submit_gate.json",
                }
            )
        if self.role_scheduling:
            policy_kwargs["role_scheduler"] = RoleScheduler(
                initial_workers=self.initial_worker_count,
                max_workers=self.worker_count,
            )
        if self.stop_controller is not None:
            policy_kwargs["stop_controller"] = self.stop_controller
        policy_kwargs["pipeline_scheduler"] = self.pipeline_scheduler
        # ModelRouter owns per-call compute selection only. Typed worker outcomes
        # are retained by the tactical/session path and must not become durable
        # research-strategy counters inside the router.
        policy_kwargs["root_obligation_id"] = self.target_id
        prover_kwargs["research_policy"] = ResearchPolicy(**policy_kwargs)
        active_proof_task = self._claim_target_pipeline_task("proof")
        # Literature and verification tasks belong to the same run lifetime as
        # the long-running OpenProver call.  They are monitored in parallel;
        # only the target proof task itself remains owned by OpenProver.
        self._start_async_pipeline_monitor(include_proof=False)
        prover = Prover(**prover_kwargs)
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
            "billing_mode": getattr(holders.get("worker"), "billing_mode", None),
            "usage": _usage_metrics(holders.get("worker")),
        }
        self.metrics["routing"] = self.model_router.snapshot()
        self.metrics["pipelines"] = self.pipeline_scheduler.snapshot()
        _write_json(self.run_dir / "usage.json", self.metrics)
