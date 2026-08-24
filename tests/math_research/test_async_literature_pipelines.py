from __future__ import annotations

import json
import threading

from math_research_agent.research.pipelines import (
    AsyncDAGScheduler,
    AsynchronousPipelineRuntime,
)


def make_scheduler(tmp_path):
    return AsyncDAGScheduler(
        state_path=tmp_path / "pipeline_state.json",
        config={
            "routing": {
                "allow_dual_track": True,
                "allow_proof_fallback_when_literature_unavailable": True,
            },
            "literature_budget": {
                "initial_literature_searchers": 3,
                "max_literature_searchers": 6,
                "max_citation_chain_depth": 2,
            },
        },
    )


def test_failed_proof_result_is_not_rendered_as_successful_task_completion(tmp_path):
    timeline = tmp_path / "timeline.jsonl"
    scheduler = AsyncDAGScheduler(
        state_path=tmp_path / "pipeline_state.json",
        timeline_path=timeline,
        project_id="project",
        run_id="run",
    )
    scheduler.add_obligation("O", target_statement="proof target")
    task = scheduler.dispatch_window({"proof": 1, "literature": 0, "verification": 0})["proof"][0]

    scheduler.complete_task(task["task_id"], {"success": False, "proof_candidate": False})

    events = [json.loads(line) for line in timeline.read_text(encoding="utf-8").splitlines()]
    completed = next(event for event in events if event["action"] == "TASK_COMPLETED")
    assert completed["status"] == "FAILED"
    assert completed["payload"]["payload"]["outcome"] == "NO_CANDIDATE"


def test_cross_pipeline_concurrency_dependency_blocking_and_dual_track(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_obligation("A1", target_statement="old proof A1")
    scheduler.add_obligation("B1", target_statement="old proof B1")
    scheduler.add_obligation(
        "L",
        target_statement="new high-value lemma",
        literature_first=True,
        dual_track=True,
    )
    scheduler.add_obligation("A2", target_statement="depends on L", dependencies=["L"])
    scheduler.add_obligation("V", target_statement="already has a result")
    scheduler.create_task("verification", "V", role="theorem_verifier")

    window = scheduler.dispatch_window(
        {
            "proof": 2,
            "literature": 1,
            "verification": 1,
        }
    )
    assert len(window["proof"]) == 2
    assert len(window["literature"]) == 1
    assert len(window["verification"]) == 1
    snapshot = scheduler.snapshot()
    assert snapshot["obligations"]["A2"]["status"] == "BLOCKED_DEPENDENCY"
    assert snapshot["obligations"]["A1"]["status"] == "PROOF_ACTIVE"
    assert snapshot["obligations"]["B1"]["status"] == "PROOF_ACTIVE"

    lead = window["literature"][0]
    scheduler.complete_task(
        lead["task_id"],
        {
            "search_tasks": [
                {"strategy": "exact_theorem", "public_query": "exact theorem"},
                {"strategy": "equivalent_formulation", "public_query": "equivalent formulation"},
                {"strategy": "method_search", "public_query": "method search"},
            ]
        },
    )
    searchers = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 3,
            "verification": 0,
        }
    )["literature"]
    assert len(searchers) == 3
    assert len({task["payload"]["strategy"] for task in searchers}) == 3

    source = {
        "title": "Discovered source",
        "DOI_or_stable_identifier": "doi:10.1234/test",
        "deep_read_required": True,
    }
    # Random completion order and duplicate discovery are both safe.
    scheduler.complete_task(
        searchers[2]["task_id"],
        {
            "sources": [source],
            "create_reader": True,
        },
    )
    scheduler.complete_task(
        searchers[0]["task_id"],
        {
            "sources": [source],
            "create_reader": True,
        },
    )
    scheduler.complete_task(searchers[1]["task_id"], {"sources": []})
    snapshot = scheduler.snapshot()
    assert len(snapshot["sources"]) == 1
    assert snapshot["literature"]["duplicate_searches_avoided"] == 1
    assert any(task["role"] == "literature_deep_reader" for task in snapshot["tasks"].values())
    reader = next(
        task
        for task in scheduler.dispatch_window(
            {
                "proof": 0,
                "literature": 1,
                "verification": 0,
            }
        )["literature"]
        if task["role"] == "literature_deep_reader"
    )
    scheduler.complete_task(
        reader["task_id"],
        {
            "theorems": [{"statement": "T"}],
            "citation_chain": [{"source_id": reader["payload"]["source_id"]}],
        },
    )
    assert any(
        task["payload"].get("strategy") == "citation_chain"
        for task in scheduler.snapshot()["tasks"].values()
    )

    scheduler.apply_literature_result(
        "L",
        verdict="STRONGER_RESULT_FOUND",
        authority_status="VERIFIED_SOURCE_THEOREM",
        synthesis_path="literature/LITERATURE_SYNTHESIS.md",
    )
    snapshot = scheduler.snapshot()
    speculative_id = snapshot["dual_tracks"]["L"]["speculative_proof_task_id"]
    assert snapshot["tasks"][speculative_id]["status"] == "READY"
    reconstruction = next(
        task
        for task in snapshot["tasks"].values()
        if task["obligation_id"] == "L"
        and task["pipeline"] == "verification"
        and task["status"] == "READY"
    )
    claimed = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 0,
            "verification": 10,
        }
    )["verification"]
    assert reconstruction["task_id"] in {task["task_id"] for task in claimed}
    scheduler.complete_task(
        reconstruction["task_id"],
        {
            "verdict": "APPLICABILITY_CANDIDATE",
            "applicability_id": "app-L",
            "assumption_snapshot_hash": scheduler.applicability_context("L")[
                "assumption_snapshot_hash"
            ],
            "result_artifact": "EXTERNAL_AUTHORITY_RECONSTRUCTION.json",
        },
    )
    secondary = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 0,
            "verification": 10,
        }
    )["verification"]
    secondary = next(
        task
        for task in secondary
        if task["obligation_id"] == "L" and task["role"] == "theorem_verifier"
    )
    scheduler.complete_task(
        secondary["task_id"],
        {
            "verdict": "APPLICABLE",
            "authority_status": "APPLICABLE_EXTERNAL_AUTHORITY",
            "applicability_status": "APPLICABLE_EXTERNAL_AUTHORITY",
            "applicability_id": "app-L",
            "assumption_snapshot_hash": scheduler.applicability_context("L")[
                "assumption_snapshot_hash"
            ],
            "deterministic_applicability_promotion": True,
        },
    )
    snapshot = scheduler.snapshot()
    assert snapshot["obligations"]["L"]["status"] == "CLOSED"
    assert snapshot["tasks"][speculative_id]["status"] == "REDIRECTED"
    assert snapshot["obligations"]["A2"]["status"] == "PROOF_READY"
    assert any(
        task["obligation_id"] == "A2" and task["status"] == "READY"
        for task in snapshot["tasks"].values()
    )


