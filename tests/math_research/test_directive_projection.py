from __future__ import annotations

import dataclasses

import pytest

from math_research_agent.research.directive import BudgetProfile
from math_research_agent.research.project import ProjectError, ProjectStore
from math_research_agent.research.research_store import ResearchStoreFacade
from math_research_agent.research.retrieval import ContextBuilder
from math_research_agent.research.truth_store import TruthStoreFacade


def _directive(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Directive projection")
    project.add_theorem("T1", "Root", "Every object has property P.")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    store = ResearchStoreFacade(project, truth_store=truth)
    research_map = store.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": "O1",
                "title": "Boundary case",
                "statement": "Establish P at the boundary",
                "obligation_kind": "BOUNDARY",
                "scope": ["boundary only"],
            },
            {
                "obligation_id": "O2",
                "title": "Converse",
                "statement": "Investigate the converse",
                "obligation_kind": "CONVERSE",
                "scope": ["converse only"],
            },
        ],
        created_by="test",
    )
    directive = store.create_directive(
        research_map.research_map_id,
        "O1",
        tactical_goal="Prove the boundary case without entering the converse branch.",
        allowed_scope=("boundary only",),
        prohibited_routes=("converse argument",),
        relevant_evidence_refs=("evidence:boundary-table",),
        requested_worker_roles=("constructive", "boundary_auditor"),
        budget_profile=BudgetProfile.capture(
            wall_clock_seconds=300,
            max_workers=3,
            max_provider_calls=20,
            reasoning_tier="standard",
        ),
        created_by="test",
    )
    return project, store, snapshot, research_map, directive


def test_r13_directive_scopes_planner_input_and_worker_context(tmp_path):
    project, _, snapshot, _, directive = _directive(tmp_path)
    package = ContextBuilder(project).build(
        "T1",
        claim_snapshot=snapshot.to_dict(),
        directive=directive,
    )
    tactical = package.data["tactical_context"]
    assert tactical["source_directive_id"] == directive.directive_id
    assert tactical["obligation_id"] == "O1"
    assert package.data["allowed_scope"] == ["boundary only"]
    assert "O2" not in tactical
    assert directive.directive_id in package.markdown
    assert "Planner output cannot" in package.markdown

    worker = directive.worker_context(
        task_id="task-1",
        task_goal="Check the endpoint calculation",
        evidence_refs=("evidence:boundary-table",),
    )
    assert worker["obligation_id"] == "O1"
    with pytest.raises(ProjectError, match="exceeds Directive scope"):
        directive.worker_context(
            task_id="task-2",
            task_goal="Use unrelated evidence",
            evidence_refs=("evidence:converse",),
        )


def test_r14_directive_and_planner_projection_cannot_mutate_map(tmp_path):
    _, store, _, research_map, directive = _directive(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        directive.tactical_goal = "replace the long-term frontier"
    tactical = directive.tactical_context()
    tactical["tactical_goal"] = "planner invented a new frontier"
    tactical["allowed_scope"].append("O2")
    assert store.load_current_map(research_map.research_map_id) == research_map
    assert directive.tactical_goal != tactical["tactical_goal"]


def test_directive_and_tactical_session_are_immutable_content_artifacts(tmp_path):
    _, store, _, research_map, directive = _directive(tmp_path)
    loaded = store.load_directive(directive.directive_id)
    assert loaded == directive
    session = store.bind_tactical_session(
        directive.directive_id,
        execution_run_id="run-1",
        parent_execution_run_id="run-0",
        execution_status="RUNNING",
    )
    assert session.obligation_id == "O1"
    assert session.research_map_hash == research_map.research_map_hash
    assert session.execution_status == "RUNNING"
    assert store.load_tactical_session(session.tactical_session_id) == session
    with pytest.raises(ProjectError, match="Unsupported TacticalSession"):
        store.bind_tactical_session(
            directive.directive_id,
            execution_run_id="run-2",
            execution_status="OPEN",
        )
