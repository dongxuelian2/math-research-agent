from __future__ import annotations

import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openprover.math_research.governance import GovernanceController
from openprover.math_research.cli import build_parser, dispatch
from openprover.math_research.project import ProjectStore
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.runtime_artifacts import RuntimeArtifactStore
from openprover.math_research.runtime_backend import SQLiteRuntimeBackend, _MIGRATION_1
from openprover.math_research.runtime_bindings import CrossPlaneExecutionBinding
from openprover.math_research.runtime_dispatch import DurableProviderDispatcher
from openprover.math_research.runtime_model import (
    AttemptState,
    FaultInjected,
    FaultInjector,
    FaultPoint,
    JobState,
    RuntimeConflict,
)
from openprover.math_research.truth_store import TruthStoreFacade
from openprover.math_research.truth_identity import domain_hash


def _job(backend: SQLiteRuntimeBackend, key: str = "job") -> dict:
    return backend.create_logical_job(
        job_kind="TACTICAL_SESSION",
        semantic_target="O1",
        idempotency_key=key,
        obligation_id="O1",
        execution_binding=CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=domain_hash("test-claim", {"id": "runtime"})
        ),
    )


def _running_attempt(
    backend: SQLiteRuntimeBackend,
    job: dict,
    *,
    provider: str = "mock",
) -> tuple[dict, dict, dict, dict]:
    attempt, outbox = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider=provider,
        model="mock-model",
        reasoning_tier="routine",
        payload_hash="sha256:" + "a" * 64,
        dispatch_kind="PROVIDER_INVOCATION",
    )
    outbox_claim = backend.claim_outbox(outbox["outbox_id"], owner="test", ttl_seconds=60)
    lease = backend.claim_attempt(attempt["attempt_id"], owner="test", ttl_seconds=60)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="test",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    backend.transition_outbox(
        outbox["outbox_id"],
        "DISPATCHED",
        claim_token=outbox_claim["claim_token"],
        claim_generation=outbox_claim["claim_generation"],
        actor="test",
    )
    return attempt, outbox, lease, outbox_claim


def _result(
    backend: SQLiteRuntimeBackend,
    store: RuntimeArtifactStore,
    job: dict,
    *,
    tag: str,
) -> tuple[dict, dict, dict]:
    attempt, outbox, lease, outbox_claim = _running_attempt(backend, job)
    artifact = store.persist_and_register(
        backend,
        {"status": "SUCCESS", "tag": tag},
        artifact_kind="PROVIDER_RESULT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        idempotency_key=f"{attempt['attempt_id']}:{tag}",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    backend.transition_outbox(
        outbox["outbox_id"],
        "ACKNOWLEDGED",
        claim_token=outbox_claim["claim_token"],
        claim_generation=outbox_claim["claim_generation"],
        actor="test",
    )
    return attempt, artifact, result


def test_d1_sqlite_schema_version_wal_and_control_plane_boundary(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    check = backend.check()
    assert check["schema_version"] == 3
    assert check["journal_mode"] == "WAL"
    assert check["foreign_keys"] is True
    assert check["synchronous"] != 0
    assert check["integrity_check"] == "ok"
    assert check["control_plane_only"] is True
    assert "BLOB" not in backend.schema_sql().upper()


def test_d1_forward_migration_from_previous_runtime_schema(tmp_path: Path):
    root = tmp_path / "project"
    database = root / "runtime" / "control.sqlite3"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.executescript(_MIGRATION_1)
    connection.execute(
        "INSERT INTO runtime_schema(singleton, schema_version, migrated_at) VALUES(1, 1, 'old')"
    )
    connection.commit()
    connection.close()
    backend = SQLiteRuntimeBackend(root)
    assert backend.check()["schema_version"] == 3
    assert [
        row["target_version"] for row in backend.list_rows("runtime_migration_history")
    ] == [2, 3]


def test_d2_logical_job_is_stable_across_multiple_attempts(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    job = _job(backend)
    first, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="gemini",
        payload_hash="sha256:one",
        dispatch_kind="PROVIDER_INVOCATION",
    )
    second, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="codex_cli",
        payload_hash="sha256:one",
        dispatch_kind="PROVIDER_INVOCATION",
        retry_fallback_reason="Gemini timeout",
    )
    assert first["attempt_id"] != second["attempt_id"]
    assert first["attempt_number"] == 1
    assert second["attempt_number"] == 2
    assert first["logical_job_id"] == second["logical_job_id"] == job["logical_job_id"]


