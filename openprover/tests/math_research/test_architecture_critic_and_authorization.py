from __future__ import annotations

from openprover.math_research.architecture_critic import (
    ArchitectureCritic,
    evaluate_patch,
)
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
from openprover.math_research.governance import GovernanceController
from openprover.math_research.project import ProjectStore, utc_now
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.structural_probe import (
    ProbeBudget,
    StructuralProbe,
    StructuralProbePlan,
)
from openprover.math_research.truth_identity import domain_hash
from openprover.math_research.truth_store import TruthStoreFacade


def _workflow(tmp_path, *, probe_result="SUPPORTS_PATCH"):
    project = ProjectStore.initialize(tmp_path / "project", "Critic")
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
                "obligation_id": item,
                "title": item,
                "statement": f"Resolve {item}",
                "obligation_kind": "OTHER",
                "scope": [item],
            }
            for item in ("O1", "O2")
        ],
        created_by="test",
        strategic_thesis="Old two-branch partition",
    )
    controller = GovernanceController(project, research_store=research)
    controller.signal_review(research_map.research_map_id, "HUMAN_REQUEST")
    findings = tuple(
        ArchitectureDimensionFinding.capture(
            dimension=item.value,
            status="CONCERN",
            summary=f"Checked {item.value}",
            evidence_refs=("governance-evidence",),
        )
        for item in ArchitectureDimension
    )
    review = ArchitectureReview.capture(
        root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
        research_map_id=research_map.research_map_id,
        research_map_version=research_map.version,
        research_map_hash=research_map.research_map_hash,
        trigger_reasons=("HUMAN_REQUEST",),
        reviewed_partition="Two branches duplicate one obstruction.",
        reviewed_parameterization="One coordinate appears redundant.",
        reviewed_obstruction_model="The obstruction is unchanged.",
        reviewed_dependency_architecture="Dependencies are current.",
        reviewed_termination_mechanisms="No termination mechanism is visible.",
        open_obligation_ids=("O1", "O2"),
        blocked_obligation_ids=(),
        route_failure_refs=(),
        structural_effect_refs=(),
        dimension_findings=findings,
        proposed_actions=("Probe a finite-obstruction repartition.",),
        evidence_refs=("governance-evidence",),
        verdict="STRUCTURAL_PROBE_REQUIRED",
        author=GovernanceActor.capture(
            role="ARCHITECTURE_REVIEWER",
            actor_id="reviewer-1",
            provider="mock",
            model="governance-model",
            context_hash=domain_hash("test_context", {"role": "review"}),
            fresh_context=True,
        ),
        created_at=utc_now(),
        committed_at=utc_now(),
    )
    controller.commit_review(review)
    plan = StructuralProbePlan.capture(
        review_id=review.review_id,
        review_hash=review.review_hash,
        root_claim_snapshot_hash=review.root_claim_snapshot_hash,
        research_map_id=review.research_map_id,
        source_map_version=review.research_map_version,
        source_map_hash=review.research_map_hash,
        proposed_mechanism="Finite obstruction reduction",
        proposed_partition_change="O1/O2 become N1/N2",
        proposed_parameterization="Remove redundant coordinate",
        target_obstruction="Unbounded family",
        bounded_scope=("two representative cases",),
        budget=ProbeBudget(1, 2, 4, 60),
        success_criteria=("Every representative maps to N1 or N2.",),
        failure_criteria=("A representative escapes N1/N2.",),
        evidence_refs=("governance-evidence",),
        created_at=utc_now(),
        created_by="reviewer-1",
    )
    controller.persist_probe_plan(plan)
    probe = StructuralProbe.capture(
        plan=plan,
        result=probe_result,
        evidence_refs=("probe-evidence",),
        result_basis="Bounded success/failure criteria evaluated.",
        closed_at=utc_now(),
        closed_by="probe-runner",
    )
    controller.close_probe(probe)
    return project, truth, research, research_map, controller, review, probe


