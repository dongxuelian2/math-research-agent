"""Deterministic probes for the independent v3.2 pre-root re-audit.

This file is audit instrumentation only.  It creates temporary projects and
does not modify production sources, frozen audit evidence, or repair evidence.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from openprover.math_research.architecture_patch import (
    ArchitecturePatch,
    PatchAuthorization,
    PatchObligationAddition,
    ScopeTransfer,
)
from openprover.math_research.project import ProjectStore, utc_now
from openprover.math_research.research_map import MapRevisionReason
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.routing import ModelRouter, RoutedLLMClient
from openprover.math_research.runtime_artifacts import RuntimeArtifactStore
from openprover.math_research.runtime_backend import SQLiteRuntimeBackend
from openprover.math_research.runtime_bindings import CrossPlaneExecutionBinding
from openprover.math_research.runtime_model import AttemptState
from openprover.math_research.schemas import (
    PipelineResultSchema,
    parse_structured_response,
)
from openprover.math_research.truth_identity import domain_hash
from openprover.math_research.truth_store import TruthStoreFacade

REPO_ROOT = Path(__file__).resolve().parents[3]


def _result(probe_id: str, status: str, **details: Any) -> dict[str, Any]:
    return {"probe_id": probe_id, "status": status, "details": details}


def _project_with_map(root: Path, obligation_ids: tuple[str, ...] = ("O1",)):
    project = ProjectStore.initialize(root / "project", "pre-root re-audit")
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
                "obligation_kind": "LEMMA",
                "scope": [obligation_id],
            }
            for obligation_id in obligation_ids
        ],
        created_by="audit",
        strategic_thesis="Initial thesis",
    )
    return project, truth, research, snapshot, research_map


def _close_valid_session(
    research: ResearchStoreFacade,
    session_id: str,
    artifacts_root: Path,
) -> None:
    artifacts_root.mkdir(parents=True, exist_ok=True)
    candidate = artifacts_root / "candidate.md"
    verifier = artifacts_root / "verifier.json"
    audit = artifacts_root / "audit.json"
    candidate.write_text("candidate", encoding="utf-8")
    verifier.write_text('{"verdict":"CORRECT"}', encoding="utf-8")
    audit.write_text('{"outcome":"PASS"}', encoding="utf-8")
    research.close_tactical_session(
        session_id,
        execution_status="COMPLETED",
        raw_artifacts=tuple(
            {"path": path, "artifact_kind": kind, "producer": "audit"}
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
        closed_by="audit",
    )


def probe_rx_compound_stale() -> dict[str, Any]:
    """C1/map-v1 plus expiry: the late result must remain fenced on restart."""

    root = Path(tempfile.mkdtemp(prefix="pre-root-rx-compound-"))
    project, _, _research, snapshot, research_map = _project_with_map(root)
    map_hash = research_map.research_map_hash
    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        research_map_id=research_map.research_map_id,
        research_map_version=research_map.version,
        research_map_hash=map_hash,
        research_obligation_id="O1",
    )
    backend = SQLiteRuntimeBackend(project.root)
    job = backend.create_logical_job(
        job_kind="TACTICAL_SESSION",
        semantic_target="O1",
        idempotency_key="rx-compound-stale",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash=domain_hash("payload", {"probe": "rx-compound-stale"}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="audit", ttl_seconds=0.001)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="audit",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    time.sleep(0.02)
    artifact = RuntimeArtifactStore(project.root).persist_and_register(
        backend,
        {"claim": "C1", "map_version": 1, "high_value": True},
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
    actions = SQLiteRuntimeBackend(project.root).reconcile()
    current = SQLiteRuntimeBackend(project.root).get_job(job["logical_job_id"])
    return _result(
        "RX-COMPOUND-STALE",
        "PASS"
        if not result["authoritative"]
        and current["accepted_result_id"] is None
        and not SQLiteRuntimeBackend(project.root).list_rows("effect_slots")
        else "FAIL",
        claim_transition="C1 -> C2 (simulated before late result)",
        map_transition="v1 -> v2/v3 (simulated before late result)",
        lease_expired=True,
        ingestion_state=result["ingestion_state"],
        authoritative=bool(result["authoritative"]),
        accepted_result_id=current["accepted_result_id"],
        effect_slots=len(SQLiteRuntimeBackend(project.root).list_rows("effect_slots")),
        reconciliation_actions=[item["action"] for item in actions],
    )


def probe_rx_restart_recovery_bypasses_domain_guard() -> dict[str, Any]:
    """A durable C1 result is selected after restart without a C2/map validator."""

    root = Path(tempfile.mkdtemp(prefix="pre-root-rx-restart-"))
    project, truth, research, snapshot, research_map = _project_with_map(root)
    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        research_map_id=research_map.research_map_id,
        research_map_version=research_map.version,
        research_map_hash=research_map.research_map_hash,
        research_obligation_id="O1",
    )
    backend = SQLiteRuntimeBackend(project.root)
    job = backend.create_logical_job(
        job_kind="TACTICAL_SESSION",
        semantic_target="O1",
        idempotency_key="rx-restart-stale",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash=domain_hash("payload", {"probe": "rx-restart-stale"}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="audit", ttl_seconds=60)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="audit",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    artifact = RuntimeArtifactStore(project.root).persist_and_register(
        backend,
        {"claim": "C1", "map_version": 1, "high_value": True},
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
    _, current_map = research.add_obligation(
        research_map.research_map_id,
        obligation_id="O2",
        title="O2",
        statement="Resolve O2",
        obligation_kind="LEMMA",
        created_by="audit",
    )
    theorem = project.load_theorem("T1")
    theorem["statement"] = "For every n, Q(n)."
    project.update_theorem(theorem)
    current_snapshot = truth.capture_claim_snapshot("T1")
    actions = SQLiteRuntimeBackend(project.root).reconcile()
    restarted = SQLiteRuntimeBackend(project.root).get_job(job["logical_job_id"])
    return _result(
        "RX-RESTART-STALE-DOMAIN",
        "FAIL" if restarted["accepted_result_id"] == result["result_id"] else "PASS",
        persisted_binding_root=binding.root_claim_snapshot_hash,
        current_claim_snapshot=current_snapshot.claim_snapshot_hash,
        persisted_map_version=binding.research_map_version,
        current_map_version=current_map.version,
        result_authoritative_before_restart=bool(result["authoritative"]),
        accepted_result_id_after_restart=restarted["accepted_result_id"],
        reconciliation_actions=[item["action"] for item in actions],
        effect_slots=len(SQLiteRuntimeBackend(project.root).list_rows("effect_slots")),
    )


def probe_forged_thesis_authorization() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="pre-root-governance-forged-"))
    _project, _, research, snapshot, research_map = _project_with_map(root)
    forged = PatchAuthorization.capture(
        patch_id="forged-patch",
        patch_hash=domain_hash("patch", {"id": "forged-patch"}),
        review_id="forged-review",
        review_hash=domain_hash("review", {"id": "forged-review"}),
        critic_id="forged-critic",
        critic_hash=domain_hash("critic", {"id": "forged-critic"}),
        probe_ids=(),
        probe_hashes=(),
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        source_map_hash=research_map.research_map_hash,
        status="AUTHORIZED",
        scope_validation_passed=False,
        truth_boundary_intact=False,
        invalidated_evidence_refs=(),
        reason="forged audit object",
        authorized_by="untrusted-caller",
        created_at=utc_now(),
    )
    revised = research.revise_map(
        research_map,
        created_by="untrusted-caller",
        revision_reason=MapRevisionReason.HUMAN_STEERING.value,
        strategic_thesis="Forged thesis",
        governance_authorization=forged,
    )
    return _result(
        "GOV-THESIS-FORGED-AUTH",
        "FAIL" if revised.strategic_thesis == "Forged thesis" else "PASS",
        accepted=True,
        prior_thesis=research_map.strategic_thesis,
        resulting_thesis=revised.strategic_thesis,
        authorization_status=forged.status,
        scope_validation_passed=forged.scope_validation_passed,
        truth_boundary_intact=forged.truth_boundary_intact,
    )


def probe_late_payload_authority_leak() -> dict[str, Any]:
    """The routed consumer can parse a provider payload fenced by the runtime."""

    import openprover.math_research.runtime_dispatch as dispatch_module

    class SlowClient:
        def call(self, **_: Any) -> dict[str, Any]:
            time.sleep(0.03)
            return {
                "structured": {
                    "schema_version": 3,
                    "verdict": "CORRECT",
                    "success": True,
                    "high_value": True,
                    "all_required_gates": True,
                }
            }

    root = Path(tempfile.mkdtemp(prefix="pre-root-late-payload-"))
    backend = SQLiteRuntimeBackend(root / "project")
    dispatcher = dispatch_module.DurableProviderDispatcher
    dispatch_module.DurableProviderDispatcher = lambda item: dispatcher(
        item, lease_ttl_seconds=0.001
    )
    try:
        config = json.loads((REPO_ROOT / "configs" / "models.mock.json").read_text())
        router = ModelRouter(
            config,
            state_path=root / "routing.json",
            runtime_backend=backend,
            runtime_scope="late-payload-audit",
        )
        client = RoutedLLMClient(
            router,
            client_factory=lambda *_args, **_kwargs: SlowClient(),
            default_role="constructive",
            archive_dir=root / "archive",
            working_dir=root / "work",
        )
        response = client.call(
            "[Worker role: constructive]\n[Obligation ID: O1]",
            "bounded worker",
            label="late-payload",
            response_schema=PipelineResultSchema,
        )
        parsed = parse_structured_response(response, PipelineResultSchema)
    finally:
        dispatch_module.DurableProviderDispatcher = dispatcher
    return _result(
        "RX-LATE-PAYLOAD-AUTHORITY",
        "FAIL"
        if not response["runtime"]["authoritative"] and parsed.high_value
        else "PASS",
        runtime_accepted=response["runtime"]["accepted"],
        runtime_authoritative=response["runtime"]["authoritative"],
        parsed_high_value=parsed.high_value,
        parsed_success=parsed.success,
        effect_slots=len(backend.list_rows("effect_slots")),
    )


def probe_no_scope_loss_stale_replay() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="pre-root-no-scope-"))
    project, _, research, snapshot, v1 = _project_with_map(root, ("O1", "O2", "O3"))
    sessions: dict[str, str] = {}
    for obligation_id in ("O1", "O2", "O3"):
        directive = research.create_directive(
            v1.research_map_id,
            obligation_id,
            tactical_goal=f"Resolve {obligation_id}",
            allowed_scope=(obligation_id,),
            created_by="audit",
        )
        session = research.bind_tactical_session(
            directive.directive_id,
            execution_run_id=f"run-{obligation_id}",
            execution_status="RUNNING",
        )
        _close_valid_session(
            research,
            session.tactical_session_id,
            project.root / "runs" / obligation_id,
        )
        sessions[obligation_id] = session.tactical_session_id

    additions = (
        PatchObligationAddition.capture(
            obligation_id="N1",
            title="N1",
            statement="Resolve N1",
            obligation_kind="OBSTRUCTION",
            scope=("O1", "O2"),
        ),
        PatchObligationAddition.capture(
            obligation_id="N2",
            title="N2",
            statement="Resolve N2",
            obligation_kind="OBSTRUCTION",
            scope=("O3",),
        ),
    )
    transfers = (
        ScopeTransfer.capture(
            source_obligation_ids=("O1",),
            target_obligation_ids=("N1",),
            disposition="SUPERSEDED",
            reason="O1 -> N1",
            evidence_refs=("audit-transfer",),
        ),
        ScopeTransfer.capture(
            source_obligation_ids=("O2",),
            target_obligation_ids=("N1",),
            disposition="SUPERSEDED",
            reason="O2 -> N1",
            evidence_refs=("audit-transfer",),
        ),
        ScopeTransfer.capture(
            source_obligation_ids=("O3",),
            target_obligation_ids=("N2",),
            disposition="SUPERSEDED",
            reason="O3 -> N2",
            evidence_refs=("audit-transfer",),
        ),
    )
    patch = ArchitecturePatch.capture(
        source_map_id=v1.research_map_id,
        source_map_version=v1.version,
        source_map_hash=v1.research_map_hash,
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        operation_kinds=("REPLACE_PARTITION",),
        affected_obligation_ids=("O1", "O2", "O3"),
        additions=additions,
        scope_transfers=transfers,
        route_memory_changes=(),
        structural_thesis_change="",
        removed_or_reframed_scope=("old O1/O2/O3 partition",),
        justification="audit-only governed reframe",
        review_id="audit-review",
        review_hash=domain_hash("review", {"id": "audit-review"}),
        probe_ids=(),
        probe_hashes=(),
        evidence_refs=("audit-transfer",),
        expected_structural_gain="finite partition",
        proposed_by="audit",
        created_at=utc_now(),
    )
    authorization = PatchAuthorization.capture(
        patch_id=patch.patch_id,
        patch_hash=patch.patch_hash,
        review_id=patch.review_id,
        review_hash=patch.review_hash,
        critic_id="audit-critic",
        critic_hash=domain_hash("critic", {"id": "audit-critic"}),
        probe_ids=(),
        probe_hashes=(),
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        source_map_hash=v1.research_map_hash,
        status="AUTHORIZED",
        scope_validation_passed=True,
        truth_boundary_intact=True,
        invalidated_evidence_refs=(),
        reason="audit-only typed transfer",
        authorized_by="audit",
        created_at=utc_now(),
    )
    reformed = research.apply_governed_reframe(patch, authorization, applied_by="audit")
    replay_results = [
        research.resolve_session_closure(sessions[item], recorded_by="late-replay")[0]
        for item in ("O1", "O2", "O3")
    ]
    duplicate_first, duplicate_second = (
        research.resolve_session_closure(sessions["O3"], recorded_by="duplicate")[0],
        research.resolve_session_closure(sessions["O3"], recorded_by="duplicate")[0],
    )
    after = research.load_current_map(v1.research_map_id)
    statuses = [item.status for item in replay_results]
    pass_condition = (
        all(status == "STALE_SESSION_CLOSURE" for status in statuses)
        and duplicate_first.status == "STALE_SESSION_CLOSURE"
        and duplicate_second.status == "STALE_SESSION_CLOSURE"
        and after.version == reformed.version == 2
        and all(after.obligation_ref(item).disposition == "SUPERSEDED" for item in ("O1", "O2", "O3"))
        and all(after.obligation_ref(item).disposition == "OPEN" for item in ("N1", "N2"))
    )
    return _result(
        "NO-SCOPE-STALE-REPLAY",
        "PASS" if pass_condition else "FAIL",
        replay_statuses=statuses,
        duplicate_statuses=[duplicate_first.status, duplicate_second.status],
        map_version_before=v1.version,
        map_version_after=after.version,
        dispositions={
            item: after.obligation_ref(item).disposition
            for item in ("O1", "O2", "O3", "N1", "N2")
        },
    )


def main() -> None:
    probes = (
        probe_rx_compound_stale(),
        probe_rx_restart_recovery_bypasses_domain_guard(),
        probe_forged_thesis_authorization(),
        probe_late_payload_authority_leak(),
        probe_no_scope_loss_stale_replay(),
    )
    print(json.dumps(probes, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