def test_d3_intent_and_outbox_are_durable_before_provider_invocation(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    job = _job(backend)
    observed = {}

    def provider():
        observed["attempts"] = backend.list_rows("attempts")
        observed["outbox"] = backend.list_rows("outbox")
        return {"result": "ok"}

    response = DurableProviderDispatcher(backend).execute(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        model="mock",
        reasoning_tier="routine",
        payload={"prompt": "bounded"},
        invoke=provider,
    )
    assert observed["attempts"][0]["state"] == "RUNNING"
    assert observed["outbox"][0]["state"] == "DISPATCHED"
    assert response["runtime"]["accepted"] is True


def test_d4_outbox_survives_crash_before_dispatch(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    job = _job(backend)
    called = False

    def provider():
        nonlocal called
        called = True
        return {"result": "unexpected"}

    with pytest.raises(FaultInjected):
        DurableProviderDispatcher(backend).execute(
            logical_job_id=job["logical_job_id"],
            provider="mock",
            model="mock",
            reasoning_tier="routine",
            payload={"prompt": "bounded"},
            invoke=provider,
            fault_injector=FaultInjector(FaultPoint.AFTER_INTENT_COMMIT),
        )
    assert called is False
    assert backend.list_rows("outbox")[0]["state"] == "PENDING"
    actions = SQLiteRuntimeBackend(tmp_path / "project").reconcile()
    assert any(item["action"] == "REDISPATCH" for item in actions)


def test_d5_d8_duplicate_dispatch_and_ingestion_are_idempotent(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    store = RuntimeArtifactStore(tmp_path / "project")
    job = _job(backend)
    attempt, artifact, result = _result(backend, store, job, tag="one")
    duplicate = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        idempotency_key=f"{attempt['attempt_id']}:one",
        lease_token="stale-after-first-ingestion",
        generation=0,
    )
    assert duplicate["result_id"] == result["result_id"]
    assert len(backend.list_rows("attempt_results")) == 1
    assert backend.accept_result(job["logical_job_id"])["result_id"] == result["result_id"]


def test_d6_existing_result_artifact_is_reconciled_without_provider_recall(tmp_path: Path):
    root = tmp_path / "project"
    backend = SQLiteRuntimeBackend(root)
    store = RuntimeArtifactStore(root)
    job = _job(backend)
    attempt, _, _, _ = _running_attempt(backend, job)
    store.write(
        {"result": "survived"},
        artifact_kind="PROVIDER_RESULT",
        producer_attempt_id=attempt["attempt_id"],
        result_metadata={"completion_status": "SUCCESS"},
    )
    actions = SQLiteRuntimeBackend(root).reconcile(
        binding_validator=lambda current: current is not None
    )
    assert any(item["action"] == "INGEST_EXISTING_RESULT" for item in actions)
    assert backend.get_job(job["logical_job_id"])["accepted_result_id"] is not None


def test_d7_database_result_with_missing_artifact_blocks_effect(tmp_path: Path):
    root = tmp_path / "project"
    backend = SQLiteRuntimeBackend(root)
    store = RuntimeArtifactStore(root)
    job = _job(backend)
    _, artifact, result = _result(backend, store, job, tag="missing")
    backend.accept_result(job["logical_job_id"])
    (root / artifact["relative_path"]).unlink()
    actions = backend.reconcile()
    assert any(item["action"] == "BLOCK_MISSING_ARTIFACT" for item in actions)
    assert backend.get_job(job["logical_job_id"])["state"] == JobState.BLOCKED
    with pytest.raises(Exception, match="missing"):
        backend.apply_effect_once(
            logical_job_id=job["logical_job_id"],
            effect_kind="TEST",
            semantic_target_type="O",
            semantic_target_id="O1",
            source_result_id=result["result_id"],
            apply=lambda _: None,
        )


def test_d9_two_successful_attempts_have_one_accepted_result_and_effect(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    store = RuntimeArtifactStore(tmp_path / "project")
    job = _job(backend)
    _, _, first = _result(backend, store, job, tag="first")
    _, _, second = _result(backend, store, job, tag="second")
    winner = backend.accept_result(job["logical_job_id"])
    assert winner["result_id"] == first["result_id"]
    assert second["result_id"] != winner["result_id"]
    calls = []
    backend.apply_effect_once(
        logical_job_id=job["logical_job_id"],
        effect_kind="RESOLVE_RESEARCH_OBLIGATION",
        semantic_target_type="RESEARCH_OBLIGATION",
        semantic_target_id="O1",
        source_result_id=winner["result_id"],
        apply=lambda slot_id: calls.append(slot_id) or {"done": True},
    )
    backend.apply_effect_once(
        logical_job_id=job["logical_job_id"],
        effect_kind="RESOLVE_RESEARCH_OBLIGATION",
        semantic_target_type="RESEARCH_OBLIGATION",
        semantic_target_id="O1",
        source_result_id=winner["result_id"],
        apply=lambda slot_id: calls.append(slot_id) or {"done": True},
    )
    assert len(calls) == 1
    assert len(backend.list_rows("effect_slots")) == 1


def test_d10_attempt_lease_acquisition_is_compare_and_swap(tmp_path: Path):
    root = tmp_path / "project"
    backend = SQLiteRuntimeBackend(root)
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=_job(backend)["logical_job_id"],
        provider="mock",
        payload_hash="sha256:x",
        dispatch_kind="PROVIDER_INVOCATION",
    )
    barrier = threading.Barrier(2)

    def claim(owner):
        candidate = SQLiteRuntimeBackend(root)
        barrier.wait()
        try:
            return candidate.claim_attempt(attempt["attempt_id"], owner=owner, ttl_seconds=60)
        except RuntimeConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("one", "two")))
    assert sum(item is not None for item in outcomes) == 1