def _patch(research_map, review, probe, *, complete=True):
    additions = (
        PatchObligationAddition.capture(
            obligation_id="N1",
            title="Finite obstruction one",
            statement="Resolve finite obstruction one",
            obligation_kind="OBSTRUCTION",
            scope=("finite-1",),
        ),
        PatchObligationAddition.capture(
            obligation_id="N2",
            title="Finite obstruction two",
            statement="Resolve finite obstruction two",
            obligation_kind="OBSTRUCTION",
            scope=("finite-2",),
        ),
    )
    transfers = [
        ScopeTransfer.capture(
            source_obligation_ids=("O1",),
            target_obligation_ids=("N1",),
            disposition="SUPERSEDED",
            reason="N1 preserves O1 scope under the finite partition.",
            evidence_refs=("probe-evidence",),
        )
    ]
    if complete:
        transfers.append(
            ScopeTransfer.capture(
                source_obligation_ids=("O2",),
                target_obligation_ids=("N2",),
                disposition="SUPERSEDED",
                reason="N2 preserves O2 scope under the finite partition.",
                evidence_refs=("probe-evidence",),
            )
        )
    return ArchitecturePatch.capture(
        source_map_id=research_map.research_map_id,
        source_map_version=research_map.version,
        source_map_hash=research_map.research_map_hash,
        root_claim_snapshot_hash=research_map.root_claim_snapshot_hash,
        operation_kinds=("REPLACE_PARTITION", "CHANGE_STRATEGIC_THESIS"),
        affected_obligation_ids=("O1", "O2"),
        additions=additions,
        scope_transfers=tuple(transfers),
        route_memory_changes=(),
        structural_thesis_change="Reduce the root to two finite obstructions.",
        removed_or_reframed_scope=("old two-branch partition",),
        justification="The bounded probe supports an exhaustive finite repartition.",
        review_id=review.review_id,
        review_hash=review.review_hash,
        probe_ids=(probe.probe_id,),
        probe_hashes=(probe.probe_hash,),
        evidence_refs=("governance-evidence", "probe-evidence"),
        expected_structural_gain="Infinite-to-finite reduction",
        proposed_by="patcher-1",
        created_at=utc_now(),
    )


def _critic_actor(*, model="governance-critic-model"):
    return GovernanceActor.capture(
        role="ARCHITECTURE_CRITIC",
        actor_id="critic-1",
        provider="mock",
        model=model,
        context_hash=domain_hash("test_context", {"role": "critic"}),
        fresh_context=True,
    )


def test_critic_independence_is_durable_and_approval_cannot_mutate_map(tmp_path):
    _, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    patch = _patch(research_map, review, probe)
    controller.persist_patch(patch)
    critic = evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )
    assert critic.verdict == "APPROVE"
    assert critic.independence_receipt.policy_satisfied is True
    assert critic.independence_receipt.same_provider is True
    assert critic.independence_receipt.same_model is False
    controller.persist_critic(critic)
    assert controller.load_critic(critic.critic_id) == critic
    assert research.load_current_map(research_map.research_map_id) == research_map


def test_same_model_critic_fallback_is_not_independent(tmp_path):
    _, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    patch = _patch(research_map, review, probe)
    controller.persist_patch(patch)
    critic = evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(model="governance-model"),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )
    assert critic.independence_receipt.same_model is True
    assert critic.independence_receipt.policy_satisfied is False
    assert critic.verdict != "APPROVE"
    controller.persist_critic(critic)
    authorization = controller.authorize_patch(patch.patch_id, critic.critic_id)
    assert authorization.status != "AUTHORIZED"


def test_g13_critic_rejection_blocks_authorization(tmp_path):
    _, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    patch = _patch(research_map, review, probe)
    controller.persist_patch(patch)
    critic = ArchitectureCritic.capture(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(),
        verdict="REJECT",
        reasons=("The probe does not justify the proposed global thesis.",),
        evidence_refs=("critic-evidence",),
        route_failure_refs=(),
        created_at=utc_now(),
    )
    controller.persist_critic(critic)
    authorization = controller.authorize_patch(patch.patch_id, critic.critic_id)
    assert authorization.status == "REJECTED"
    assert research.load_current_map(research_map.research_map_id) == research_map


