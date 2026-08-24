from pathlib import Path
import io
import json
import threading

import pytest

from math_research_agent.research.project import ProjectStore
from math_research_agent.research.project_orchestrator import ProjectOrchestrator
from math_research_agent.research.schemas import ProjectSubproblemSchema
from math_research_agent.research.ui_events import UiEventEmitter


def test_project_orchestrator_plans_children_from_purpose(tmp_path):
    project = ProjectStore.initialize(
        tmp_path / "project",
        "Core project",
        purpose="Understand a core mathematical identity",
    )
    result = ProjectOrchestrator(
        project,
        config_path=Path(__file__).parents[2] / "tests" / "fixtures" / "models.mock.toml",
        max_subproblems=1,
    ).run(plan_only=True)

    assert result["status"] == "PLANNED"
    assert result["subproblem_ids"] == ["purpose-analysis"]
    assert project.load_project()["purpose"] == "Understand a core mathematical identity"
    assert project.load_theorem("purpose-analysis")["status"] == "OPEN"
    assert project.load_project()["orchestrator"]["status"] == "PLANNED"
    assert project.load_project()["display_title"] == "Core project"


def test_project_subproblem_accepts_existence_claim_type():
    item = ProjectSubproblemSchema(
        id="existence",
        title="Existence obligation",
        statement="There exists a witness satisfying the stated property.",
        claim_type="existence",
    )
    assert item.claim_type == "existence"


def test_project_orchestrator_emits_compact_ui_events(tmp_path):
    project = ProjectStore.initialize(
        tmp_path / "project",
        "Core project",
        purpose="Understand a core mathematical identity",
    )
    output = io.StringIO()
    emitter = UiEventEmitter(
        project_id="core-project",
        project_root=project.root,
        stream=output,
    )
    ProjectOrchestrator(
        project,
        config_path=Path(__file__).parents[2] / "tests" / "fixtures" / "models.mock.toml",
        max_subproblems=1,
        event_sink=emitter,
    ).run(plan_only=True)
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["status"] for event in events[:2]] == ["STARTED", "PROGRESS"]
    assert any(event["action"] == "materialize_subproblem" for event in events)
    assert events[-1]["status"] == "COMPLETED"


def test_project_title_fallback_is_short_and_deterministic():
    purpose = "这是一个很长很长的数学研究目标，用于验证常驻界面标题不会无限增长"
    title = ProjectOrchestrator._project_title(
        "",
        {"display_title": ""},
        purpose,
    )
    assert len(title) <= 32
    assert title == purpose[:32]


def test_project_orchestrator_mock_e2e_emits_real_stage_actions(tmp_path):
    project = ProjectStore.initialize(
        tmp_path / "project",
        "Core project",
        purpose="Understand a core mathematical identity",
    )
    output = io.StringIO()
    emitter = UiEventEmitter(
        project_id="core-project",
        project_root=project.root,
        stream=output,
    )
    result = ProjectOrchestrator(
        project,
        config_path=Path(__file__).parents[2] / "tests" / "fixtures" / "models.mock.toml",
        max_subproblems=1,
        event_sink=emitter,
    ).run()
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    actions = {event["action"] for event in events}
    assert result["status"] in {"COMPLETE", "PARTIAL"}
    assert {
        "plan_project",
        "validate_project_plan",
        "materialize_subproblem",
        "prove_subproblem",
        "generate_candidate",
        "verify_candidate",
        "audit_candidate",
        "persist_project_result",
        "summarize_project",
    } <= actions
    assert "heartbeat" not in actions
    assert "traceback" not in output.getvalue().casefold()


def test_project_orchestrator_reuses_plan_and_child_checkpoint(tmp_path):
    project = ProjectStore.initialize(
        tmp_path / "project",
        "Core project",
        purpose="Understand a core mathematical identity",
    )
    config = Path(__file__).parents[2] / "tests" / "fixtures" / "models.mock.toml"

    first = ProjectOrchestrator(project, config_path=config, max_subproblems=1).run()
    second = ProjectOrchestrator(project, config_path=config, max_subproblems=1).run()

    assert first["plan_run"] == second["plan_run"]
    assert second["children"][0]["resumed"] is True
    timeline = (project.root / "timeline.jsonl").read_text(encoding="utf-8")
    assert '"action":"resume_project"' in timeline


def test_planner_failure_is_persisted_as_infrastructure_block(tmp_path, monkeypatch):
    project = ProjectStore.initialize(
        tmp_path / "project",
        "Core project",
        purpose="Understand a core mathematical identity",
    )
    orchestrator = ProjectOrchestrator(
        project,
        config_path=Path(__file__).parents[2] / "tests" / "fixtures" / "models.mock.toml",
    )

    def fail_planning(*_args, **_kwargs):
        raise RuntimeError("planner response incomplete")

    monkeypatch.setattr(orchestrator, "_plan", fail_planning)
    with pytest.raises(RuntimeError, match="incomplete"):
        orchestrator.run()

    status = project.load_project()["orchestrator"]
    assert status["status"] == "BLOCKED_INFRASTRUCTURE"
    assert status["phase"] == "PLANNING"
    assert status["error"] == "planner response incomplete"


def test_independent_child_failure_does_not_stop_later_subproblems(tmp_path, monkeypatch):
    project = ProjectStore.initialize(
        tmp_path / "project",
        "Core project",
        purpose="Understand a core mathematical identity",
    )
    for theorem_id in ("first", "second"):
        project.add_theorem(
            theorem_id,
            theorem_id.title(),
            f"Prove {theorem_id}",
            status="OPEN",
        )
    metadata = project.load_project()
    metadata["orchestrator"] = {"status": "RUNNING"}
    project.save_project(metadata)

    calls = []
    barrier = threading.Barrier(2)

    class PartialChild:
        def __init__(self, _project, target_id, **_kwargs):
            calls.append(target_id)

        def run(self):
            barrier.wait(timeout=2)
            return {"status": "PARTIAL", "run_id": "child-run"}

    monkeypatch.setattr(
        "math_research_agent.research.orchestrator.ResearchOrchestrator",
        PartialChild,
    )
    orchestrator = ProjectOrchestrator(
        project,
        config_path=Path(__file__).parents[2] / "tests" / "fixtures" / "models.mock.toml",
    )
    result = orchestrator._run_children(
        [
            ProjectSubproblemSchema(
                id="first",
                title="First",
                statement="Prove first",
                dependencies=[],
                tags=[],
                branch="main",
                proof_type="NATURAL_LANGUAGE",
                claim_type="implication",
            ),
            ProjectSubproblemSchema(
                id="second",
                title="Second",
                statement="Prove second",
                dependencies=[],
                tags=[],
                branch="main",
                proof_type="NATURAL_LANGUAGE",
                claim_type="implication",
            ),
        ],
        "orchestrator-run",
        "Understand a core mathematical identity",
    )

    assert set(calls) == {"first", "second"}
    assert [child["id"] for child in result["children"]] == ["first", "second"]
    assert result["status"] == "PARTIAL"