def test_d11_d12_heartbeat_and_expired_lease_orphaning(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=_job(backend)["logical_job_id"],
        provider="mock",
        payload_hash="sha256:x",
        dispatch_kind="PROVIDER_INVOCATION",
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="worker", ttl_seconds=5)
    renewed = backend.heartbeat(
        attempt["attempt_id"],
        lease_token=lease["lease_token"],
        generation=lease["generation"],
        ttl_seconds=10,
    )
    assert renewed["lease_expires_at"] > lease["lease_expires_at"]
    orphaned = backend.orphan_expired_leases(now=renewed["lease_expires_at"] + 1)
    assert orphaned[0]["state"] == AttemptState.ORPHANED
    assert backend.get_job(attempt["logical_job_id"])["state"] == JobState.ACTIVE


def test_d13_stale_fencing_result_is_retained_but_cannot_win(tmp_path: Path):
    root = tmp_path / "project"
    backend = SQLiteRuntimeBackend(root)
    store = RuntimeArtifactStore(root)
    job = _job(backend)
    attempt, _, lease1, _ = _running_attempt(backend, job)
    stale_artifact = store.persist_and_register(
        backend,
        {"worker": "stale"},
        artifact_kind="PROVIDER_RESULT_STALE",
        producer_attempt_id=attempt["attempt_id"],
    )
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.ORPHANED,
        actor="reconciler",
        lease_token=lease1["lease_token"],
        generation=lease1["generation"],
    )
    lease2 = backend.claim_attempt(
        attempt["attempt_id"], owner="replacement", ttl_seconds=60, allow_orphaned=True
    )
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="replacement",
        lease_token=lease2["lease_token"],
        generation=lease2["generation"],
    )
    stale = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=stale_artifact["artifact_id"],
        completion_status="SUCCESS",
        idempotency_key="stale",
        lease_token=lease1["lease_token"],
        generation=lease1["generation"],
    )
    fresh_artifact = store.persist_and_register(
        backend,
        {"worker": "replacement"},
        artifact_kind="PROVIDER_RESULT_FRESH",
        producer_attempt_id=attempt["attempt_id"],
    )
    fresh = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=fresh_artifact["artifact_id"],
        completion_status="SUCCESS",
        idempotency_key="fresh",
        lease_token=lease2["lease_token"],
        generation=lease2["generation"],
    )
    assert stale["ingestion_state"] == "STALE_FENCED"
    assert fresh["authoritative"] == 1
    assert backend.accept_result(job["logical_job_id"])["result_id"] == fresh["result_id"]


