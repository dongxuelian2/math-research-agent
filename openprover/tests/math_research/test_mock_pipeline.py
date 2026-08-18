import json

from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectStore


def mock_config(path):
    data = {
        "schema_version": 1,
        "isolation": True,
        "budget": {"mode": "time", "limit": 120, "conclude_after": 0.99},
        "roles": {
            "planner": {"provider": "mock", "model": "mock-planner"},
            "worker": {"provider": "mock", "model": "mock-worker"},
            "cheap_auditor": {"provider": "mock", "model": "mock-auditor"},
            "final_auditor": {"provider": "mock", "model": "mock-final"},
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_mock_pipeline_and_resume(tmp_path):
    store = ProjectStore.initialize(tmp_path / "demo", "Demo", demo=True)
    store.add_theorem(
        "demo-next-square",
        "Next-square identity",
        "For every natural n, (n+1)^2 = n^2 + 2n + 1.",
        status="PROVED",
        tags=["demo"],
        claim_type="equality",
    )
    store.add_theorem(
        "demo-odd-sum",
        "Sum of odd numbers",
        "For every natural n, sum_{k=1}^n (2k-1) = n^2.",
        dependencies=["demo-next-square"],
        tags=["demo"],
        claim_type="equality",
    )
    config = mock_config(tmp_path / "mock.json")
    first = ResearchOrchestrator(
        store, "demo-odd-sum", config_path=config, worker_count=3, dry_run=False,
    )
    state = first.run(stop_after="context")
    assert state["phase"] == "CONTEXT_READY"

    resumed = ResearchOrchestrator(
        store,
        "demo-odd-sum",
        config_path=config,
        worker_count=3,
        dry_run=False,
        resume="latest",
    )
    final = resumed.run()
    assert final["status"] == "PROVED"
    assert store.load_theorem("demo-odd-sum")["status"] == "PROVED"
    worker_tasks = list(resumed.run_dir.glob("openprover/steps/*/workers/task_*.md"))
    assert len(worker_tasks) >= 3
    assert (resumed.run_dir / "audits" / "gate.json").exists()
    assert list((store.root / "reports").glob("demo-odd-sum-resolution-*.md"))
