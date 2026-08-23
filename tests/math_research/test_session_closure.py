from __future__ import annotations

from pathlib import Path

from math_research_agent.research.project import ProjectStore
from math_research_agent.research.research_evidence import (
    ProviderProvenance,
    can_resolve_obligation,
)
from math_research_agent.research.research_store import ResearchStoreFacade
from math_research_agent.research.runtime_backend import SQLiteRuntimeBackend
from math_research_agent.research.runtime_effects import RuntimeEffectCoordinator
from math_research_agent.research.runtime_model import AttemptState
from math_research_agent.research.truth_identity import domain_hash
from math_research_agent.research.truth_store import TruthStoreFacade


def _session(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Session closure")
    project.add_theorem("T1", "Root", "P holds for all n.")
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
                "title": "Core lemma",
                "statement": "Prove the core lemma",
                "obligation_kind": "LEMMA",
                "scope": ["core lemma only"],
            }
        ],
        created_by="test",
    )
    directive = store.create_directive(
        research_map.research_map_id,
        "O1",
        tactical_goal="Produce and independently validate the core lemma.",
        allowed_scope=("core lemma only",),
        created_by="test",
    )
    session = store.bind_tactical_session(
        directive.directive_id,
        execution_run_id="run-1",
        execution_status="RUNNING",
    )
    artifacts = project.root / "runs" / "run-1"
    artifacts.mkdir(parents=True)
    return project, store, snapshot, research_map, session, artifacts


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_r6_worker_prose_and_candidate_alone_cannot_resolve(tmp_path):
    project, store, _, v1, session, artifacts = _session(tmp_path)
    worker = _write(artifacts / "worker.md", "THE OBLIGATION IS FULLY PROVED")
    candidate = _write(artifacts / "candidate.md", "Candidate proof without validation")
    closure = store.close_tactical_session(
        session.tactical_session_id,
        execution_status="COMPLETED",
        raw_artifacts=(
            {"path": worker, "artifact_kind": "WORKER_OUTPUT", "producer": "worker"},
            {"path": candidate, "artifact_kind": "CANDIDATE", "producer": "planner"},
        ),
        evidence_specs=(
            {
                "artifact_path": candidate,
                "evidence_kind": "CANDIDATE",
                "verifier_status": "NOT_APPLICABLE",
                "audit_status": "NOT_APPLICABLE",
                "authority_status": "UNKNOWN",
            },
        ),
        closed_by="test",
    )
    decision, revised = store.resolve_session_closure(
        session.tactical_session_id, recorded_by="test"
    )
    assert closure.raw_artifacts[0].artifact_sha256
    assert decision.status == "INSUFFICIENT_EVIDENCE"
    assert revised is None
    assert store.load_current_map(v1.research_map_id).obligation_ref("O1").disposition == "OPEN"
    assert project.load_theorem("T1")["status"] == "OPEN"