def test_d14_cancel_complete_race_has_one_authoritative_terminal_state(tmp_path: Path):
    root = tmp_path / "project"
    backend = SQLiteRuntimeBackend(root)
    store = RuntimeArtifactStore(root)
    job = _job(backend)
    attempt, _, lease, _ = _running_attempt(backend, job)
    backend.request_cancel(attempt["attempt_id"], actor="user")
    artifact = store.persist_and_register(
        backend,
        {"completed": True},
        artifact_kind="PROVIDER_RESULT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    backend.accept_result(job["logical_job_id"])
    final = backend.finalize_cancel(attempt["attempt_id"], actor="executor")
    assert final["state"] == AttemptState.COMPLETED
    assert result["authoritative"] == 1


def test_d19_cross_store_artifact_partial_write_is_recoverable(tmp_path: Path):
    root = tmp_path / "project"
    backend = SQLiteRuntimeBackend(root)
    store = RuntimeArtifactStore(root)
    job = _job(backend)
    attempt, _, _, _ = _running_attempt(backend, job)
    with pytest.raises(FaultInjected):
        store.persist_and_register(
            backend,
            {"durable": True},
            artifact_kind="PROVIDER_RESULT",
            producer_attempt_id=attempt["attempt_id"],
            fault_injector=FaultInjector(FaultPoint.AFTER_ARTIFACT_WRITE),
        )
    assert backend.list_rows("artifact_registry") == []
    actions = SQLiteRuntimeBackend(root).reconcile()
    assert any(item["action"] == "REPAIR_PROJECTION" for item in actions)
    assert len(backend.list_rows("artifact_registry")) == 1


def test_d19_domain_apply_before_runtime_ack_is_reconciled_once(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    store = RuntimeArtifactStore(tmp_path / "project")
    job = _job(backend)
    _, _, result = _result(backend, store, job, tag="effect-crash")
    backend.accept_result(job["logical_job_id"])
    domain_receipts = {}

    def apply(slot_id):
        return domain_receipts.setdefault(slot_id, {"domain_revision": 2})

    def recover(slot_id):
        return domain_receipts.get(slot_id)

    with pytest.raises(FaultInjected):
        backend.apply_effect_once(
            logical_job_id=job["logical_job_id"],
            effect_kind="CREATE_RESEARCH_MAP_REVISION",
            semantic_target_type="RESEARCH_MAP",
            semantic_target_id="map-T1",
            source_result_id=result["result_id"],
            apply=apply,
            recover=recover,
            fault_injector=FaultInjector(FaultPoint.AFTER_DOMAIN_APPLY_BEFORE_ACK),
        )
    slot, outcome = SQLiteRuntimeBackend(tmp_path / "project").apply_effect_once(
        logical_job_id=job["logical_job_id"],
        effect_kind="CREATE_RESEARCH_MAP_REVISION",
        semantic_target_type="RESEARCH_MAP",
        semantic_target_id="map-T1",
        source_result_id=result["result_id"],
        apply=lambda _: pytest.fail("recovery repeated the domain mutation"),
        recover=recover,
    )
    assert slot["status"] == "ACKNOWLEDGED"
    assert outcome == {"domain_revision": 2}
    assert len(domain_receipts) == 1


def test_d20_restart_preserves_truth_research_and_governance(tmp_path: Path):
    project = ProjectStore.initialize(tmp_path / "project", "Restart")
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
                "obligation_id": "O1",
                "title": "Open",
                "statement": "Resolve O1",
                "obligation_kind": "LEMMA",
            }
        ],
        created_by="test",
    )
    governance = GovernanceController(project, research_store=research)
    due = governance.signal_review(research_map.research_map_id, "HUMAN_REQUEST")
    backend = SQLiteRuntimeBackend(project.root)
    _running_attempt(backend, _job(backend))
    theorem_before = project.load_theorem("T1")
    map_before = research.load_current_map(research_map.research_map_id)
    SQLiteRuntimeBackend(project.root).reconcile()
    assert project.load_theorem("T1") == theorem_before
    assert research.load_current_map(research_map.research_map_id) == map_before
    assert governance.load_clock(research_map.research_map_id).clock_hash == due.clock_hash
    assert governance.load_clock(research_map.research_map_id).review_due is True