def test_g14_negative_scope_loss_is_found_by_critic_and_authorization(tmp_path):
    _, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    patch = _patch(research_map, review, probe, complete=False)
    assert patch.scope_transfer_complete is False
    controller.persist_patch(patch)
    critic = evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )
    assert critic.verdict == "SCOPE_LOSS"
    controller.persist_critic(critic)
    authorization = controller.authorize_patch(patch.patch_id, critic.critic_id)
    assert authorization.status == "REJECTED"
    assert authorization.scope_validation_passed is False
    assert research.load_current_map(research_map.research_map_id) == research_map


def test_g17_root_change_makes_authorization_stale(tmp_path):
    project, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    patch = _patch(research_map, review, probe)
    controller.persist_patch(patch)
    critic = evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )
    controller.persist_critic(critic)
    theorem = project.load_theorem("T1")
    theorem["statement"] = "For every n, Q(n)."
    project.update_theorem(theorem)
    authorization = controller.authorize_patch(patch.patch_id, critic.critic_id)
    assert authorization.status == "STALE"
    assert research.load_current_map(research_map.research_map_id) == research_map


def test_g18_invalidated_evidence_requires_revalidation(tmp_path):
    _, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    patch = _patch(research_map, review, probe)
    controller.persist_patch(patch)
    critic = evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )
    controller.persist_critic(critic)
    authorization = controller.authorize_patch(
        patch.patch_id,
        critic.critic_id,
        invalidated_evidence_refs=("probe-evidence",),
    )
    assert authorization.status == "REVALIDATION_REQUIRED"
    assert authorization.invalidated_evidence_refs == ("probe-evidence",)
    assert research.load_current_map(research_map.research_map_id) == research_map


def test_g20_critic_cannot_resolve_obligation(tmp_path):
    _, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    patch = _patch(research_map, review, probe)
    controller.persist_patch(patch)
    critic = evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )
    controller.persist_critic(critic)
    current = research.load_current_map(research_map.research_map_id)
    assert current.obligation_ref("O1").disposition == "OPEN"
    assert current.obligation_ref("O2").disposition == "OPEN"


def test_g15_g16_authorized_reframe_creates_one_version_and_preserves_history(tmp_path):
    project, _, research, research_map, controller, review, probe = _workflow(tmp_path)
    theorem_before = project.load_theorem("T1")
    patch = _patch(research_map, review, probe)
    controller.persist_patch(patch)
    critic = evaluate_patch(
        patch=patch,
        review=review,
        current_map=research_map,
        probes=(probe,),
        critic_actor=_critic_actor(),
        evidence_refs=("critic-evidence",),
        created_at=utc_now(),
    )
    controller.persist_critic(critic)
    authorization = controller.authorize_patch(patch.patch_id, critic.critic_id)

    assert authorization.status == "AUTHORIZED"
    assert research.load_current_map(research_map.research_map_id) == research_map
    target, application = controller.apply_authorized_patch(
        authorization.authorization_id,
        applied_by="governance-applier",
    )

    assert target.version == 2
    assert target.parent_version_ref == research_map.research_map_hash
    assert research.load_map(research_map.research_map_hash) == research_map
    assert research.load_current_map(research_map.research_map_id) == target
    assert {item.obligation_id for item in target.obligation_refs} == {
        "O1",
        "O2",
        "N1",
        "N2",
    }
    assert target.obligation_ref("O1").disposition == "SUPERSEDED"
    assert target.obligation_ref("O2").disposition == "SUPERSEDED"
    assert target.obligation_ref("N1").disposition == "OPEN"
    assert target.obligation_ref("N2").disposition == "OPEN"
    assert controller.load_application(application.application_id) == application
    assert project.load_theorem("T1") == theorem_before
