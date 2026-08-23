"""Public policy adapter between the research layer and Math Research Agent core."""

from __future__ import annotations

import json
from pathlib import Path

from .campaign import PreSubmitGate
from .project import ProjectError, utc_now
from .scheduler import RoleScheduler, StopController
from .schemas import SchemaError, WorkerEventSchema, parse_worker_event_footer, structured_payload


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ResearchPolicy:
    """Research-only hooks consumed by Prover's public policy interface."""

    def __init__(
        self,
        *,
        pre_submit_gate: PreSubmitGate | None = None,
        pre_submit_gate_path: Path | None = None,
        role_scheduler: RoleScheduler | None = None,
        stop_controller: StopController | None = None,
        pipeline_scheduler=None,
        model_router=None,
        root_obligation_id: str | None = None,
    ):
        self.pre_submit_gate = pre_submit_gate
        self.pre_submit_gate_path = pre_submit_gate_path
        self.role_scheduler = role_scheduler
        self.stop_controller = stop_controller
        self.pipeline_scheduler = pipeline_scheduler
        self.model_router = model_router
        self.root_obligation_id = root_obligation_id

    def before_submit(self, prover, plan: dict, step_dir: Path) -> str | None:
        proof_slug = plan.get("proof_slug", "")
        content = prover.repo.read_item(proof_slug) if proof_slug else None
        if not content or self.pre_submit_gate is None:
            return None
        decision = self.pre_submit_gate.evaluate(content)
        if self.pre_submit_gate_path is not None:
            _write_json(self.pre_submit_gate_path, decision.to_dict())
        if not decision.allowed:
            blocker_text = "; ".join(
                f"{item['type']}: {item['detail']}" for item in decision.blockers
            )
            prover.tui.log(
                f"submit_proof blocked by research harness: {blocker_text}",
                color="red",
            )
            return "continue"
        return None

    def prepare_spawn(self, prover, plan: dict, step_dir: Path, planner_resp: dict | None = None):
        if self.stop_controller is not None and self.stop_controller.requested():
            _write_json(
                prover.work_dir / "graceful_stop.json",
                {
                    "status": "STOPPED_BEFORE_NEW_WORKER",
                    "created_at": utc_now(),
                },
            )
            prover.tui.log(
                "Graceful stop requested: no new Worker was started; checkpoint now.",
                color="yellow",
            )
            return plan, "stop"
        if self.role_scheduler is None:
            return plan
        original_tasks = list(plan.get("tasks", []))
        assignments = self.role_scheduler.assign_tasks(original_tasks)
        prepared = dict(plan)
        prepared["tasks"] = [
            {
                **dict(original_tasks[item.index]),
                "summary": f"[{item.role}] {item.summary}",
                "description": item.description,
                "worker_role": item.role,
            }
            for item in assignments
        ]
        _write_json(
            step_dir / "worker_assignments.json",
            {
                "schema_version": 3,
                "capacity": len(assignments),
                "assignments": [item.to_dict() for item in assignments],
            },
        )
        return prepared

    def after_spawn(self, prover, plan: dict, step_dir: Path, status: str) -> None:
        self._record_worker_events(prover, plan, step_dir)

    def after_worker_batch(
        self,
        prover,
        plan: dict,
        step_dir: Path,
        worker_responses: list[dict | None],
    ) -> None:
        """Materialize validated Worker events before any state consumer runs."""

        workers_dir = step_dir / "workers"
        tasks = list(plan.get("tasks", []))
        for index, response in enumerate(worker_responses):
            artifact_index = int(tasks[index].get("_original_index", index))
            self._materialize_event(
                workers_dir / f"event_{artifact_index}.json",
                response,
                role="worker",
            )

    def after_verifier_batch(
        self,
        prover,
        plan: dict,
        step_dir: Path,
        verifier_responses: dict[int, dict],
    ) -> None:
        """Materialize validated Verifier events keyed by Worker index."""

        workers_dir = step_dir / "workers"
        tasks = list(plan.get("tasks", []))
        for index, task in enumerate(tasks):
            artifact_index = int(task.get("_original_index", index))
            self._materialize_event(
                workers_dir / f"verifier_event_{artifact_index}.json",
                verifier_responses.get(index),
                role="verifier",
            )

    @staticmethod
    def _materialize_event(path: Path, response: dict | None, *, role: str) -> None:
        error = str((response or {}).get("error") or "").strip()
        output = str((response or {}).get("result") or "")
        if error:
            event = WorkerEventSchema(
                event="ERROR",
                failure_kind="WORKER_EXECUTION_ERROR" if role == "worker" else "VERIFIER_ERROR",
                details=[error],
            )
        else:
            try:
                event = parse_worker_event_footer(output)
            except SchemaError as exc:
                event = WorkerEventSchema(
                    event="ERROR",
                    failure_kind="MISSING_OR_INVALID_TYPED_EVENT",
                    details=[str(exc)],
                )
        _write_json(path, structured_payload(event))

    def _record_worker_events(self, prover, plan: dict, step_dir: Path) -> None:
        """Bridge only typed Worker event artifacts into routing and the DAG."""
        tasks = list(plan.get("tasks", []))
        workers_dir = step_dir / "workers"
        progress = {
            "branch_closure": False,
            "parameter_reduction": False,
            "stronger_invariant": False,
            "verified_lemma": False,
            "dependency_simplification": False,
        }
        signal_map = {
            "BRANCH_CLOSURE": "branch_closure",
            "PARAMETER_REDUCTION": "parameter_reduction",
            "STRONGER_INVARIANT": "stronger_invariant",
            "VERIFIED_LEMMA": "verified_lemma",
            "DEPENDENCY_SIMPLIFICATION": "dependency_simplification",
        }

        for index, task in enumerate(tasks):
            artifact_index = int(task.get("_original_index", index))
            obligation_id = str(
                task.get("obligation_id") or task.get("obligation") or prover.work_dir.name
            )
            event = self._load_worker_event(workers_dir / f"event_{artifact_index}.json")
            verifier_event = self._load_worker_event(
                workers_dir / f"verifier_event_{artifact_index}.json"
            )
            if event.literature_request and self.pipeline_scheduler is not None:
                request = dict(event.literature_request)
                request.setdefault("obligation_id", obligation_id)
                try:
                    self.pipeline_scheduler.add_literature_request(request)
                except ProjectError:
                    pass
            for signal in event.progress_signals:
                key = signal_map.get(signal)
                if key:
                    progress[key] = True
            if self.model_router is None:
                continue
            if event.verdict.value == "CORRECT" and verifier_event.verdict.value in {
                "FLAWED",
                "CRITICALLY_FLAWED",
                "UNCERTAIN",
            }:
                self.model_router.record_verifier_disagreement(
                    obligation_id,
                    worker_verdict=event.verdict.value,
                    verifier_verdict=verifier_event.verdict.value,
                )
            if event.event.value in {"NO_PROGRESS", "FAILED_ROUTE", "ERROR"}:
                failure_kind = (
                    event.failure_kind
                    or {
                        "NO_PROGRESS": "NO_PROGRESS",
                        "FAILED_ROUTE": "MATHEMATICAL_OBSTRUCTION",
                        "ERROR": "MALFORMED_RESULT",
                    }[event.event.value]
                )
                self.model_router.record_failure(
                    obligation_id, failure_kind, detail="typed_worker_event"
                )
            if event.high_value:
                branch = str(task.get("branch_id") or task.get("branch") or "main")
                self.model_router.promote_high_value(
                    obligation_id,
                    theorem_level=branch in {"main", "global"},
                )
        if self.model_router is not None and tasks:
            self.model_router.record_frontier_cycle(
                self.root_obligation_id or prover.work_dir.name,
                progress=progress,
            )

    @staticmethod
    def _load_worker_event(path: Path) -> WorkerEventSchema:
        if not path.exists():
            return WorkerEventSchema(
                event="ERROR",
                failure_kind="MISSING_TYPED_EVENT_SIDECAR",
                details=[f"missing typed event sidecar: {path.name}"],
            )
        try:
            return WorkerEventSchema.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            return WorkerEventSchema(
                event="ERROR",
                failure_kind="MALFORMED_RESULT",
                details=[f"invalid worker event sidecar: {type(exc).__name__}"],
            )
