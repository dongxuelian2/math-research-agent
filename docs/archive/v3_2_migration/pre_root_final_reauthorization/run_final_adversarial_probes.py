"""Independent, audit-only adversarial probes for the final pre-root gate.

The probes use temporary projects and never write production sources or checked-in
historical evidence.  They deliberately exercise public production entry points
and record both fail-closed cases and positive binding controls.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from openprover.math_research.architecture_patch import PatchAuthorization
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectError, ProjectStore, utc_now
from openprover.math_research.research_map import MapRevisionReason
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.routing import ModelRouter, RoutedLLMClient
from openprover.math_research.runtime_artifacts import RuntimeArtifactStore
from openprover.math_research.runtime_backend import SQLiteRuntimeBackend
from openprover.math_research.runtime_bindings import CrossPlaneExecutionBinding
from openprover.math_research.runtime_dispatch import DurableProviderDispatcher
from openprover.math_research.runtime_model import (
    AttemptState,
    RuntimeConflict,
    RuntimeResultRejected,
)
from openprover.math_research.truth_identity import domain_hash
from openprover.math_research.truth_store import TruthStoreFacade

REPO_ROOT = Path(__file__).resolve().parents[3]
MOCK_CONFIG = REPO_ROOT / "configs" / "models.mock.json"


def _result(
    probe_id: str,
    expected: str,
    actual: str,
    *,
    passed: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "probe_id": probe_id,
        "status": "PASS" if passed else "FAIL",
        "expected": expected,
        "actual": actual,
        "details": details or {},
    }


def _research(root: Path, *, map_id: str = "map-T1"):
    project = ProjectStore.initialize(
        root / "project", "final pre-root reauthorization"
    )
    project.add_theorem("T1", "Root", "For every n, P(n).")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    research = ResearchStoreFacade(project, truth_store=truth)
    research_map = research.create_initial_map(
        research_map_id=map_id,
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": "O1",
                "title": "O1",
                "statement": "Resolve O1",
                "obligation_kind": "LEMMA",
                "scope": ["O1"],
            }
        ],
        created_by="final-auditor",
        strategic_thesis="Initial thesis",
    )
    return project, truth, research, snapshot, research_map


def _forged_authorization(snapshot_hash: str, map_hash: str) -> PatchAuthorization:
    return PatchAuthorization.capture(
        patch_id="forged-patch",
        patch_hash=domain_hash("patch", {"id": "forged-patch"}),
        review_id="forged-review",
        review_hash=domain_hash("review", {"id": "forged-review"}),
        critic_id="forged-critic",
        critic_hash=domain_hash("critic", {"id": "forged-critic"}),
        probe_ids=(),
        probe_hashes=(),
        root_claim_snapshot_hash=snapshot_hash,
        source_map_hash=map_hash,
        status="AUTHORIZED",
        scope_validation_passed=False,
        truth_boundary_intact=False,
        invalidated_evidence_refs=(),
        reason="untrusted payload",
        authorized_by="untrusted-payload",
        created_at=utc_now(),
    )


def probe_f005_forgery_matrix() -> dict[str, Any]:
    """Try raw, cloned, serialized, nested, cross-map, and casing attacks."""

    root = Path(tempfile.mkdtemp(prefix="final-reauth-f005-"))
    _, _, research, snapshot, map_a = _research(root, map_id="map-A")
    map_b = research.create_initial_map(
        research_map_id="map-B",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": "O1-B",
                "title": "O1-B",
                "statement": "Resolve O1-B",
                "obligation_kind": "LEMMA",
                "scope": ["O1-B"],
            }
        ],
        created_by="final-auditor",
        strategic_thesis="Independent map thesis",
    )
    forged = _forged_authorization(
        snapshot.claim_snapshot_hash, map_a.research_map_hash
    )
    serialized = PatchAuthorization.from_dict(json.loads(json.dumps(forged.to_dict())))
    cases: list[tuple[str, Any, dict[str, Any]]] = [
        ("A1_raw_AUTHORIZED_flag", forged, {}),
        ("A2_clone_mutated_thesis", replace(forged, reason="different thesis"), {}),
        (
            "A3_clone_altered_scope",
            forged,
            {
                "removed_or_reframed_scope": ("O1",),
                "revision_reason": MapRevisionReason.ARCHITECTURE_PATCH.value,
            },
        ),
        ("A4_cross_map_reuse", forged, {"parent": map_b}),
        ("A5_restart_replay", forged, {}),
        ("A7_serialized_deserialized", serialized, {}),
        ("A8_stripped_binding_envelope", {"authorization": forged.to_dict()}, {}),
        (
            "A11_nested_envelope_confusion",
            {"authority": {"status": "AUTHORIZED", "payload": forged.to_dict()}},
            {},
        ),
        ("A12_alternate_casing", replace(forged, status="authorized"), {}),
        ("A13_thesis_A_against_map_B", forged, {"parent": map_b}),
        (
            "A14_invalid_mutation_target",
            forged,
            {"strategic_thesis": "Forged target thesis"},
        ),
    ]
    observations = []
    for case_id, authority, overrides in cases:
        current = (
            map_a if overrides.get("parent", map_a) is map_a else overrides["parent"]
        )
        parent = overrides.pop("parent", current)
        before = research.load_current_map(parent.research_map_id)
        kwargs = {
            "parent": parent,
            "created_by": "untrusted-payload",
            "revision_reason": overrides.pop(
                "revision_reason", MapRevisionReason.HUMAN_STEERING.value
            ),
            "strategic_thesis": overrides.pop("strategic_thesis", "Forged thesis"),
            "governance_authorization": authority,
            **overrides,
        }
        try:
            research.revise_map(**kwargs)
        except ProjectError as exc:  # expected for every raw/untrusted authority case
            outcome = f"rejected:{type(exc).__name__}: {exc}"
            rejected = True
        else:
            outcome = "accepted"
            rejected = False
        after = research.load_current_map(parent.research_map_id)
        unchanged = after == before
        observations.append(
            {
                "case": case_id,
                "outcome": outcome,
                "map_unchanged": unchanged,
                "passed": rejected and unchanged,
            }
        )
    passed = all(item["passed"] for item in observations)
    return _result(
        "F005-A1-A14",
        "all untrusted authority representations fail closed and no map/thesis mutation occurs",
        "all cases rejected without map mutation"
        if passed
        else "one or more forgery cases accepted or mutated state",
        passed=passed,
        details={"cases": observations},
    )


def _persist_result(root: Path, binding: CrossPlaneExecutionBinding, *, tag: str):
    backend = SQLiteRuntimeBackend(root)
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="O1",
        idempotency_key=f"job-{tag}",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        model="mock",
        reasoning_tier="research",
        payload_hash=domain_hash("payload", {"tag": tag}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(
        attempt["attempt_id"], owner="final-auditor", ttl_seconds=60
    )
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="final-auditor",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    artifact = RuntimeArtifactStore(root).persist_and_register(
        backend,
        {"structured": {"success": True, "high_value": True}, "tag": tag},
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
    return backend, job, result


def probe_f007_restart_and_stale_controls() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="final-reauth-f007-restart-"))
    c1 = domain_hash("claim", {"id": "C1"})
    c2 = domain_hash("claim", {"id": "C2"})
    binding = CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=c1)
    _backend, job, result = _persist_result(
        root / "project", binding, tag="restart-controls"
    )
    no_validator = SQLiteRuntimeBackend(root / "project")
    no_validator.reconcile()
    unaccepted_without_validator = (
        no_validator.get_job(job["logical_job_id"])["accepted_result_id"] is None
    )
    stale = SQLiteRuntimeBackend(root / "project")
    stale.reconcile(
        binding_validator=lambda current: (
            True
            if current is not None and current.root_claim_snapshot_hash == c2
            else "STALE_CLAIM_SNAPSHOT"
        )
    )
    fenced = next(
        row
        for row in stale.list_rows("attempt_results")
        if row["result_id"] == result["result_id"]
    )
    stale_fenced = (
        fenced["authoritative"] == 0
        and stale.get_job(job["logical_job_id"])["accepted_result_id"] is None
    )
    valid_root = root / "valid-project"
    _valid_backend, valid_job, valid_result = _persist_result(
        valid_root, binding, tag="valid-restart"
    )
    valid = SQLiteRuntimeBackend(valid_root)
    valid.reconcile(
        binding_validator=lambda current: (
            current is not None and current.root_claim_snapshot_hash == c1
        )
    )
    valid_accepts = (
        valid.get_job(valid_job["logical_job_id"])["accepted_result_id"]
        == valid_result["result_id"]
    )
    passed = unaccepted_without_validator and stale_fenced and valid_accepts
    return _result(
        "F007-RESTART-CONTROLS",
        "missing validator blocks; stale validator fences; valid validator accepts the exact binding",
        "all three restart outcomes observed" if passed else "restart control mismatch",
        passed=passed,
        details={
            "result_id": result["result_id"],
            "unaccepted_without_validator": unaccepted_without_validator,
            "stale_result": fenced,
            "valid_accepts": valid_accepts,
            "valid_result_id": valid_result["result_id"],
        },
    )


class _MockProviderClient:
    def call(self, **_: Any) -> dict[str, Any]:
        return {"structured": {"success": True, "high_value": True, "authorized": True}}

    def cleanup(self) -> None:
        return None


def probe_f007_partial_binding_widening() -> dict[str, Any]:
    """A current-root but map/session-stripped binding must not be a wildcard."""

    root = Path(tempfile.mkdtemp(prefix="final-reauth-f007-partial-"))
    project = ProjectStore.initialize(root / "project", "partial binding")
    project.add_theorem("T1", "Root", "P holds.")
    orchestrator = ResearchOrchestrator(
        project,
        "T1",
        config_path=MOCK_CONFIG,
        run_id="partial-binding-run",
        worker_count=1,
    )
    orchestrator._ensure_research_plane_ready()
    current = orchestrator._current_execution_binding()
    assert current is not None and current.research_map_id is not None
    partial = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=current.root_claim_snapshot_hash
    )
    orchestrator.model_router.execution_binding = partial
    orchestrator.model_router.execution_binding_validator = (
        orchestrator._validate_execution_binding
    )
    validator_value = orchestrator._validate_execution_binding(partial)
    client = RoutedLLMClient(
        orchestrator.model_router,
        client_factory=lambda *_args, **_kwargs: _MockProviderClient(),
        default_role="worker",
        archive_dir=root / "archive",
        working_dir=root / "working",
    )
    try:
        response = client.call(
            "[Worker role: worker]", "system", label="partial-binding"
        )
        accepted = response.get("runtime", {}).get("accepted") is True
        semantic_payload_reached = (
            response.get("structured", {}).get("high_value") is True
        )
        actual = (
            "partial binding accepted and semantic payload returned"
            if accepted
            else "partial binding rejected"
        )
        error = None
    except (ProjectError, RuntimeConflict, RuntimeResultRejected) as exc:
        accepted = False
        semantic_payload_reached = False
        actual = f"rejected:{type(exc).__name__}: {exc}"
        error = str(exc)
    finally:
        client.cleanup()
    rows = orchestrator.runtime_backend.list_rows("logical_jobs")
    accepted_ids = [
        row["accepted_result_id"] for row in rows if row["accepted_result_id"]
    ]
    passed = (
        not accepted and not semantic_payload_reached and validator_value is not True
    )
    return _result(
        "NF-003-PARTIAL-BINDING",
        "current-domain validator rejects a root-only binding when map/session context is current",
        actual,
        passed=passed,
        details={
            "current_binding": current.to_dict(),
            "partial_binding": partial.to_dict(),
            "validator_value": validator_value,
            "accepted_result_ids": accepted_ids,
            "error": error,
        },
    )


def probe_f007_no_backend_guard() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="final-reauth-f007-no-backend-"))
    router = ModelRouter(
        {"provider": "mock", "model": "mock", "reasoning_effort": "low"},
        require_execution_binding=True,
    )
    client = RoutedLLMClient(
        router,
        client_factory=lambda *_args, **_kwargs: _MockProviderClient(),
        default_role="worker",
        archive_dir=root / "archive",
        working_dir=root / "working",
    )
    try:
        response = client.call("[Worker role: worker]", "system", label="no-backend")
    except RuntimeConflict as exc:
        rejected = True
        actual = f"rejected:{type(exc).__name__}: {exc}"
        details = {"response": None}
    else:
        rejected = False
        actual = "semantic response returned without runtime binding"
        details = {"response_keys": sorted(response), "response": response}
    finally:
        client.cleanup()
    return _result(
        "NF-004-NO-BACKEND-GUARD",
        "require_execution_binding=True fails closed even when no runtime backend is configured",
        actual,
        passed=rejected,
        details=details,
    )


def probe_f002_terminal_rejection() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="final-reauth-f002-"))
    claim = domain_hash("claim", {"id": "late"})
    binding = CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=claim)
    backend = SQLiteRuntimeBackend(root / "project")
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="O1",
        idempotency_key="late-terminal",
        execution_binding=binding,
    )
    response = DurableProviderDispatcher(backend, lease_ttl_seconds=0.001).execute(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        model="mock",
        reasoning_tier="research",
        payload={"high_value": True},
        invoke=lambda: (
            time.sleep(0.02) or {"structured": {"success": True, "high_value": True}}
        ),
        execution_binding=binding,
        binding_validator=lambda current: current == binding,
    )
    result_id = response["runtime"]["result_id"]
    fresh = SQLiteRuntimeBackend(root / "project")
    fresh.reconcile(binding_validator=lambda current: current == binding)
    accepted_after_restart = fresh.get_job(job["logical_job_id"])["accepted_result_id"]
    semantic_calls: list[str] = []
    try:
        fresh.accept_result(
            job["logical_job_id"], binding_validator=lambda current: current == binding
        )
    except RuntimeConflict as exc:
        selection_error = str(exc)
    else:
        selection_error = "accepted unexpectedly"
    try:
        fresh.apply_effect_once(
            logical_job_id=job["logical_job_id"],
            effect_kind="AUDIT_EFFECT",
            semantic_target_type="RESEARCH_MAP",
            semantic_target_id="map-T1",
            source_result_id=result_id,
            apply=lambda _slot: semantic_calls.append("apply"),
            execution_binding=binding,
            binding_validator=lambda current: current == binding,
        )
    except RuntimeConflict as exc:
        effect_error = str(exc)
    else:
        effect_error = "effect prepared unexpectedly"
    try:
        RoutedLLMClient._raise_if_runtime_result_rejected(response)
    except RuntimeResultRejected as exc:
        routed_error = str(exc)
    else:
        routed_error = "routed consumer accepted unexpectedly"
    row = next(
        row
        for row in fresh.list_rows("attempt_results")
        if row["result_id"] == result_id
    )
    passed = (
        response["runtime"]["accepted"] is False
        and response["runtime"]["authoritative"] is False
        and "structured" not in response
        and accepted_after_restart is None
        and selection_error != "accepted unexpectedly"
        and effect_error != "effect prepared unexpectedly"
        and routed_error != "routed consumer accepted unexpectedly"
        and not semantic_calls
        and row["authoritative"] == 0
        and not fresh.list_rows("effect_slots")
    )
    return _result(
        "F002-TERMINAL-REJECTION",
        "rejected payload stays permanently non-consumable across restart, selection, effects, and routed consumption",
        "all terminality checks passed"
        if passed
        else "a rejected payload remained consumable",
        passed=passed,
        details={
            "result_id": result_id,
            "runtime": response["runtime"],
            "attempt_result": row,
            "accepted_after_restart": accepted_after_restart,
            "selection_error": selection_error,
            "effect_error": effect_error,
            "routed_error": routed_error,
            "semantic_calls": semantic_calls,
        },
    )


def main() -> None:
    probes = [
        probe_f005_forgery_matrix(),
        probe_f007_restart_and_stale_controls(),
        probe_f007_partial_binding_widening(),
        probe_f007_no_backend_guard(),
        probe_f002_terminal_rejection(),
    ]
    print(
        json.dumps(
            {"repository": str(REPO_ROOT), "probes": probes},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
