"""Focused regressions for the v3.2 pre-root blocker repairs."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from openprover.math_research.architecture_patch import (
    PatchAuthorization,
    ScopeTransfer,
)
from openprover.math_research.research_evidence import EvidenceProjection
from openprover.math_research.research_map import MapRevisionReason
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.runtime_artifacts import RuntimeArtifactStore
from openprover.math_research.runtime_backend import SQLiteRuntimeBackend
from openprover.math_research.runtime_bindings import CrossPlaneExecutionBinding
from openprover.math_research.runtime_effects import RuntimeEffectCoordinator
from openprover.math_research.runtime_model import (
    AttemptState,
    FaultInjected,
    FaultInjector,
    FaultPoint,
    RuntimeConflict,
)
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.truth_identity import domain_hash
from openprover.math_research.truth_store import TruthStoreFacade
from openprover.math_research.project import ProjectStore


def _session(tmp_path: Path, *, include_second_obligation: bool = False):
    project = ProjectStore.initialize(tmp_path / "project", "pre-root repair")
    project.add_theorem("T1", "Root", "P holds for all n.")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    store = ResearchStoreFacade(project, truth_store=truth)
    ids = ("O1", "O2") if include_second_obligation else ("O1",)
    research_map = store.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": item,
                "title": item,
                "statement": f"Resolve {item}",
                "obligation_kind": "LEMMA",
                "scope": [item],
            }
            for item in ids
        ],
        created_by="test",
    )
    directive = store.create_directive(
        research_map.research_map_id,
        "O1",
        tactical_goal="Resolve O1",
        allowed_scope=("O1",),
        created_by="test",
    )
    session = store.bind_tactical_session(
        directive.directive_id,
        execution_run_id="run-1",
        execution_status="RUNNING",
    )
    artifacts = project.root / "runs" / "run-1"
    artifacts.mkdir(parents=True)
    return project, truth, store, snapshot, research_map, directive, session, artifacts


def _valid_closure(store, session, artifacts):
    candidate = artifacts / "candidate.md"
    verifier = artifacts / "verifier.json"
    audit = artifacts / "audit.json"
    for path, value in (
        (candidate, "candidate"),
        (verifier, '{"verdict":"CORRECT"}'),
        (audit, '{"outcome":"PASS"}'),
    ):
        path.write_text(value, encoding="utf-8")
    return store.close_tactical_session(
        session.tactical_session_id,
        execution_status="COMPLETED",
        raw_artifacts=tuple(
            {"path": path, "artifact_kind": kind, "producer": "test"}
            for path, kind in (
                (candidate, "CANDIDATE"),
                (verifier, "VERIFIER"),
                (audit, "AUDIT"),
            )
        ),
        evidence_specs=(
            {
                "artifact_path": candidate,
                "evidence_kind": "CANDIDATE",
                "verifier_status": "NOT_APPLICABLE",
                "audit_status": "NOT_APPLICABLE",
                "authority_status": "NOT_APPLICABLE",
            },
            {
                "artifact_path": verifier,
                "evidence_kind": "VERIFIER",
                "verifier_status": "PASS",
                "audit_status": "NOT_APPLICABLE",
                "authority_status": "NOT_APPLICABLE",
            },
            {
                "artifact_path": audit,
                "evidence_kind": "AUDIT",
                "verifier_status": "NOT_APPLICABLE",
                "audit_status": "PASS",
                "authority_status": "TRUSTED",
            },
        ),
        closed_by="test",
    )


def test_f001_stale_closure_isolated_and_explicit_transfer_revalidates(tmp_path):
    project, _, store, snapshot, v1, _, session, artifacts = _session(
        tmp_path, include_second_obligation=True
    )
    closure = _valid_closure(store, session, artifacts)
    _, v2 = store.add_obligation(
        v1.research_map_id,
        obligation_id="O3",
        title="O3",
        statement="Resolve O3",
        obligation_kind="LEMMA",
        created_by="test",
        scope=("O3",),
    )
    # The old O1 closure is now stale and O1 is explicitly transferred to O2.
    _, v3 = store.record_disposition(
        v2.research_map_id,
        "O1",
        disposition="SUPERSEDED",
        superseded_by=("O2",),
        reason="Governed transfer O1 -> O2",
        recorded_by="test",
        revision_reason=MapRevisionReason.OBLIGATION_RESOLVED.value,
    )
    assert store.evaluate_session_closure(session.tactical_session_id).status == (
        "STALE_SESSION_CLOSURE"
    )
    before = store.load_current_map(v3.research_map_id)
    decision, revised = store.resolve_session_closure(
        session.tactical_session_id, recorded_by="test"
    )
    assert decision.status == "STALE_SESSION_CLOSURE"
    assert revised is None
    assert store.load_current_map(v3.research_map_id).version == before.version
    assert store.load_current_map(v3.research_map_id).obligation_ref("O1").disposition == (
        "SUPERSEDED"
    )
    assert store.load_current_map(v3.research_map_id).obligation_ref("O2").disposition == (
        "OPEN"
    )

    transfer = ScopeTransfer.capture(
        source_obligation_ids=("O1",),
        target_obligation_ids=("O2",),
        disposition="SUPERSEDED",
        reason="Explicit current-scope transfer",
        evidence_refs=("transfer-evidence",),
    )
    authorization = PatchAuthorization.capture(
        patch_id="patch-transfer",
        patch_hash=domain_hash("patch", {"id": "patch-transfer"}),
        review_id="review-transfer",
        review_hash=domain_hash("review", {"id": "review-transfer"}),
        critic_id="critic-transfer",
        critic_hash=domain_hash("critic", {"id": "critic-transfer"}),
        probe_ids=(),
        probe_hashes=(),
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        source_map_hash=v1.research_map_hash,
        status="AUTHORIZED",
        scope_validation_passed=True,
        truth_boundary_intact=True,
        invalidated_evidence_refs=(),
        reason="authorized transfer",
        authorized_by="test",
        created_at="2026-01-01T00:00:00+00:00",
    )
    current_o2 = store.load_current_map(v3.research_map_id).obligation_ref("O2")
    revalidated = tuple(
        EvidenceProjection.capture(
            evidence_kind=item.evidence_kind,
            obligation_id="O2",
            obligation_hash=current_o2.obligation_hash,
            root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
            artifact_sha256=item.artifact_sha256,
            retained_artifact_path=item.retained_artifact_path,
            scope_obligation_ids=("O2",),
            verifier_status=item.verifier_status,
            audit_status=item.audit_status,
            authority_status=item.authority_status,
            authority_refs=item.authority_refs,
            summary="freshly reprojected for O2",
        )
        for item in closure.validated_evidence
    )
    transfer_decision, v4 = store.revalidate_transferred_session_closure(
        session.tactical_session_id,
        target_obligation_id="O2",
        transfer=transfer,
        authorization=authorization,
        revalidated_evidence=revalidated,
        recorded_by="test",
    )
    assert transfer_decision.status == "RESOLUTION_ACCEPTED"
    assert v4 is not None
    assert v4.obligation_ref("O1").disposition == "SUPERSEDED"
    assert v4.obligation_ref("O2").disposition == "RESOLVED"

    # Replaying the old closure after explicit transfer remains stale; it does
    # not resolve O1 or manufacture another map version.
    for _ in range(2):
        stale, no_map = store.resolve_session_closure(
            session.tactical_session_id, recorded_by="replay"
        )
        assert stale.status == "STALE_SESSION_CLOSURE"
        assert no_map is None
    assert store.load_current_map(v3.research_map_id).version == v4.version
    assert project.load_theorem("T1")["status"] == "OPEN"


def test_f002_expired_result_is_retained_but_cannot_be_accepted(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "lease fence")
    root_hash = domain_hash("claim", {"id": "C1"})
    binding = CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=root_hash)
    backend = SQLiteRuntimeBackend(project.root)
    job = backend.create_logical_job(
        job_kind="TACTICAL_SESSION",
        semantic_target="O1",
        idempotency_key="expired-result",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash=domain_hash("payload", {"id": "expired"}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="test", ttl_seconds=0.001)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="test",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    time.sleep(0.02)
    artifact_path = project.root / "runtime" / "expired.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text('{"late":true}\n', encoding="utf-8")
    artifact = backend.register_artifact(
        artifact_path,
        artifact_kind="PROVIDER_RESULT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
        execution_binding=binding,
    )
    assert result["authoritative"] == 0
    assert result["ingestion_state"] == "STALE_FENCED"
    assert backend.verify_artifact(artifact["artifact_id"])["artifact_id"] == artifact["artifact_id"]
    with pytest.raises(RuntimeConflict, match="no current-compatible"):
        backend.accept_result(job["logical_job_id"])
    assert not backend.list_rows("effect_slots")


def test_f007_stale_binding_is_fenced_at_acceptance(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "binding fence")
    c1 = domain_hash("claim", {"id": "C1"})
    c2 = domain_hash("claim", {"id": "C2"})
    binding = CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=c1)
    backend = SQLiteRuntimeBackend(project.root)
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="O1",
        idempotency_key="stale-binding",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash=domain_hash("payload", {"id": "stale"}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="test", ttl_seconds=60)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="test",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    artifact_store = RuntimeArtifactStore(project.root)
    artifact = artifact_store.persist_and_register(
        backend,
        {"claim_snapshot_hash": c1},
        artifact_kind="PROVIDER_RESULT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
        execution_binding=binding,
    )
    with pytest.raises(RuntimeConflict, match="no current-compatible"):
        backend.accept_result(
            job["logical_job_id"],
            binding_validator=lambda current: (
                True
                if current is not None and current.root_claim_snapshot_hash == c2
                else "STALE_CLAIM_SNAPSHOT: C1 is no longer current"
            ),
        )
    fenced = next(row for row in backend.list_rows("attempt_results") if row["result_id"] == result["result_id"])
    assert fenced["authoritative"] == 0
    assert fenced["ingestion_state"] == "STALE_FENCED"
    assert backend.verify_artifact(artifact["artifact_id"])["artifact_id"] == artifact["artifact_id"]
    assert not backend.list_rows("effect_slots")


def test_f007_binding_survives_job_attempt_result_and_effect_persistence(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "binding persistence")
    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=domain_hash("claim", {"id": "C1"}),
        research_map_id="map-T1",
        research_map_version=4,
        research_map_hash=domain_hash("map", {"version": 4}),
        research_obligation_id="O1",
        directive_id="directive-1",
        tactical_session_id="session-1",
    )
    backend = SQLiteRuntimeBackend(project.root)
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="O1",
        idempotency_key="binding-persistence",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash=domain_hash("payload", {"id": "persisted"}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="test", ttl_seconds=60)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="test",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    artifact = RuntimeArtifactStore(project.root).persist_and_register(
        backend,
        {"ok": True},
        artifact_kind="PROVIDER_RESULT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
        execution_binding=binding,
    )
    backend.accept_result(job["logical_job_id"])
    slot, created = backend.prepare_effect(
        logical_job_id=job["logical_job_id"],
        effect_kind="PERSISTENCE_PROBE",
        semantic_target_type="RESEARCH_OBLIGATION",
        semantic_target_id="O1",
        source_result_id=result["result_id"],
        execution_binding=binding,
        binding_validator=lambda current: True,
    )
    assert created is True
    assert CrossPlaneExecutionBinding.from_dict(
        json.loads(job["cross_plane_binding"])
    ) == binding
    assert CrossPlaneExecutionBinding.from_dict(
        json.loads(attempt["cross_plane_binding"])
    ) == binding
    persisted_result = next(
        row for row in backend.list_rows("attempt_results") if row["result_id"] == result["result_id"]
    )
    assert CrossPlaneExecutionBinding.from_dict(
        json.loads(persisted_result["cross_plane_binding"])
    ) == binding
    assert CrossPlaneExecutionBinding.from_dict(
        json.loads(slot["cross_plane_binding"])
    ) == binding


def test_f003_domain_apply_before_ack_reconciles_by_effect_identity(tmp_path):
    project, _, store, _, research_map, _, session, artifacts = _session(tmp_path)
    closure = _valid_closure(store, session, artifacts)
    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=closure.root_claim_snapshot_hash,
        research_map_id=closure.research_map_id,
        research_map_version=closure.research_map_version,
        research_map_hash=closure.research_map_hash,
        research_obligation_id=closure.obligation_id,
        directive_id=closure.directive_id,
        tactical_session_id=closure.tactical_session_id,
    )
    runtime = SQLiteRuntimeBackend(project.root)
    source = RuntimeEffectCoordinator(runtime).register_semantic_result(
        idempotency_key=f"closure-source:{closure.session_closure_id}",
        semantic_target=closure.obligation_id,
        payload={"closure_hash": closure.closure_hash},
        binding=binding,
    )
    coordinator = RuntimeEffectCoordinator(runtime)
    with pytest.raises(FaultInjected):
        coordinator.apply_research_session_closure(
            logical_job_id=source["logical_job_id"],
            source_result_id=source["result_id"],
            research_store=store,
            tactical_session_id=session.tactical_session_id,
            recorded_by="test",
            fault_injector=FaultInjector(FaultPoint.AFTER_DOMAIN_APPLY_BEFORE_ACK),
        )
    assert store.load_current_map(research_map.research_map_id).version == 2
    replay_slot, replay = coordinator.apply_research_session_closure(
        logical_job_id=source["logical_job_id"],
        source_result_id=source["result_id"],
        research_store=store,
        tactical_session_id=session.tactical_session_id,
        recorded_by="replay",
    )
    assert replay_slot["status"] == "ACKNOWLEDGED"
    assert replay["decision"].status == "RESOLUTION_ACCEPTED"
    assert store.load_current_map(research_map.research_map_id).version == 2
    assert len(runtime.list_rows("effect_slots")) == 1


def test_production_semantic_effects_use_effect_slots_and_exact_bindings(tmp_path):
    repository_root = Path(__file__).resolve().parents[3]
    project_root = tmp_path / "project"
    shutil.copytree(repository_root / "projects" / "demo", project_root)
    theorem_path = project_root / "theorems" / "demo-odd-sum.json"
    theorem = json.loads(theorem_path.read_text(encoding="utf-8"))
    theorem.update({"status": "OPEN", "proof_file": "", "last_run": "", "history": []})
    theorem_path.write_text(json.dumps(theorem, indent=2) + "\n", encoding="utf-8")

    state = ResearchOrchestrator(
        ProjectStore(project_root),
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
    ).run()

    backend = SQLiteRuntimeBackend(project_root)
    effect_kinds = {row["effect_kind"] for row in backend.list_rows("effect_slots")}
    assert {
        "APPLY_SESSION_CLOSURE",
        "COMMIT_STRUCTURAL_EFFECT",
        "RECORD_GOVERNANCE_SESSION",
        "APPLY_TRUTH_MUTATION",
    } <= effect_kinds

    closure_job = next(
        row
        for row in backend.list_rows("logical_jobs")
        if row["idempotency_key"] == f"session-closure:{state['session_closure_id']}"
    )
    binding = CrossPlaneExecutionBinding.from_dict(
        json.loads(closure_job["cross_plane_binding"])
    )
    current_map = ResearchStoreFacade(ProjectStore(project_root)).load_current_map(
        state["research_map_id"]
    )
    assert binding.root_claim_snapshot_hash == current_map.root_claim_snapshot_hash
    assert binding.research_map_id == state["research_map_id"]
    assert binding.research_map_version == 1
    assert binding.research_map_hash
    assert binding.research_obligation_id
    assert binding.directive_id
    assert binding.tactical_session_id == state["tactical_session_id"]
