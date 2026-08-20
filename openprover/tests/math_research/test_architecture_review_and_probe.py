from __future__ import annotations

import pytest

from openprover.math_research.architecture_review import (
    ArchitectureDimension,
    ArchitectureDimensionFinding,
    ArchitectureReview,
    GovernanceActor,
)
from openprover.math_research.governance import GovernanceController
from openprover.math_research.project import ProjectError, ProjectStore, utc_now
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.runtime_artifacts import RuntimeArtifactStore
from openprover.math_research.runtime_backend import SQLiteRuntimeBackend
from openprover.math_research.runtime_effects import RuntimeEffectCoordinator
from openprover.math_research.runtime_model import AttemptState
from openprover.math_research.structural_probe import (
    ProbeBudget,
    StructuralProbe,
    StructuralProbePlan,
)
from openprover.math_research.truth_store import TruthStoreFacade


def _setup(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Architecture Governance")
    project.add_theorem("T1", "Root", "For every n, P(n).")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    research = ResearchStoreFacade(project, truth_store=truth)
    research_map = research.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": obligation_id,
                "title": obligation_id,
                "statement": f"Resolve {obligation_id}",
                "obligation_kind": "STRUCTURAL",
                "scope": [obligation_id],
            }
            for obligation_id in ("O1", "O2")
        ],
        created_by="test",
        strategic_thesis="Old partition",
    )
    controller = GovernanceController(project, research_store=research)
    return project, truth, research, research_map, controller


def _review(research_map, *, verdict="STRUCTURAL_PROBE_REQUIRED"):
    findings = [
        ArchitectureDimensionFinding.capture(
            dimension=dimension.value,
            status=("CRITICAL" if dimension is ArchitectureDimension.SCOPE_COVERAGE else "CONCERN"),
            summary=f"Reviewed {dimension.value}",
            evidence_refs=("review-evidence",),
        )
        for dimension in ArchitectureDimension
    ]
    now = utc_now()
    return ArchitectureReview.capture(
        root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
        research_map_id=research_map.research_map_id,
        research_map_version=research_map.version,
        research_map_hash=research_map.research_map_hash,
        trigger_reasons=("HUMAN_REQUEST",),
        reviewed_partition="The old partition duplicates the obstruction.",
        reviewed_parameterization="One parameter is redundant.",
        reviewed_obstruction_model="The root obstruction has not moved.",
        reviewed_dependency_architecture="Dependencies remain explicit.",
        reviewed_termination_mechanisms="No termination mechanism is visible.",
        open_obligation_ids=("O1", "O2"),
        blocked_obligation_ids=(),
        route_failure_refs=(),
        structural_effect_refs=(),
        dimension_findings=findings,
        proposed_actions=("Run a bounded finite-reduction probe.",),
        evidence_refs=("review-evidence",),
        verdict=verdict,
        author=GovernanceActor.capture(
            role="ARCHITECTURE_REVIEWER",
            actor_id="reviewer-1",
            provider="mock",
            model="review-model",
            fresh_context=True,
        ),
        created_at=now,
        committed_at=now,
    )


def _plan(review):
    return StructuralProbePlan.capture(
        review_id=review.review_id,
        review_hash=review.review_hash,
        root_claim_snapshot_hash=review.root_claim_snapshot_hash,
        research_map_id=review.research_map_id,
        source_map_version=review.research_map_version,
        source_map_hash=review.research_map_hash,
        proposed_mechanism="Collapse the infinite family to two finite obstructions.",
        proposed_partition_change="Replace O1/O2 by a finite obstruction family.",
        proposed_parameterization="Remove one independent parameter.",
        target_obstruction="Unbounded parameter family",
        bounded_scope=("two representative boundary cases",),
        budget=ProbeBudget(
            max_sessions=1,
            max_workers=2,
            max_provider_calls=4,
            wall_clock_seconds=60,
        ),
        success_criteria=("Both representatives reduce to the same finite obstruction.",),
        failure_criteria=("A representative requires the removed parameter.",),
        evidence_refs=("review-evidence",),
        created_at=utc_now(),
        created_by="ArchitectureReviewer",
    )


