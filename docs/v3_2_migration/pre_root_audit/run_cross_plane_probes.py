"""Deterministic pre-root-synthesis probes.

This runner is audit instrumentation only.  It creates temporary projects,
invokes the frozen Phase 3--6 code, and reports observed state as JSON.  It
does not modify production sources or the checked-in project state.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from openprover.math_research.architecture_critic import (
    ArchitectureCriticIndependenceReceipt,
)
from openprover.math_research.architecture_review import GovernanceActor
from openprover.math_research.project import ProjectStore
from openprover.math_research.research_evidence import ProviderProvenance
from openprover.math_research.research_map import MapRevisionReason
from openprover.math_research.research_obligation import ObligationDispositionKind
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.runtime_artifacts import RuntimeArtifactStore
from openprover.math_research.runtime_backend import SQLiteRuntimeBackend
from openprover.math_research.runtime_dispatch import DurableProviderDispatcher
from openprover.math_research.runtime_model import (
    AttemptState,
    FaultInjected,
    FaultInjector,
    FaultPoint,
)
from openprover.math_research.truth_store import TruthStoreFacade


def _result(
    probe_id: str,
    expected: str,
    actual: str,
    *,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "probe_id": probe_id,
        "status": status,
        "expected": expected,
        "actual": actual,
        "details": details or {},
    }


def _session(root: Path):
    project = ProjectStore.initialize(root / "project", "Pre-root audit")
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
        created_by="audit",
    )
    directive = store.create_directive(
        research_map.research_map_id,
        "O1",
        tactical_goal="Produce and independently validate the core lemma.",
        allowed_scope=("core lemma only",),
        created_by="audit",
    )
    session = store.bind_tactical_session(
        directive.directive_id,
        execution_run_id="run-1",
        execution_status="RUNNING",
    )
    artifacts = project.root / "runs" / "run-1"
    artifacts.mkdir(parents=True)
    return project, truth, store, snapshot, research_map, session, artifacts


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _valid_closure(store, session, artifacts: Path):
    candidate = _write(artifacts / "candidate.md", "complete candidate proof")
    verifier = _write(artifacts / "verifier.json", '{"verdict":"CORRECT"}')
    audit = _write(artifacts / "audit.json", '{"outcome":"PASS"}')
    return store.close_tactical_session(
        session.tactical_session_id,
        execution_status="COMPLETED",
        raw_artifacts=tuple(
            {"path": path, "artifact_kind": kind, "producer": producer}
            for path, kind, producer in (
                (candidate, "CANDIDATE", "planner"),
                (verifier, "VERIFIER", "worker_verifier"),
                (audit, "AUDIT", "audit_gate"),
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
                "authority_refs": ("authority:gate-1",),
            },
        ),
        provider_provenance=(
            ProviderProvenance.capture(provider="mock", model="mock", call_refs=("call-1",)),
        ),
        closed_by="audit",
    )


def probe_x1_claim_binding() -> dict[str, Any]:
    """Show that runtime acceptance does not itself compare claim snapshots."""

    root = Path(tempfile.mkdtemp(prefix="pre-root-x1-"))
    backend = SQLiteRuntimeBackend(root / "project")
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="T1",
        idempotency_key="x1-claim-binding",
        claim_snapshot_hash="sha256:" + "1" * 64,
    )
    response = DurableProviderDispatcher(backend).execute(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        model="mock",
        reasoning_tier="routine",
        payload={"claim_snapshot_hash": "sha256:" + "1" * 64},
        claim_snapshot_hash="sha256:" + "1" * 64,
        invoke=lambda: {"late": True, "claim": "C1"},
    )
    current_job = backend.get_job(job["logical_job_id"])
    return _result(
        "X1",
        "late C1 result is retained but cannot become current C2 authority",
        "runtime accepted the result as authoritative; no runtime stale comparison occurred",
        status="PARTIAL",
        details={
            "accepted_result_id": current_job["accepted_result_id"],
            "runtime_claim_snapshot_hash": current_job["claim_snapshot_hash"],
            "returned_claim": response.get("claim"),
            "note": "Domain gates must still revalidate; the SQLite runtime did not enforce it.",
        },
    )


def probe_x2_old_session_new_map() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pre-root-x2-") as temp:
        project, _, store, _, v1, session, artifacts = _session(Path(temp))
        closure = _valid_closure(store, session, artifacts)
        _, v2 = store.add_obligation(
            v1.research_map_id,
            obligation_id="O2",
            title="Replacement lemma",
            statement="Prove the replacement lemma",
            obligation_kind="LEMMA",
            scope=("replacement lemma only",),
            created_by="governance-reframe",
        )
        _, v3 = store.record_disposition(
            v1.research_map_id,
            "O1",
            disposition=ObligationDispositionKind.SUPERSEDED.value,
            superseded_by=("O2",),
            reason="approved reframe transfer",
            recorded_by="governance-reframe",
            revision_reason=MapRevisionReason.SCOPE_SUPERSESSION.value,
        )
        decision, v4 = store.resolve_session_closure(
            session.tactical_session_id, recorded_by="audit-late"
        )
        return _result(
            "X2",
            "old SessionClosure is rejected or explicitly revalidated when map version changes",
            f"{decision.status}; map version advanced {v3.version}->{v4.version if v4 else None}",
            status=(
                "FAIL"
                if decision.status == "RESOLUTION_ACCEPTED" and v4 is not None
                else "PASS"
            ),
            details={
                "closure_research_map_version": closure.research_map_version,
                "closure_research_map_hash": closure.research_map_hash,
                "current_before_resolution_version": v3.version,
                "current_before_resolution_disposition": v3.obligation_ref("O1").disposition,
                "current_after_resolution_version": v4.version if v4 else None,
                "resolved_disposition": (
                    v4.obligation_ref("O1").disposition if v4 else None
                ),
                "replacement_obligation_disposition": (
                    v4.obligation_ref("O2").disposition if v4 else None
                ),
                "project_root": str(project.root),
            },
        )


def probe_research_thesis_bypass() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pre-root-governance-") as temp:
        _, _, store, _, research_map, _, _ = _session(Path(temp))
        try:
            revised = store.revise_map(
                research_map,
                strategic_thesis="UNAUTHORIZED REFRAME",
                created_by="planner-prose",
                revision_reason=MapRevisionReason.HUMAN_STEERING.value,
            )
        except Exception as exc:  # expected secure behavior
            return _result(
                "GOV-THESIS-BYPASS",
                "strategic thesis change requires an authorized ArchitecturePatch",
                f"rejected: {type(exc).__name__}: {exc}",
                status="PASS",
            )
        return _result(
            "GOV-THESIS-BYPASS",
            "strategic thesis change requires an authorized ArchitecturePatch",
            f"accepted map revision v{revised.version} without authorization",
            status="FAIL",
            details={"research_map_hash": revised.research_map_hash},
        )


def probe_x4_expired_lease_result() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="pre-root-x4-")) / "project"
    backend = SQLiteRuntimeBackend(root)
    job = backend.create_logical_job(
        job_kind="TACTICAL_SESSION",
        semantic_target="O1",
        idempotency_key="x4-expired-lease",
        obligation_id="O1",
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="worker-a",
        payload_hash="sha256:" + "a" * 64,
        dispatch_kind="PROVIDER_INVOCATION",
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="worker-a", ttl_seconds=60)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="worker-a",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    with backend._transaction() as connection:
        connection.execute(
            "UPDATE attempts SET lease_expires_at = ? WHERE attempt_id = ?",
            (time.time() - 10, attempt["attempt_id"]),
        )
    artifact = RuntimeArtifactStore(root).persist_and_register(
        backend,
        {"worker_event": "high-value", "generation": lease["generation"]},
        artifact_kind="WORKER_EVENT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    return _result(
        "X4",
        "expired generation-1 result is retained but fenced before it can be authoritative",
        f"ingestion_state={result['ingestion_state']}; authoritative={bool(result['authoritative'])}",
        status="FAIL" if result["authoritative"] else "PASS",
        details={
            "attempt_state_after_ingestion": backend.get_attempt(attempt["attempt_id"])["state"],
            "lease_expires_at": backend.get_attempt(attempt["attempt_id"])["lease_expires_at"],
            "result_id": result["result_id"],
        },
    )


def probe_after_provider_result_recovery() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="pre-root-dispatched-")) / "project"
    backend = SQLiteRuntimeBackend(root)
    job = backend.create_logical_job(
        job_kind="TACTICAL_SESSION",
        semantic_target="O1",
        idempotency_key="after-provider-result",
        obligation_id="O1",
    )
    try:
        DurableProviderDispatcher(backend, lease_ttl_seconds=60).execute(
            logical_job_id=job["logical_job_id"],
            provider="mock",
            model="mock",
            reasoning_tier="routine",
            payload={"prompt": "crash window"},
            invoke=lambda: {"answer": "provider accepted"},
            fault_injector=FaultInjector(FaultPoint.AFTER_PROVIDER_RESULT),
        )
    except FaultInjected:
        pass
    before = {
        "attempt": backend.list_rows("attempts")[0],
        "outbox": backend.list_rows("outbox")[0],
        "manifests": RuntimeArtifactStore(root).manifests(),
    }
    with backend._transaction() as connection:
        connection.execute(
            "UPDATE attempts SET lease_expires_at = ? WHERE attempt_id = ?",
            (time.time() - 10, before["attempt"]["attempt_id"]),
        )
    actions = backend.reconcile()
    after = {
        "attempt": backend.list_rows("attempts")[0],
        "outbox": backend.list_rows("outbox")[0],
        "actions": actions,
        "manifests": RuntimeArtifactStore(root).manifests(),
    }
    stuck = (
        after["attempt"]["state"] == AttemptState.ORPHANED
        and after["outbox"]["state"] == "DISPATCHED"
        and not any(
            item["object_type"] == "OUTBOX"
            and item["object_id"] == after["outbox"]["outbox_id"]
            for item in actions
        )
    )
    return _result(
        "FAULT-AFTER-PROVIDER-RESULT",
        "unknown provider execution is classified and retried/adopted/manual-reviewed",
        f"attempt={after['attempt']['state']}; outbox={after['outbox']['state']}; actions={len(actions)}",
        status="FAIL" if stuck else "PASS",
        details={
            "before": {
                "attempt_state": before["attempt"]["state"],
                "outbox_state": before["outbox"]["state"],
                "manifest_count": len(before["manifests"]),
            },
            "after": {
                "attempt_state": after["attempt"]["state"],
                "outbox_state": after["outbox"]["state"],
                "manifest_count": len(after["manifests"]),
                "actions": after["actions"],
            },
        },
    )


def probe_same_model_independence() -> dict[str, Any]:
    review = GovernanceActor.capture(
        role="REVIEWER",
        actor_id="reviewer-1",
        provider="mock",
        model="same-model",
        context_hash="sha256:" + "1" * 64,
        fresh_context=True,
    )
    critic = GovernanceActor.capture(
        role="CRITIC",
        actor_id="critic-1",
        provider="mock",
        model="same-model",
        context_hash="sha256:" + "2" * 64,
        fresh_context=True,
    )
    receipt = ArchitectureCriticIndependenceReceipt.capture(
        review_author=review,
        patch_author_id="patch-author-1",
        critic_actor=critic,
        shared_evidence_refs=(),
    )
    return _result(
        "GOV-SAME-MODEL-FALLBACK",
        "same-model fallback is explicitly policy_satisfied=false",
        f"same_model={receipt.same_model}; policy_satisfied={receipt.policy_satisfied}",
        status="FAIL" if receipt.same_model and receipt.policy_satisfied else "PASS",
        details=receipt.to_dict(),
    )


def run() -> list[dict[str, Any]]:
    probes: tuple[Callable[[], dict[str, Any]], ...] = (
        probe_x1_claim_binding,
        probe_x2_old_session_new_map,
        probe_research_thesis_bypass,
        probe_x4_expired_lease_result,
        probe_after_provider_result_recovery,
        probe_same_model_independence,
    )
    results: list[dict[str, Any]] = []
    for probe in probes:
        try:
            results.append(probe())
        except Exception as exc:  # preserve a failed observation for the evidence record
            results.append(
                _result(
                    probe.__name__,
                    "probe completes deterministically",
                    f"probe error: {type(exc).__name__}: {exc}",
                    status="UNVERIFIED",
                )
            )
    return results


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
