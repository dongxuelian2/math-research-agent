"""Bounded production-path smoke for heterogeneous routing and async queues.

The campaign/orchestrator/scheduler/runtime objects are real.  The handlers are
deliberately synthetic so this check cannot launch a mathematical campaign or
consume provider quota.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from openprover.math_research.campaign import CampaignEngine, CampaignStore
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectStore, utc_now


class MiniOrchestrator(ResearchOrchestrator):
    """Use the production object graph with deterministic local handlers."""

    def __init__(self, project, target_id, **kwargs):
        self.route_records: list[dict] = []
        super().__init__(
            project,
            target_id,
            pipeline_handlers={
                "proof": self._synthetic_proof,
                "literature": self._synthetic_literature,
                "verification": self._synthetic_verification,
            },
            **kwargs,
        )

    def _route(self, task: dict) -> dict:
        snapshot = self.pipeline_scheduler.snapshot()
        obligation = snapshot["obligations"][task["obligation_id"]]
        requested_tier = obligation.get("current_tier", "research")
        route = self.model_router.resolve(
            task.get("role", "worker"),
            obligation_id=task["obligation_id"],
            requested_tier=requested_tier,
            reserve=False,
        )
        call = self.model_router.begin_call(
            route,
            obligation_id=task["obligation_id"],
            branch_id=obligation.get("branch_id", "main"),
        )
        response = {"usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0}}
        self.model_router.finish_call(call["call_id"], response=response)
        record = {
            "task_id": task["task_id"],
            "obligation_id": task["obligation_id"],
            "role": task.get("role"),
            "requested_tier": requested_tier,
            "routing_call_id": call["call_id"],
            **route.to_dict(),
        }
        self.route_records.append(record)
        return record

    def _synthetic_proof(self, task, _context):
        route = self._route(task)
        return {"success": True, "proof_candidate": "SYNTHETIC_MINI_SMOKE", "routing": route}

    def _synthetic_literature(self, task, _context):
        route = self._route(task)
        return {
            "literature_verdict": "NO_SUFFICIENT_RESULT_FOUND",
            "search_strategies": [],
            "routing": route,
        }

    def _synthetic_verification(self, task, _context):
        route = self._route(task)
        return {"verdict": "CORRECT", "all_required_gates": True, "routing": route}

    def run(self) -> dict:
        scheduler = self.pipeline_scheduler
        existing = scheduler.snapshot()["obligations"]
        if "mini-routine" not in existing:
            scheduler.add_obligation(
                "mini-routine",
                target_statement="synthetic routine obligation",
                current_tier="routine",
                fresh_independent_obligation=True,
            )
        if "mini-research" not in existing:
            scheduler.add_obligation(
                "mini-research",
                target_statement="synthetic research obligation",
                current_tier="research",
                fresh_independent_obligation=True,
            )
        if "mini-strategic" not in existing:
            scheduler.add_obligation(
                "mini-strategic",
                target_statement="synthetic strategic obligation",
                current_tier="strategic",
                fresh_independent_obligation=True,
            )
        if "mini-literature" not in existing:
            scheduler.add_obligation(
                "mini-literature",
                target_statement="synthetic literature-first obligation",
                current_tier="routine",
                literature_first=True,
                fresh_independent_obligation=True,
            )

        runtime = self.pipeline_runtime
        if runtime is None:
            raise RuntimeError("production runtime was not initialized")
        for _ in range(120):
            runtime.start_window({"proof": 3, "literature": 2, "verification": 3})
            runtime.poll()
            snap = scheduler.snapshot()
            ready = any(
                snap["tasks"].get(task_id, {}).get("status") in {"READY", "RETRY_READY"}
                for queue in snap["queues"].values()
                for task_id in queue
            )
            if not runtime.pending() and not ready:
                break
            time.sleep(0.005)
        runtime.poll()
        snap = scheduler.snapshot()
        pending = runtime.pending() or any(
            snap["tasks"].get(task_id, {}).get("status") in {"READY", "RETRY_READY", "ACTIVE"}
            for queue in snap["queues"].values()
            for task_id in queue
        )
        if pending:
            raise RuntimeError("mini-smoke left pending async work")

        resuming = self.state.get("phase") == "CHECKPOINT" or int(self.state.get("resumptions", 0)) > 0
        if not resuming:
            self._checkpoint(
                "CHECKPOINT",
                status="STOPPED_AT_CHECKPOINT",
                checkpoint_reason="MINI_SMOKE_CHECKPOINT",
                resume_phase="MINI_PIPELINE_COMPLETE",
                mini_route_records=self.route_records,
                mini_scheduler=snap,
            )
        else:
            self._checkpoint(
                "COMPLETE",
                status="PROVED",
                completed_at=utc_now(),
                mini_route_records=self.route_records,
                mini_scheduler=snap,
            )
        return self.state


def run(root: Path, config_path: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    project_root = root / "project"
    project = ProjectStore.initialize(project_root, "heterogeneous mini smoke", demo=True)
    project.add_theorem(
        "target",
        "Synthetic target",
        "For every n, n = n.",
        status="OPEN",
        claim_type="equality",
    )
    campaign_store = CampaignStore(project)
    campaign_store.create("mini-campaign", target_id="target", initial_workers=2, max_workers=2)

    instances: list[MiniOrchestrator] = []

    def factory(project, target_id, **kwargs):
        instance = MiniOrchestrator(project, target_id, **kwargs)
        instances.append(instance)
        return instance

    engine = CampaignEngine(
        project,
        config_path=config_path,
        worker_count=2,
        orchestrator_factory=factory,
    )
    first = engine.run("mini-campaign", stop_after_checkpoint=True)
    checkpoint = campaign_store.load("mini-campaign")
    campaign_store.resume("mini-campaign")
    second = engine.run("mini-campaign")
    final = campaign_store.load("mini-campaign")
    first_routes = instances[0].route_records if instances else []
    second_routes = instances[1].route_records if len(instances) > 1 else []
    route_classes = {
        item["requested_tier"]: {
            "requested_model": item.get("requested_model"),
            "actual_model": item.get("model"),
            "actual_provider": item.get("provider"),
            "fallback": item.get("fallback"),
            "reasoning_effort": item.get("reasoning_effort"),
        }
        for item in first_routes
    }
    result = {
        "phase": "C",
        "status": "PASS",
        "campaign_id": "mini-campaign",
        "first_run_status": first.get("status"),
        "first_run_phase": first.get("phase"),
        "checkpoint_campaign_status": checkpoint.get("status"),
        "checkpoint_runtime_state_run_id": checkpoint.get("runtime_state_run_id"),
        "resume_run_status": second.get("status"),
        "resume_run_phase": second.get("phase"),
        "final_campaign_status": final.get("status"),
        "route_classes": route_classes,
        "route_records": first_routes + second_routes,
        "scheduler_tasks": instances[0].pipeline_scheduler.snapshot()["tasks"] if instances else {},
        "no_external_provider_calls": True,
    }
    (root / "phase-C.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.config), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