def test_d21_legacy_checkpoint_import_does_not_invent_history(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    record = backend.import_legacy_checkpoint(
        "runs/legacy/state.json",
        classification="MIGRATED_FROM_LEGACY_CHECKPOINT",
        metadata={"phase": "CONTEXT_READY"},
    )
    assert record["classification"] == "MIGRATED_FROM_LEGACY_CHECKPOINT"
    assert backend.list_rows("attempts") == []
    assert backend.list_rows("outbox") == []
    assert backend.journal() == []


def test_d22_project_runtime_databases_are_isolated(tmp_path: Path):
    first = SQLiteRuntimeBackend(tmp_path / "a")
    second = SQLiteRuntimeBackend(tmp_path / "b")
    first_job = _job(first, "same-local-id")
    second_job = _job(second, "same-local-id")
    assert first_job["logical_job_id"] == second_job["logical_job_id"]
    _running_attempt(first, first_job)
    assert len(first.list_rows("attempts")) == 1
    assert second.list_rows("attempts") == []
    assert first.db_path != second.db_path


def test_runtime_check_and_reconcile_cli_are_cross_platform(tmp_path: Path):
    project = ProjectStore.initialize(tmp_path / "project", "Runtime CLI")
    parser = build_parser()
    checked = dispatch(parser.parse_args(["runtime-check", "--project", str(project.root)]))
    assert checked["journal_mode"] == "WAL"
    reconciled = dispatch(parser.parse_args(["reconcile", "--project", str(project.root)]))
    assert reconciled["runtime"]["integrity_check"] == "ok"
    assert reconciled["action_count"] == 0


def _execute_final_state(root: Path, *, crash: bool) -> dict:
    backend = SQLiteRuntimeBackend(root)
    job = _job(backend)
    dispatcher = DurableProviderDispatcher(backend)
    if crash:
        with pytest.raises(FaultInjected):
            dispatcher.execute(
                logical_job_id=job["logical_job_id"],
                provider="mock",
                model="mock",
                reasoning_tier="routine",
                payload={"prompt": "equivalent"},
                invoke=lambda: {"result": "same"},
                fault_injector=FaultInjector(FaultPoint.BEFORE_RESULT_DB_COMMIT),
            )
        backend = SQLiteRuntimeBackend(root)
        backend.reconcile(binding_validator=lambda current: current is not None)
    else:
        dispatcher.execute(
            logical_job_id=job["logical_job_id"],
            provider="mock",
            model="mock",
            reasoning_tier="routine",
            payload={"prompt": "equivalent"},
            invoke=lambda: {"result": "same"},
        )
    current = backend.get_job(job["logical_job_id"])
    accepted = next(
        row
        for row in backend.list_rows("attempt_results")
        if row["result_id"] == current["accepted_result_id"]
    )
    effects = []
    backend.apply_effect_once(
        logical_job_id=job["logical_job_id"],
        effect_kind="REGISTER_AUDIT_RESULT",
        semantic_target_type="AUDIT_TARGET",
        semantic_target_id="T1",
        source_result_id=accepted["result_id"],
        apply=lambda slot_id: effects.append(slot_id) or {"registered": True},
    )
    return {
        "job_state": backend.get_job(job["logical_job_id"])["state"],
        "accepted_results": sum(
            row["result_id"] == backend.get_job(job["logical_job_id"])["accepted_result_id"]
            for row in backend.list_rows("attempt_results")
        ),
        "effect_states": [row["status"] for row in backend.list_rows("effect_slots")],
        "domain_effect_count": len(effects),
    }


def test_d25_no_crash_and_recovered_final_states_are_equivalent(tmp_path: Path):
    normal = _execute_final_state(tmp_path / "normal", crash=False)
    recovered = _execute_final_state(tmp_path / "recovered", crash=True)
    assert (
        normal
        == recovered
        == {
            "job_state": JobState.COMPLETED,
            "accepted_results": 1,
            "effect_states": ["ACKNOWLEDGED"],
            "domain_effect_count": 1,
        }
    )