def test_proof_first_dual_track_and_literature_unavailable_fallback(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_obligation(
        "L2",
        target_statement="dual lemma",
        literature_first=True,
        dual_track=True,
    )
    proof = scheduler.dispatch_window(
        {
            "proof": 1,
            "literature": 0,
            "verification": 0,
        }
    )["proof"][0]
    scheduler.complete_task(
        proof["task_id"],
        {
            "success": True,
            "proof_candidate": True,
        },
    )
    snapshot = scheduler.snapshot()
    assert snapshot["dual_tracks"]["L2"]["proof_completed_first"] is True
    assert any(
        task["obligation_id"] == "L2"
        and task["pipeline"] == "literature"
        and task["status"] == "READY"
        for task in snapshot["tasks"].values()
    )

    scheduler.add_obligation("L3", target_statement="provider unavailable", literature_first=True)
    scheduler.apply_literature_result("L3", verdict="LITERATURE_PROVIDER_UNAVAILABLE")
    fallback = scheduler.snapshot()["obligations"]["L3"]
    assert fallback["status"] == "PROOF_READY"
    assert fallback["proof_without_literature_screening"] is True


def test_resume_does_not_requeue_completed_search(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_obligation("L", target_statement="resume lemma", literature_first=True)
    lead = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 1,
            "verification": 0,
        }
    )["literature"][0]
    scheduler.complete_task(
        lead["task_id"],
        {
            "search_tasks": [
                {"strategy": "exact_theorem", "public_query": "exact theorem"},
                {"strategy": "method_search", "public_query": "method search"},
                {"strategy": "terminology_archaeology", "public_query": "terminology archaeology"},
            ]
        },
    )
    searcher = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 1,
            "verification": 0,
        }
    )["literature"][0]
    scheduler.complete_task(searcher["task_id"], {"sources": []})

    resumed = make_scheduler(tmp_path)
    snapshot = resumed.snapshot()
    assert searcher["task_id"] in snapshot["completed_task_ids"]
    assert all(searcher["task_id"] not in queue for queue in snapshot["queues"].values())


