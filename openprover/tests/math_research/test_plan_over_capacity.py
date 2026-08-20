from __future__ import annotations

from types import SimpleNamespace

from openprover.prover import Prover


def test_plan_over_capacity_is_rejected_without_silent_task_truncation(tmp_path):
    prover = object.__new__(Prover)
    prover.research_policy = None
    prover.max_workers = 2
    logged = []
    output = []
    metadata = []
    prover.tui = SimpleNamespace(log=lambda message, **_: logged.append(message))
    prover._push_output = lambda message: output.append(message)
    prover._save_step_meta = lambda step_dir, **value: metadata.append((step_dir, value))

    result = prover._handle_spawn(
        {
            "tasks": [
                {"description": "task one"},
                {"description": "task two"},
                {"description": "task three"},
            ]
        },
        tmp_path,
        {"planner": "typed"},
    )

    assert result == "continue"
    assert output == [
        "PLAN_OVER_CAPACITY: proposed 3 tasks but max_workers=2; replan a legal batch"
    ]
    assert logged == output
    assert metadata[0][1]["status"] == "rejected"
    assert metadata[0][1]["error"] == output[0]
    assert not (tmp_path / "workers").exists()
