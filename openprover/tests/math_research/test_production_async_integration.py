from __future__ import annotations

import json
import threading
import time

from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.pipelines import AsyncDAGScheduler, AtomicResourceBudget
from openprover.math_research.project import ProjectStore


def _config(path):
    path.write_text(json.dumps({
        "schema_version": 2,
        "budget": {"mode": "time", "limit": 2},
        "roles": {
            "planner": {"provider": "mock", "model": "mock-planner"},
            "worker": {"provider": "mock", "model": "mock-worker"},
            "theorem_verifier": {"provider": "mock", "model": "mock-verifier"},
            "literature_lead": {"provider": "mock", "model": "mock-lead"},
            "literature_searcher": {"provider": "mock", "model": "mock-searcher"},
            "literature_authority_auditor": {"provider": "mock", "model": "mock-auditor"},
            "final_auditor": {"provider": "mock", "model": "mock-final"},
            "cheap_auditor": {"provider": "mock", "model": "mock-auditor"},
        },
        "routing": {"allow_dual_track": True, "allow_proof_fallback_when_literature_unavailable": True},
        "literature": {"external_transmission_approved": False},
    }), encoding="utf-8")
    return path


def _orchestrator(tmp_path, handlers):
    project = ProjectStore.initialize(tmp_path / "project", "integration", demo=True)
    project.add_theorem("target", "Target", "For all n, n = n.", status="OPEN", claim_type="equality")
    return ResearchOrchestrator(
        project,
        "target",
        config_path=_config(tmp_path / "config.json"),
        run_id="target-integration",
        pipeline_handlers=handlers,
    )


def test_actual_orchestrator_runtime_runs_three_queues_without_barrier(tmp_path):
    release = threading.Event()
    started = {name: threading.Event() for name in ("proof", "literature", "verification")}

    def make(name):
        def handler(task, context):
            started[name].set()
            assert release.wait(2)
            return {"success": False} if name == "proof" else (
                {"literature_verdict": "NO_SUFFICIENT_RESULT_FOUND"}
                if name == "literature" else {"verdict": "UNCERTAIN"}
            )
        return handler

    orchestrator = _orchestrator(tmp_path, {name: make(name) for name in started})
    try:
        scheduler = orchestrator.pipeline_scheduler
        scheduler.add_obligation("A", target_statement="proof A")
        scheduler.add_obligation("L", target_statement="lit L", literature_first=True)
        scheduler.add_obligation("V", target_statement="verify V")
        scheduler.create_task("verification", "V", role="theorem_verifier")
        window = orchestrator.pipeline_runtime.start_window({"proof": 1, "literature": 1, "verification": 1})
        assert all(len(window[name]) == 1 for name in started)
        assert all(event.wait(2) for event in started.values())
        active = scheduler.snapshot()["active"]
        assert all(len(active[name]) == 1 for name in started)
        release.set()
        deadline = time.time() + 3
        while orchestrator.pipeline_runtime.pending() and time.time() < deadline:
            orchestrator.pipeline_runtime.poll()
            time.sleep(0.01)
        assert not orchestrator.pipeline_runtime.pending()
    finally:
        release.set()
        orchestrator.close()


def test_blocking_and_nonblocking_requests_only_freeze_dependent_path(tmp_path):
    scheduler = AsyncDAGScheduler(state_path=tmp_path / "state.json", config={"routing": {"allow_dual_track": True}})
    scheduler.add_obligation("L", target_statement="lemma")
    scheduler.add_obligation("A1", target_statement="independent")
    scheduler.add_obligation("A2", target_statement="dependent", dependencies=["L"])
    scheduler.add_literature_request({
        "obligation_id": "L", "requested_statement": "lemma", "why_needed": "avoid rediscovery",
        "blocking_or_nonblocking": "blocking", "expected_impact": "HIGH", "search_hints": ["exact"],
    })
    snapshot = scheduler.snapshot()
    assert snapshot["obligations"]["A2"]["status"] == "BLOCKED_DEPENDENCY"
    assert snapshot["obligations"]["A1"]["status"] == "PROOF_READY"
    scheduler.add_literature_request({
        "obligation_id": "A1", "requested_statement": "optional", "why_needed": "context",
        "blocking_or_nonblocking": "nonblocking", "expected_impact": "LOW", "search_hints": ["method"],
    })
    assert scheduler.snapshot()["obligations"]["A1"]["status"] == "PROOF_READY"