def test_r7_r17_trusted_evidence_resolves_and_retains_all_raw_artifacts(tmp_path):
    project, store, _, v1, session, artifacts = _session(tmp_path)
    worker = _write(artifacts / "worker.md", "local construction")
    candidate = _write(artifacts / "candidate.md", "complete candidate proof")
    verifier = _write(artifacts / "verifier.json", '{"verdict":"CORRECT"}')
    audit = _write(artifacts / "audit.json", '{"outcome":"PASS"}')
    events = _write(artifacts / "events.json", "[]")
    specs = (
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
            "authority_refs": ("authority:gate-1",),
        },
    )
    closure = store.close_tactical_session(
        session.tactical_session_id,
        execution_status="COMPLETED",
        raw_artifacts=tuple(
            {
                "path": path,
                "artifact_kind": kind,
                "producer": producer,
            }
            for path, kind, producer in (
                (worker, "WORKER_OUTPUT", "worker"),
                (candidate, "CANDIDATE", "planner"),
                (verifier, "VERIFIER", "worker_verifier"),
                (audit, "AUDIT", "audit_gate"),
                (events, "EVENTS", "runtime"),
            )
        ),
        evidence_specs=specs,
        provider_provenance=(
            ProviderProvenance.capture(provider="mock", model="mock", call_refs=("call-1",)),
        ),
        closed_by="test",
    )
    retained = [project.root / item.retained_path for item in closure.raw_artifacts]
    worker.unlink()
    candidate.unlink()
    verifier.unlink()
    audit.unlink()
    events.unlink()
    assert all(path.is_file() for path in retained)

    runtime = SQLiteRuntimeBackend(project.root)
    job = runtime.create_logical_job(
        job_kind="TACTICAL_SESSION",
        semantic_target="O1",
        idempotency_key="session-closure-runtime-e2e",
        obligation_id="O1",
        claim_snapshot_hash=v1.root_claim_snapshot_hash,
    )
    attempt, _ = runtime.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash="sha256:session-closure",
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
    closure_path = (
        project.root / "research" / "sessions" / session.tactical_session_id / "closure.json"
    )
    artifact = runtime.register_artifact(
        closure_path,
        artifact_kind="SESSION_CLOSURE",
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
    slot, outcome = RuntimeEffectCoordinator(runtime).apply_research_session_closure(
        logical_job_id=job["logical_job_id"],
        source_result_id=result["result_id"],
        research_store=store,
        tactical_session_id=session.tactical_session_id,
        recorded_by="test",
    )
    decision, v2 = outcome["decision"], outcome["research_map"]
    assert decision.status == "RESOLUTION_ACCEPTED"
    assert v2 is not None
    assert v2.version == v1.version + 1
    assert v2.obligation_ref("O1").disposition == "RESOLVED"
    assert project.load_theorem("T1")["status"] == "OPEN"
    replay_decision, replay_map = store.resolve_session_closure(
        session.tactical_session_id, recorded_by="test-replay"
    )
    assert replay_decision.decision_hash == decision.decision_hash
    assert replay_map.research_map_hash == v2.research_map_hash
    assert store.load_current_map(v1.research_map_id).version == 2
    replay_slot, _ = RuntimeEffectCoordinator(runtime).apply_research_session_closure(
        logical_job_id=job["logical_job_id"],
        source_result_id=result["result_id"],
        research_store=store,
        tactical_session_id=session.tactical_session_id,
        recorded_by="test-replay",
    )
    assert replay_slot["effect_slot_id"] == slot["effect_slot_id"]
    assert len(runtime.list_rows("effect_slots")) == 1
    affected = store.affected_by_reference("authority:gate-1")
    assert affected["obligation_ids"] == ["O1"]


def test_resolution_gate_reports_stale_scope_authority_and_audit_statuses(tmp_path):
    _, store, snapshot, v1, session, artifacts = _session(tmp_path)
    candidate = _write(artifacts / "candidate.md", "candidate")
    verifier = _write(artifacts / "verifier.md", "pass")
    audit = _write(artifacts / "audit.md", "fail")
    closure = store.close_tactical_session(
        session.tactical_session_id,
        execution_status="FAILED",
        raw_artifacts=tuple(
            {"path": path, "artifact_kind": kind, "producer": "test"}
            for path, kind in ((candidate, "CANDIDATE"), (verifier, "VERIFIER"), (audit, "AUDIT"))
        ),
        evidence_specs=(
            {"artifact_path": candidate, "evidence_kind": "CANDIDATE"},
            {
                "artifact_path": verifier,
                "evidence_kind": "VERIFIER",
                "verifier_status": "PASS",
            },
            {
                "artifact_path": audit,
                "evidence_kind": "AUDIT",
                "audit_status": "FAIL",
                "authority_status": "TRUSTED",
            },
        ),
        closed_by="test",
    )
    obligation = store.load_obligation(v1.obligation_ref("O1").obligation_hash)
    assert (
        can_resolve_obligation(
            obligation, closure, current_claim_snapshot_hash=snapshot.claim_snapshot_hash
        ).status
        == "AUDIT_FAILED"
    )
    assert (
        can_resolve_obligation(
            obligation,
            closure,
            current_claim_snapshot_hash=domain_hash("different_claim", {"v": 2}),
        ).status
        == "STALE_EVIDENCE"
    )