def test_g4_formal_review_commit_is_the_only_clock_reset(tmp_path):
    project, _, _, research_map, controller = _setup(tmp_path)
    due = controller.signal_review(research_map.research_map_id, "HUMAN_REQUEST")
    assert due.review_due is True
    review = _review(research_map)
    runtime = SQLiteRuntimeBackend(project.root)
    job = runtime.create_logical_job(
        job_kind="ARCHITECTURE_REVIEW",
        semantic_target=research_map.research_map_id,
        idempotency_key="governance-runtime-e2e",
        claim_snapshot_hash=research_map.root_claim_snapshot_hash,
        governance_ref=due.clock_hash,
    )
    attempt, _ = runtime.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash="sha256:architecture-review",
        dispatch_kind="PROVIDER_INVOCATION",
    )
    lease = runtime.claim_attempt(attempt["attempt_id"], owner="test", ttl_seconds=60)
    runtime.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="test",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    artifact = RuntimeArtifactStore(project.root).persist_and_register(
        runtime,
        review.to_dict(),
        artifact_kind="ARCHITECTURE_REVIEW_RESULT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = runtime.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    runtime.accept_result(job["logical_job_id"])
    coordinator = RuntimeEffectCoordinator(runtime)
    slot, outcome = coordinator.commit_architecture_review(
        logical_job_id=job["logical_job_id"],
        source_result_id=result["result_id"],
        governance_controller=controller,
        review=review,
    )
    reset = outcome["clock"]
    assert reset.review_due is False
    assert reset.sessions_since_last_review == 0
    assert reset.last_review_id == review.review_id
    assert controller.load_review(review.review_id) == review
    replay_reset = controller.commit_review(review)
    assert replay_reset.clock_hash == reset.clock_hash
    assert replay_reset.revision == reset.revision
    replay_slot, _ = coordinator.commit_architecture_review(
        logical_job_id=job["logical_job_id"],
        source_result_id=result["result_id"],
        governance_controller=controller,
        review=review,
    )
    assert replay_slot["effect_slot_id"] == slot["effect_slot_id"]
    assert len(runtime.list_rows("effect_slots")) == 1


def test_g10_review_is_strict_and_cannot_mutate_truth(tmp_path):
    project, _, _, research_map, controller = _setup(tmp_path)
    controller.signal_review(research_map.research_map_id, "HUMAN_REQUEST")
    review = _review(research_map)
    before = project.load_theorem("T1")
    controller.commit_review(review)
    after = project.load_theorem("T1")
    assert before == after

    invalid = review.to_dict()
    invalid["theorem_status"] = "PROVED"
    with pytest.raises(ProjectError, match="unknown=.*theorem_status"):
        ArchitectureReview.from_dict(invalid)


def test_structural_probe_has_a_hard_budget_and_explicit_criteria(tmp_path):
    _, _, _, research_map, controller = _setup(tmp_path)
    controller.signal_review(research_map.research_map_id, "HUMAN_REQUEST")
    review = _review(research_map)
    controller.commit_review(review)
    plan = _plan(review)
    controller.persist_probe_plan(plan)
    assert controller.load_probe_plan(plan.probe_id) == plan
    projection = controller.checkpoint_projection(research_map.research_map_id)
    assert projection["active_structural_probe_id"] == plan.probe_id

    with pytest.raises(ProjectError, match="positive integer"):
        ProbeBudget(max_sessions=0, max_workers=1, max_provider_calls=1, wall_clock_seconds=1)


@pytest.mark.parametrize("result", ["REJECTS_PATCH", "SUPPORTS_PATCH"])
def test_g11_g12_g19_probe_result_never_mutates_map_or_truth(tmp_path, result):
    project, _, research, research_map, controller = _setup(tmp_path)
    controller.signal_review(research_map.research_map_id, "HUMAN_REQUEST")
    review = _review(research_map)
    controller.commit_review(review)
    plan = _plan(review)
    controller.persist_probe_plan(plan)
    before_truth = project.load_theorem("T1")
    probe = StructuralProbe.capture(
        plan=plan,
        result=result,
        evidence_refs=(f"probe-evidence-{result}",),
        result_basis="Bounded criteria were evaluated exactly.",
        closed_at=utc_now(),
        closed_by="StructuralProbeRunner",
    )
    controller.close_probe(probe)
    assert controller.load_probe(probe.probe_id) == probe
    assert research.load_current_map(research_map.research_map_id) == research_map
    assert project.load_theorem("T1") == before_truth
    projection = controller.checkpoint_projection(research_map.research_map_id)
    assert projection["active_structural_probe_id"] is None