def test_dual_track_cancellation_interrupts_running_task(tmp_path):
    started = threading.Event()
    interrupted = threading.Event()

    class Handle:
        def interrupt(self):
            interrupted.set()

    def proof(_task, context):
        context.set_handle(Handle())
        started.set()
        while not context.cancel_event.is_set():
            time.sleep(0.01)
        raise RuntimeError("interrupted running provider")

    scheduler = AsyncDAGScheduler(state_path=tmp_path / "state.json", config={"routing": {"allow_dual_track": True}})
    scheduler.add_obligation("L", target_statement="dual", literature_first=True, dual_track=True)
    from openprover.math_research.pipelines import AsynchronousPipelineRuntime
    runtime = AsynchronousPipelineRuntime(scheduler, {"proof": proof, "literature": lambda task: {}})
    try:
        runtime.start_window({"proof": 1, "literature": 0, "verification": 0})
        assert started.wait(2)
        scheduler.apply_literature_result("L", verdict="EXACT_RESULT_FOUND", authority_status="VERIFIED_EXTERNAL_AUTHORITY")
        assert not interrupted.is_set()
        reconstruction = next(
            task for task in scheduler.snapshot()["tasks"].values()
            if task["obligation_id"] == "L" and task["role"] == "reconstruction"
        )
        scheduler.dispatch_window({"proof": 0, "literature": 0, "verification": 10})
        snapshot_hash = scheduler.applicability_context("L")["assumption_snapshot_hash"]
        scheduler.complete_task(reconstruction["task_id"], {
            "verdict": "APPLICABILITY_CANDIDATE",
            "applicability_id": "app-L",
            "assumption_snapshot_hash": snapshot_hash,
        })
        verifier = next(
            task for task in scheduler.dispatch_window(
                {"proof": 0, "literature": 0, "verification": 10}
            )["verification"]
            if task["obligation_id"] == "L" and task["role"] == "theorem_verifier"
        )
        scheduler.complete_task(verifier["task_id"], {
            "verdict": "APPLICABLE",
            "authority_status": "APPLICABLE_EXTERNAL_AUTHORITY",
            "applicability_status": "APPLICABLE_EXTERNAL_AUTHORITY",
            "applicability_id": "app-L",
            "assumption_snapshot_hash": snapshot_hash,
            "deterministic_applicability_promotion": True,
        })
        deadline = time.time() + 3
        while runtime.pending() and time.time() < deadline:
            runtime.poll()
            time.sleep(0.01)
        assert interrupted.is_set()
        task = scheduler.snapshot()["tasks"][scheduler.snapshot()["dual_tracks"]["L"]["speculative_proof_task_id"]]
        assert task["status"] in {"INTERRUPTED", "REDIRECTED", "COMPLETED_BEFORE_CANCEL"}
    finally:
        runtime.shutdown(wait=True)


def test_resume_reconciles_orphan_and_atomic_budget_is_shared(tmp_path):
    scheduler = AsyncDAGScheduler(
        state_path=tmp_path / "state.json",
        config={"global_budget": {"provider_calls": 1}},
    )
    scheduler.add_obligation("A", target_statement="a")
    task = scheduler.dispatch_window({"proof": 1, "literature": 0, "verification": 0})["proof"][0]
    resumed = AsyncDAGScheduler(state_path=tmp_path / "state.json", config={"global_budget": {"provider_calls": 1}})
    recovered = resumed.snapshot()["tasks"][task["task_id"]]
    assert recovered["status"] == "RETRY_READY"
    assert task["task_id"] in resumed.snapshot()["queues"]["PROOF_QUEUE"]

    budget = AtomicResourceBudget({"provider_calls": 2})
    results = []
    barrier = threading.Barrier(4)

    def reserve():
        barrier.wait()
        try:
            budget.reserve({"provider_calls": 1})
            results.append(True)
        except Exception:
            results.append(False)

    workers = [threading.Thread(target=reserve) for _ in range(3)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()
    assert sum(results) == 2