def test_concurrent_checkpoint_writes_use_unique_atomic_temp_files(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_obligation("O", target_statement="concurrent checkpoint")
    errors = []

    def save_repeatedly():
        try:
            for _ in range(20):
                scheduler._save()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=save_repeatedly) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert scheduler.snapshot()["obligations"]["O"]["target_statement"] == "concurrent checkpoint"


def test_runtime_executes_three_pipelines_without_global_barrier(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_obligation("A", target_statement="proof A")
    scheduler.add_obligation("B", target_statement="proof B")
    scheduler.add_obligation("L", target_statement="literature L", literature_first=True)
    scheduler.add_obligation("V", target_statement="verify V")
    scheduler.create_task("verification", "V", role="theorem_verifier")
    release = threading.Event()
    started = {pipeline: threading.Event() for pipeline in ("proof", "literature", "verification")}

    def handler(pipeline):
        def execute(_task):
            started[pipeline].set()
            assert release.wait(2)
            if pipeline == "literature":
                return {}
            if pipeline == "proof":
                return {"success": False}
            return {"verdict": "UNCERTAIN"}

        return execute

    runtime = AsynchronousPipelineRuntime(
        scheduler,
        {pipeline: handler(pipeline) for pipeline in started},
        max_workers=4,
    )
    window = runtime.start_window({"proof": 2, "literature": 1, "verification": 1})
    assert {pipeline: len(tasks) for pipeline, tasks in window.items()} == {
        "proof": 2,
        "literature": 1,
        "verification": 1,
    }
    assert all(event.wait(2) for event in started.values())
    # All four remain active together: literature has not frozen proof siblings.
    active = scheduler.snapshot()["active"]
    assert len(active["proof"]) == 2
    assert len(active["literature"]) == 1
    assert len(active["verification"]) == 1
    release.set()
    for future in list(runtime.futures.values()):
        future.result(timeout=2)
    assert len(runtime.poll()) == 4
    runtime.shutdown()


def test_synthesizer_and_authority_auditor_are_event_derived(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_obligation("S", target_statement="search and synthesize", literature_first=True)
    lead = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 1,
            "verification": 0,
        }
    )["literature"][0]
    scheduler.complete_task(
        lead["task_id"],
        {
            "search_tasks": [{"strategy": "exact_theorem", "public_query": "exact theorem"}],
        },
    )
    searcher = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 1,
            "verification": 0,
        }
    )["literature"][0]
    scheduler.complete_task(searcher["task_id"], {"sources": []})
    synth = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 1,
            "verification": 0,
        }
    )["literature"][0]
    assert synth["role"] == "literature_synthesizer"
    scheduler.complete_task(
        synth["task_id"],
        {
            "literature_verdict": "NO_SUFFICIENT_RESULT_FOUND",
            "synthesis_path": "S/LITERATURE_SYNTHESIS.md",
        },
    )
    assert scheduler.snapshot()["obligations"]["S"]["status"] == "PROOF_READY"

    scheduler.add_obligation("A", target_statement="authority verification", literature_first=True)
    scheduler.apply_literature_result(
        "A", verdict="EXACT_RESULT_FOUND", authority_status="UNVERIFIED_REFERENCE"
    )
    authority_task = next(
        task
        for task in scheduler.snapshot()["tasks"].values()
        if task["obligation_id"] == "A" and task["role"] == "literature_authority_auditor"
    )
    assert authority_task["status"] == "READY"
    # Complete only this claimed authority task; the original Lead may also be ready.
    claimed = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 10,
            "verification": 0,
        }
    )["literature"]
    assert authority_task["task_id"] in {task["task_id"] for task in claimed}
    scheduler.complete_task(
        authority_task["task_id"],
        {
            "authority_status": "VERIFIED_SOURCE_THEOREM",
            "literature_verdict": "EXACT_RESULT_FOUND",
            "deterministic_verification": True,
        },
    )
    assert any(
        task["obligation_id"] == "A" and task["role"] == "reconstruction"
        for task in scheduler.snapshot()["tasks"].values()
    )
