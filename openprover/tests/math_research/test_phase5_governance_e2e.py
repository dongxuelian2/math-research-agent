from __future__ import annotations

import json

import pytest

from openprover.math_research.architecture_critic import evaluate_patch
from openprover.math_research.architecture_patch import (
    ArchitecturePatch,
    PatchObligationAddition,
    ScopeTransfer,
)
from openprover.math_research.architecture_review import (
    ArchitectureDimension,
    ArchitectureDimensionFinding,
    ArchitectureReview,
    GovernanceActor,
)
from openprover.math_research.campaign import CampaignStore
from openprover.math_research.governance import (
    GovernanceController,
    GovernanceThresholds,
)
from openprover.math_research.project import ProjectError, ProjectStore, utc_now
from openprover.math_research.research_map import MapRevisionReason
from openprover.math_research.research_store import (
    ResearchStoreFacade,
    research_checkpoint_projection,
)
from openprover.math_research.structural_effect import StructuralEffect
from openprover.math_research.structural_probe import (
    ProbeBudget,
    StructuralProbe,
    StructuralProbePlan,
)
from openprover.math_research.truth_identity import domain_hash
from openprover.math_research.truth_store import TruthStoreFacade


def _review(research_map, clock):
    findings = tuple(
        ArchitectureDimensionFinding.capture(
            dimension=dimension.value,
            status="CONCERN",
            summary=f"Reviewed {dimension.value} against the current immutable frontier.",
            evidence_refs=("session-evidence",),
        )
        for dimension in ArchitectureDimension
    )
    return ArchitectureReview.capture(
        root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
        research_map_id=research_map.research_map_id,
        research_map_version=research_map.version,
        research_map_hash=research_map.research_map_hash,
        trigger_reasons=clock.trigger_reasons,
        reviewed_partition="O1/O2 duplicate one obstruction while O3 is orthogonal.",
        reviewed_parameterization="One unbounded parameter can be removed.",
        reviewed_obstruction_model="The root obstruction did not move during tactical sessions.",
        reviewed_dependency_architecture="All dependencies remain explicitly represented.",
        reviewed_termination_mechanisms="No termination mechanism is visible in v1.",
        open_obligation_ids=("O1", "O2", "O3"),
        blocked_obligation_ids=(),
        route_failure_refs=(),
        structural_effect_refs=(),
        dimension_findings=findings,
        proposed_actions=("Probe a two-obstruction finite repartition.",),
        evidence_refs=("session-evidence",),
        verdict="STRUCTURAL_PROBE_REQUIRED",
        author=GovernanceActor.capture(
            role="ARCHITECTURE_REVIEWER",
            actor_id="reviewer-e2e",
            provider="mock",
            model="review-model",
            context_hash=domain_hash("phase5_context", {"role": "reviewer"}),
            fresh_context=True,
        ),
        created_at=utc_now(),
        committed_at=utc_now(),
    )


def _patch(research_map, review, probe, *, complete=True):
    additions = tuple(
        PatchObligationAddition.capture(
            obligation_id=obligation_id,
            title=f"Finite obstruction {obligation_id}",
            statement=f"Resolve the finite obstruction {obligation_id}",
            obligation_kind="OBSTRUCTION",
            scope=(f"finite:{obligation_id}",),
        )
        for obligation_id in ("N1", "N2")
    )
    transfers = [
        ScopeTransfer.capture(
            source_obligation_ids=("O1", "O2"),
            target_obligation_ids=("N1",),
            disposition="SUPERSEDED",
            reason="N1 preserves the combined O1/O2 scope.",
            evidence_refs=("probe-evidence",),
        )
    ]
    if complete:
        transfers.append(
            ScopeTransfer.capture(
                source_obligation_ids=("O3",),
                target_obligation_ids=("N2",),
                disposition="SUPERSEDED",
                reason="N2 preserves the orthogonal O3 scope.",
                evidence_refs=("probe-evidence",),
            )
        )
    return ArchitecturePatch.capture(
        source_map_id=research_map.research_map_id,
        source_map_version=research_map.version,
        source_map_hash=research_map.research_map_hash,
        root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
        operation_kinds=("REPLACE_PARTITION", "CHANGE_STRATEGIC_THESIS"),
        affected_obligation_ids=("O1", "O2", "O3"),
        additions=additions,
        scope_transfers=tuple(transfers),
        route_memory_changes=(),
        structural_thesis_change="Reduce the root to two finite obstructions.",
        removed_or_reframed_scope=("three-way unbounded partition",),
        justification="The bounded probe supports the exhaustive finite repartition.",
        review_id=review.review_id,
        review_hash=review.review_hash,
        probe_ids=(probe.probe_id,),
        probe_hashes=(probe.probe_hash,),
        evidence_refs=("session-evidence", "probe-evidence"),
        expected_structural_gain="Infinite-to-finite reduction",
        proposed_by="patcher-e2e",
        created_at=utc_now(),
    )


