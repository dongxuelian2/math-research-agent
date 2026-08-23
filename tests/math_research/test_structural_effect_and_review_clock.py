from __future__ import annotations

from math_research_agent.research.governance import (
    ArchitectureReviewTrigger,
    GovernanceController,
    GovernanceThresholds,
)
from math_research_agent.research.project import ProjectStore, utc_now
from math_research_agent.research.research_store import ResearchStoreFacade
from math_research_agent.research.structural_effect import (
    StructuralEffect,
    classify_structural_effect,
)
from math_research_agent.research.truth_store import TruthStoreFacade


def _governance(tmp_path, *, thresholds=None):
    project = ProjectStore.initialize(tmp_path / "project", "Governance")
    project.add_theorem("T1", "Root theorem", "For every n, P(n).")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    research = ResearchStoreFacade(project, truth_store=truth)
    research_map = research.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": "O1",
                "title": "Root obstruction",
                "statement": "Resolve the root obstruction",
                "obligation_kind": "OBSTRUCTION",
                "scope": ["root"],
            },
            {
                "obligation_id": "O2",
                "title": "Boundary family",
                "statement": "Classify the boundary family",
                "obligation_kind": "BOUNDARY",
                "scope": ["boundary"],
            },
        ],
        created_by="test",
        strategic_thesis="Separate the root obstruction and boundary family.",
    )
    controller = GovernanceController(
        project,
        research_store=research,
        thresholds=thresholds or GovernanceThresholds(),
    )
    return project, research, research_map, controller


def _effect(research_map, *, kind, status="VALIDATED", evidence=("evidence-1",)):
    return StructuralEffect.capture(
        root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
        research_map_id=research_map.research_map_id,
        research_map_version=research_map.version,
        research_map_hash=research_map.research_map_hash,
        obligation_refs=("O1",),
        effect_kind=kind,
        evidence_refs=evidence,
        validation_basis="deterministic test validation",
        validation_status=status,
        source_type="SESSION_CLOSURE",
        created_at=utc_now(),
        created_by="test",
    )


def test_g1_activity_tactical_and_structural_are_distinct_and_evidence_bound(tmp_path):
    _, _, research_map, controller = _governance(tmp_path)
    assert classify_structural_effect("WORKER_SPAWNED") == "ACTIVITY"
    assert classify_structural_effect("LOCAL_LEMMA_PROVED") == "TACTICAL_PROGRESS"
    assert classify_structural_effect("INFINITE_TO_FINITE_REDUCTION") == "STRUCTURAL_PROGRESS"
    assert classify_structural_effect("PARAMETERIZATION_SIMPLIFIED") == "STRUCTURAL_PROGRESS"

    activity = controller.record_effect(_effect(research_map, kind="WORKER_SPAWNED"))
    assert activity.tactical_progress_since_last_review == 0
    assert activity.structural_progress_since_last_review == 0

    tactical = controller.record_effect(_effect(research_map, kind="LOCAL_LEMMA_PROVED"))
    assert tactical.tactical_progress_since_last_review == 1
    assert tactical.structural_progress_since_last_review == 0

    structural = controller.record_effect(
        _effect(research_map, kind="INFINITE_TO_FINITE_REDUCTION")
    )
    assert structural.tactical_progress_since_last_review == 1
    assert structural.structural_progress_since_last_review == 1

    unvalidated = controller.record_effect(
        _effect(
            research_map,
            kind="GLOBAL_INVARIANT_FOUND",
            status="UNVALIDATED_CLAIM",
            evidence=(),
        )
    )
    assert unvalidated.structural_progress_since_last_review == 1


def test_g2_g3_local_success_and_obligation_resolution_do_not_reset_clock(tmp_path):
    thresholds = GovernanceThresholds(mandatory_interval_sessions=2)
    _, _, research_map, controller = _governance(tmp_path, thresholds=thresholds)
    controller.record_session(research_map.research_map_id)
    controller.record_effect(_effect(research_map, kind="LOCAL_LEMMA_PROVED"))
    due = controller.record_session(research_map.research_map_id)
    assert due.review_due is True
    assert ArchitectureReviewTrigger.MANDATORY_INTERVAL.value in due.trigger_reasons

    after_resolution = controller.record_effect(
        _effect(research_map, kind="ONE_OBLIGATION_RESOLVED")
    )
    assert after_resolution.review_due is True
    assert after_resolution.sessions_since_last_review == 2
    assert after_resolution.tactical_progress_since_last_review == 2


def test_g5_repeated_route_failures_trigger_review_without_auto_patch(tmp_path):
    thresholds = GovernanceThresholds(repeated_route_failures=3)
    _, research, research_map, controller = _governance(tmp_path, thresholds=thresholds)
    controller.record_route_failure(research_map.research_map_id, obligation_id="O1")
    controller.record_route_failure(research_map.research_map_id, obligation_id="O1")
    due = controller.record_route_failure(research_map.research_map_id, obligation_id="O1")
    assert ArchitectureReviewTrigger.REPEATED_ROUTE_FAILURE.value in due.trigger_reasons
    assert research.load_current_map(research_map.research_map_id) == research_map


def test_g6_long_blocked_obligation_uses_logical_session_age(tmp_path):
    thresholds = GovernanceThresholds(long_blocked_sessions=2)
    _, _, research_map, controller = _governance(tmp_path, thresholds=thresholds)
    controller.record_session(research_map.research_map_id, blocked_obligation_ids=("O2",))
    due = controller.record_session(research_map.research_map_id, blocked_obligation_ids=("O2",))
    assert ArchitectureReviewTrigger.LONG_BLOCKED_OBLIGATION.value in due.trigger_reasons
    assert dict(due.blocked_obligation_ages) == {"O2": 2}


def test_g7_tactical_without_structural_progress_triggers_review(tmp_path):
    thresholds = GovernanceThresholds(tactical_without_structural=2)
    _, _, research_map, controller = _governance(tmp_path, thresholds=thresholds)
    controller.record_effect(_effect(research_map, kind="LOCAL_LEMMA_PROVED"))
    due = controller.record_effect(_effect(research_map, kind="PARAMETER_RANGE_REDUCED"))
    assert (
        ArchitectureReviewTrigger.TACTICAL_WITHOUT_STRUCTURAL_PROGRESS.value in due.trigger_reasons
    )


def test_g21_successor_execution_increments_evidence_and_never_resets_clock(tmp_path):
    thresholds = GovernanceThresholds(
        mandatory_interval_sessions=2,
        candidate_repair_cycles=2,
    )
    _, _, research_map, controller = _governance(tmp_path, thresholds=thresholds)
    controller.record_session(research_map.research_map_id, successor_execution=True)
    due = controller.record_session(research_map.research_map_id, successor_execution=True)
    assert due.review_due is True
    assert due.sessions_since_last_review == 2
    assert due.candidate_repair_cycles_since_last_review == 2
    assert ArchitectureReviewTrigger.ROOT_OBSTRUCTION_STALLED.value in due.trigger_reasons
