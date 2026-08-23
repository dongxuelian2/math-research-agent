from __future__ import annotations

import threading
import time

from math_research_agent.research.pipelines import AsyncDAGScheduler, AtomicResourceBudget


def test_blocking_and_nonblocking_requests_only_freeze_dependent_path(tmp_path):
    scheduler = AsyncDAGScheduler(
        state_path=tmp_path / "state.json", config={"routing": {"allow_dual_track": True}}
    )
    scheduler.add_obligation("L", target_statement="lemma")
    scheduler.add_obligation("A1", target_statement="independent")
    scheduler.add_obligation("A2", target_statement="dependent", dependencies=["L"])
    scheduler.add_literature_request(
        {
            "obligation_id": "L",
            "requested_statement": "lemma",
            "why_needed": "avoid rediscovery",
            "blocking_or_nonblocking": "blocking",
            "expected_impact": "HIGH",
            "search_hints": ["exact"],
        }
    )
    snapshot = scheduler.snapshot()
    assert snapshot["obligations"]["A2"]["status"] == "BLOCKED_DEPENDENCY"
    assert snapshot["obligations"]["A1"]["status"] == "PROOF_READY"
    scheduler.add_literature_request(
        {
            "obligation_id": "A1",
            "requested_statement": "optional",
            "why_needed": "context",
            "blocking_or_nonblocking": "nonblocking",
            "expected_impact": "LOW",
            "search_hints": ["method"],
        }
    )
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

    scheduler = AsyncDAGScheduler(
        state_path=tmp_path / "state.json", config={"routing": {"allow_dual_track": True}}
    )
    scheduler.add_obligation("L", target_statement="dual", literature_first=True, dual_track=True)
    from math_research_agent.research.pipelines import AsynchronousPipelineRuntime

    runtime = AsynchronousPipelineRuntime(
        scheduler, {"proof": proof, "literature": lambda task: {}}
    )
    try:
        runtime.start_window({"proof": 1, "literature": 0, "verification": 0})
        assert started.wait(2)
        scheduler.apply_literature_result(
            "L", verdict="EXACT_RESULT_FOUND", authority_status="VERIFIED_SOURCE_THEOREM"
        )
        assert not interrupted.is_set()
        reconstruction = next(
            task
            for task in scheduler.snapshot()["tasks"].values()
            if task["obligation_id"] == "L" and task["role"] == "reconstruction"
        )
        scheduler.dispatch_window({"proof": 0, "literature": 0, "verification": 10})
        snapshot_hash = scheduler.applicability_context("L")["assumption_snapshot_hash"]
        scheduler.complete_task(
            reconstruction["task_id"],
            {
                "verdict": "APPLICABILITY_CANDIDATE",
                "applicability_id": "app-L",
                "assumption_snapshot_hash": snapshot_hash,
            },
        )
        verifier = next(
            task
            for task in scheduler.dispatch_window(
                {"proof": 0, "literature": 0, "verification": 10}
            )["verification"]
            if task["obligation_id"] == "L" and task["role"] == "theorem_verifier"
        )
        scheduler.complete_task(
            verifier["task_id"],
            {
                "verdict": "APPLICABLE",
                "authority_status": "APPLICABLE_EXTERNAL_AUTHORITY",
                "applicability_status": "APPLICABLE_EXTERNAL_AUTHORITY",
                "applicability_id": "app-L",
                "assumption_snapshot_hash": snapshot_hash,
                "deterministic_applicability_promotion": True,
            },
        )
        deadline = time.time() + 3
        while runtime.pending() and time.time() < deadline:
            runtime.poll()
            time.sleep(0.01)
        assert interrupted.is_set()
        task = scheduler.snapshot()["tasks"][
            scheduler.snapshot()["dual_tracks"]["L"]["speculative_proof_task_id"]
        ]
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
    resumed = AsyncDAGScheduler(
        state_path=tmp_path / "state.json", config={"global_budget": {"provider_calls": 1}}
    )
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