def _due_tactical_workflow(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "PHASE 5 E2E")
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
                "obligation_kind": "OTHER",
                "scope": [obligation_id],
            }
            for obligation_id in ("O1", "O2", "O3")
        ],
        created_by="e2e",
        strategic_thesis="Use a three-way unbounded partition.",
    )
    controller = GovernanceController(
        project,
        research_store=research,
        thresholds=GovernanceThresholds(tactical_without_structural=2),
    )

    prose = (
        "CURRENT ARCHITECTURE IS DEAD",
        "abandon branch A and replace partition P",
    )
    for index, (obligation_id, content) in enumerate(zip(("O1", "O2"), prose, strict=True)):
        directive = research.create_directive(
            research_map.research_map_id,
            obligation_id,
            tactical_goal=f"Find a local lemma for {obligation_id}",
            allowed_scope=(obligation_id,),
            created_by="planner" if index else "worker-coordinator",
        )
        session = research.bind_tactical_session(
            directive.directive_id,
            execution_run_id=f"run-{index}",
            execution_status="RUNNING",
        )
        artifact = project.root / "runs" / f"run-{index}" / "result.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(content, encoding="utf-8")
        closure = research.close_tactical_session(
            session.tactical_session_id,
            execution_status="COMPLETED",
            raw_artifacts=({"path": artifact, "artifact_kind": "OTHER", "producer": "mock"},),
            evidence_specs=(
                {
                    "artifact_path": artifact,
                    "evidence_kind": "OTHER",
                    "verifier_status": "PASS",
                    "summary": "A local tactical lemma; prose has no governance authority.",
                },
            ),
            closed_by="e2e",
        )
        effect = StructuralEffect.capture(
            root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
            research_map_id=research_map.research_map_id,
            research_map_version=research_map.version,
            research_map_hash=research_map.research_map_hash,
            obligation_refs=(obligation_id,),
            effect_kind="LOCAL_LEMMA_PROVED",
            evidence_refs=(closure.validated_evidence[0].evidence_id,),
            validation_basis="Typed SessionClosure evidence validates only a local lemma.",
            validation_status="VALIDATED",
            source_type="SESSION_CLOSURE",
            created_at=utc_now(),
            created_by="e2e-classifier",
        )
        controller.record_effect(effect)
        controller.record_session(
            research_map.research_map_id,
            root_obstruction_unchanged=True,
            tactical_session_id=session.tactical_session_id,
        )

    return project, truth, research, research_map, controller


def _complete_review_probe(research_map, controller):
    due = controller.load_clock(research_map.research_map_id)
    review = _review(research_map, due)
    reset = controller.commit_review(review)
    plan = StructuralProbePlan.capture(
        review_id=review.review_id,
        review_hash=review.review_hash,
        root_claim_snapshot_hash=review.root_claim_snapshot_hash,
        research_map_id=review.research_map_id,
        source_map_version=review.research_map_version,
        source_map_hash=review.research_map_hash,
        proposed_mechanism="Finite obstruction reduction",
        proposed_partition_change="Replace O1/O2/O3 with N1/N2",
        proposed_parameterization="Remove the unbounded coordinate",
        target_obstruction="Unbounded obstruction family",
        bounded_scope=("two representatives",),
        budget=ProbeBudget(1, 2, 4, 60),
        success_criteria=("Every representative maps to N1 or N2.",),
        failure_criteria=("A representative escapes N1/N2.",),
        evidence_refs=("session-evidence",),
        created_at=utc_now(),
        created_by="reviewer-e2e",
    )
    controller.persist_probe_plan(plan)
    probe = StructuralProbe.capture(
        plan=plan,
        result="SUPPORTS_PATCH",
        evidence_refs=("probe-evidence",),
        result_basis="Every bounded representative satisfied the success criterion.",
        closed_at=utc_now(),
        closed_by="probe-runner",
    )
    controller.close_probe(probe)
    return review, reset, probe


def _critic(patch, review, research_map, probe):
    return evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=GovernanceActor.capture(
            role="ARCHITECTURE_CRITIC",
            actor_id="critic-e2e",
            provider="mock",
            model="critic-model",
            context_hash=domain_hash("phase5_context", {"role": "critic"}),
            fresh_context=True,
        ),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )


def test_production_governance_e2e(tmp_path):
    project, _, research, v1, controller = _due_tactical_workflow(tmp_path)
    theorem_before = project.load_theorem("T1")
    due = controller.load_clock(v1.research_map_id)
    assert due.review_due is True
    assert "TACTICAL_WITHOUT_STRUCTURAL_PROGRESS" in due.trigger_reasons
    assert due.tactical_progress_since_last_review == 2
    assert due.structural_progress_since_last_review == 0
    assert research.load_current_map(v1.research_map_id) == v1

    with pytest.raises(ProjectError, match="DESTRUCTIVE_REFRAME_REQUIRES_GOVERNANCE"):
        research.revise_map(
            v1,
            created_by="planner-prose",
            revision_reason=MapRevisionReason.HUMAN_STEERING.value,
            removed_or_reframed_scope=("branch A",),
        )

    restored = GovernanceController(project, research_store=research)
    assert restored.ensure_clock(v1.research_map_id).clock_hash == due.clock_hash
    review, reset, probe = _complete_review_probe(v1, restored)
    assert reset.review_due is False
    assert reset.sessions_since_last_review == 0
    assert reset.tactical_progress_since_last_review == 0

    patch = _patch(v1, review, probe)
    restored.persist_patch(patch)
    assert research.load_current_map(v1.research_map_id) == v1
    critic = _critic(patch, review, v1, probe)
    restored.persist_critic(critic)
    authorization = restored.authorize_patch(patch.patch_id, critic.critic_id)
    assert authorization.status == "AUTHORIZED"
    assert research.load_current_map(v1.research_map_id) == v1

    v2, application = restored.apply_authorized_patch(
        authorization.authorization_id,
        applied_by="governance-e2e",
    )
    assert v2.version == 2
    assert research.load_map(v1.research_map_hash) == v1
    assert {item.obligation_id for item in v2.obligation_refs} == {
        "O1",
        "O2",
        "O3",
        "N1",
        "N2",
    }
    assert all(v2.obligation_ref(item).disposition == "SUPERSEDED" for item in ("O1", "O2", "O3"))
    assert all(v2.obligation_ref(item).disposition == "OPEN" for item in ("N1", "N2"))
    assert restored.load_application(application.application_id) == application
    assert project.load_theorem("T1") == theorem_before


def test_negative_scope_loss_governance_e2e(tmp_path):
    _, _, research, v1, controller = _due_tactical_workflow(tmp_path)
    review, _, probe = _complete_review_probe(v1, controller)
    patch = _patch(v1, review, probe, complete=False)
    controller.persist_patch(patch)
    critic = _critic(patch, review, v1, probe)
    assert critic.verdict == "SCOPE_LOSS"
    controller.persist_critic(critic)
    authorization = controller.authorize_patch(patch.patch_id, critic.critic_id)

    assert authorization.status == "REJECTED"
    assert authorization.scope_validation_passed is False
    assert research.load_current_map(v1.research_map_id) == v1
    assert {item.obligation_id for item in v1.obligation_refs} == {"O1", "O2", "O3"}


def test_g22_campaign_checkpoint_resume_preserves_due_state(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Campaign governance")
    project.add_theorem("T1", "Root", "P holds.")
    campaigns = CampaignStore(project)
    record = campaigns.create("campaign-T1", target_id="T1")
    research = ResearchStoreFacade(project)
    governance = GovernanceController(project, research_store=research)
    due = governance.signal_review(record["research_map_id"], "HUMAN_REQUEST")
    frontier = research_checkpoint_projection(research, record["research_map_id"])
    frontier.update(governance.checkpoint_projection(record["research_map_id"]))
    frontier["governance_checkpoint_classification"] = "DIRECT_IMPORT"
    campaigns.update_runtime_state("campaign-T1", research_frontier=frontier)
    campaigns.checkpoint("campaign-T1", "STOPPED_AT_CHECKPOINT")

    resumed = campaigns.resume("campaign-T1")
    assert resumed["architecture_review_due"] is True
    assert resumed["architecture_review_clock_hash"] == due.clock_hash
    assert resumed["governance_checkpoint_classification"] == "DIRECT_IMPORT"


def test_g23_legacy_checkpoint_requires_review_without_fabricating_one(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Legacy governance")
    project.add_theorem("T1", "Root", "P holds.")
    campaigns = CampaignStore(project)
    campaigns.create("campaign-T1", target_id="T1")
    path = campaigns.path("campaign-T1")
    legacy = json.loads(path.read_text(encoding="utf-8"))
    for key in tuple(legacy):
        if key.startswith("architecture_review_") or key in {
            "last_architecture_review_id",
            "last_architecture_review_hash",
            "active_structural_probe_id",
            "pending_architecture_patch_id",
            "governance_checkpoint_classification",
        }:
            legacy.pop(key)
    path.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")

    resumed = campaigns.resume("campaign-T1")
    reviews = project.root / "research" / "governance" / "architecture_reviews"
    assert resumed["governance_checkpoint_classification"] == "GOVERNANCE_REVIEW_REQUIRED"
    assert resumed["architecture_review_due"] is True
    assert "HUMAN_REQUEST" in resumed["architecture_review_triggers"]
    assert resumed["last_architecture_review_id"] is None
    assert not reviews.exists() or not tuple(reviews.glob("*.json"))
