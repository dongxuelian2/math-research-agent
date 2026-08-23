from __future__ import annotations

import json
from pathlib import Path

import pytest

from math_research_agent.research.project import ProjectError, ProjectStore, utc_now
from math_research_agent.research.runtime_backend import SQLiteRuntimeBackend
from math_research_agent.research.runtime_effects import RuntimeEffectCoordinator
from math_research_agent.research.runtime_model import AttemptState
from math_research_agent.research.state_machine import AuditGate
from math_research_agent.research.truth_store import TruthMutationBlocked, TruthStoreFacade
from math_research_agent.research.truth_mutation import (
    TruthMutationIntent,
    capture_artifact_refs,
)


def _project(tmp_path: Path) -> ProjectStore:
    project = ProjectStore.initialize(tmp_path / "project", "Truth mutation", demo=True)
    project.add_theorem(
        "dependency",
        "Dependency",
        "For every n, n = n.",
        status="PROVED",
        claim_type="equality",
    )
    project.add_theorem(
        "target",
        "Target",
        "For every n, n + 0 = n.",
        status="AUDITING",
        dependencies=["dependency"],
        claim_type="equality",
    )
    return project


def _canonical(digest: str = "a" * 64, *, status: str = "RESOLVED_CANONICAL") -> list[dict]:
    return [
        {
            "requirement": {
                "logical_name": "source",
                "canonical_filename": "source.md",
                "purpose": "proof_authority",
            },
            "resolution_status": status,
            "computed_sha256": (f"sha256:{digest}" if status == "RESOLVED_CANONICAL" else None),
            "expected_sha256": f"sha256:{digest}",
            "authority_source": "test",
            "authority_record": {"registry_id": "test"},
        }
    ]


def _passing_gate(snapshot_hash: str) -> AuditGate:
    return AuditGate(
        forward_implication=True,
        converse_if_applicable=True,
        exhaustive_cases=True,
        parameter_ranges=True,
        boundary_cases=True,
        dependencies_valid=True,
        no_counterexample=True,
        auditors_pass=True,
        final_auditor_pass=True,
        computational_evidence_separated=True,
        audited_claim_snapshot_hash=snapshot_hash,
    )


def _artifact(project: ProjectStore) -> Path:
    path = project.root / "runs" / "test" / "audits" / "gate.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"outcome":"PASS"}\n', encoding="utf-8")
    return path


def _promote(
    facade: TruthStoreFacade,
    snapshot,
    artifact: Path,
    *,
    canonical: list[dict] | None = None,
    hook=None,
    prepare_hook=None,
    after_hook=None,
):
    return facade.compare_and_transition(
        "target",
        claim_snapshot=snapshot,
        gate=_passing_gate(snapshot.claim_snapshot_hash),
        actor="Archivist",
        reason="exact audits passed",
        audit_artifacts=[artifact],
        metadata_updates={"proof_file": "reports/target.md"},
        canonical_authority=canonical or _canonical(),
        before_compare_hook=hook,
        after_prepare_hook=prepare_hook,
        after_transition_hook=after_hook,
    )


def test_replay_recovers_receipt_after_crash_between_transition_and_receipt(tmp_path: Path):
    prepared_project = _project(tmp_path / "prepared")
    prepared_facade = TruthStoreFacade(prepared_project)
    prepared_snapshot = prepared_facade.capture_claim_snapshot(
        "target", canonical_authority=_canonical()
    )
    prepared_artifact = _artifact(prepared_project)

    def crash_after_prepare() -> None:
        raise RuntimeError("injected crash after prepared evidence")

    with pytest.raises(RuntimeError, match="after prepared"):
        _promote(
            prepared_facade,
            prepared_snapshot,
            prepared_artifact,
            prepare_hook=crash_after_prepare,
        )
    assert prepared_project.load_theorem("target")["status"] == "AUDITING"
    recovered_from_prepare = _promote(
        prepared_facade,
        prepared_snapshot,
        prepared_artifact,
    )
    assert recovered_from_prepare[0]["status"] == "PROVED"

    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())
    audit_artifact = _artifact(project)

    def crash_after_transition() -> None:
        raise RuntimeError("injected crash after theorem transition")

    with pytest.raises(RuntimeError, match="injected crash"):
        _promote(
            facade,
            snapshot,
            audit_artifact,
            after_hook=crash_after_transition,
        )

    crashed = project.load_theorem("target")
    assert crashed["status"] == "PROVED"
    assert len(crashed["status_history"]) == 2
    prepared = list((project.root / "truth" / "mutations" / "prepared").glob("*.json"))
    assert len(prepared) == 1
    assert not list((project.root / "truth" / "mutations" / "receipts").glob("*.json"))

    theorem, resulting_snapshot, intent, receipt = _promote(
        facade,
        snapshot,
        audit_artifact,
    )
    assert theorem["status"] == "PROVED"
    assert resulting_snapshot.captured_status == "PROVED"
    assert receipt.mutation_id == intent.mutation_id
    assert receipt.applied_at == theorem["status_history"][-1]["at"]
    assert len(project.load_theorem("target")["status_history"]) == 2


def test_success_writes_intent_then_receipt_with_before_after_hashes(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())
    audit_artifact = _artifact(project)
    gate = _passing_gate(snapshot.claim_snapshot_hash)
    intent_preview = TruthMutationIntent.capture(
        theorem_id="target",
        from_status=snapshot.captured_status,
        requested_to_status="PROVED",
        claim_snapshot_hash=snapshot.claim_snapshot_hash,
        assertion_identity_hash=snapshot.assertion_identity_hash,
        audited_claim_snapshot_hash=gate.audited_claim_snapshot_hash,
        trust_policy_fingerprint=snapshot.trust_policy_fingerprint,
        audit_artifacts=capture_artifact_refs([audit_artifact], project_root=project.root),
        requested_by="Archivist",
        reason="exact audits passed",
        created_at=utc_now(),
    )
    runtime = SQLiteRuntimeBackend(project.root)
    job = runtime.create_logical_job(
        job_kind="AUDIT",
        semantic_target="target",
        idempotency_key="truth-runtime-e2e",
        claim_snapshot_hash=snapshot.claim_snapshot_hash,
    )
    attempt, _ = runtime.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        payload_hash="sha256:truth-audit",
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
    artifact = runtime.register_artifact(
        audit_artifact,
        artifact_kind="AUDIT_GATE",
        producer_attempt_id=attempt["attempt_id"],
    )
    runtime_result = runtime.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="PASS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    runtime.accept_result(job["logical_job_id"])
    applied = {}

    def apply_truth(_effect_slot_id):
        value = _promote(facade, snapshot, audit_artifact)
        applied["value"] = value
        return {"receipt": value[3]}

    coordinator = RuntimeEffectCoordinator(runtime)
    slot, _ = coordinator.apply_truth_mutation(
        logical_job_id=job["logical_job_id"],
        source_result_id=runtime_result["result_id"],
        theorem_id="target",
        claim_snapshot_hash=snapshot.claim_snapshot_hash,
        apply=apply_truth,
        receipt_path=facade.receipt_path,
        mutation_id=intent_preview.mutation_id,
        load_receipt=facade.load_mutation_receipt,
    )
    theorem, resulting, intent, receipt = applied["value"]

    assert theorem["status"] == "PROVED"
    assert resulting.captured_status == "PROVED"
    assert intent.claim_snapshot_hash == snapshot.claim_snapshot_hash
    assert receipt.previous_status == "AUDITING"
    assert receipt.resulting_status == "PROVED"
    assert receipt.project_record_hash_before != receipt.project_record_hash_after
    assert facade.load_mutation_intent(intent.mutation_id) == intent
    assert facade.load_mutation_receipt(intent.mutation_id) == receipt
    transition_count = len(project.load_theorem("target")["status_history"])
    replay = _promote(facade, snapshot, audit_artifact)
    assert replay[2] == intent
    assert replay[3] == receipt
    assert len(project.load_theorem("target")["status_history"]) == transition_count
    replay_slot, _ = coordinator.apply_truth_mutation(
        logical_job_id=job["logical_job_id"],
        source_result_id=runtime_result["result_id"],
        theorem_id="target",
        claim_snapshot_hash=snapshot.claim_snapshot_hash,
        apply=lambda _: pytest.fail("Truth mutation replay called the domain apply function"),
        receipt_path=facade.receipt_path,
        mutation_id=intent.mutation_id,
        load_receipt=facade.load_mutation_receipt,
    )
    assert replay_slot["effect_slot_id"] == slot["effect_slot_id"]
    assert len(runtime.list_rows("effect_slots")) == 1


def test_t10_root_identity_race_blocks_without_receipt(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())

    def mutate_root() -> None:
        theorem = project.load_theorem("target")
        theorem["statement"] = "For every n, n + 1 = n."
        project.update_theorem(theorem)

    with pytest.raises(TruthMutationBlocked) as caught:
        _promote(facade, snapshot, _artifact(project), hook=mutate_root)

    assert facade.intent_path(caught.value.mutation_id).is_file()
    assert not facade.receipt_path(caught.value.mutation_id).exists()
    assert project.load_theorem("target")["status"] == "AUDITING"
    blocked = json.loads(caught.value.blocked_path.read_text(encoding="utf-8"))
    assert blocked["status"] == "ASSERTION_CHANGED"


def test_t11_dependency_race_requires_revalidation_and_never_promotes(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())

    with pytest.raises(TruthMutationBlocked) as caught:
        _promote(
            facade,
            snapshot,
            _artifact(project),
            hook=lambda: project.transition(
                "dependency", "FROZEN", actor="Human", reason="authority withdrawn"
            ),
        )

    blocked = json.loads(caught.value.blocked_path.read_text(encoding="utf-8"))
    assert blocked["status"] == "DEPENDENCY_CHANGED"
    assert not facade.receipt_path(caught.value.mutation_id).exists()
    assert project.load_theorem("target")["status"] == "AUDITING"


def test_t12_authority_hash_change_blocks_after_intent(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())

    with pytest.raises(TruthMutationBlocked) as caught:
        _promote(
            facade,
            snapshot,
            _artifact(project),
            canonical=_canonical(status="HASH_MISMATCH"),
        )

    blocked = json.loads(caught.value.blocked_path.read_text(encoding="utf-8"))
    assert blocked["status"] == "UNRESOLVABLE_AUTHORITY"
    assert facade.intent_path(caught.value.mutation_id).is_file()
    assert not facade.receipt_path(caught.value.mutation_id).exists()


def test_trust_policy_race_blocks_but_metadata_race_does_not(tmp_path: Path):
    blocked_project = _project(tmp_path / "blocked")
    blocked_facade = TruthStoreFacade(blocked_project)
    blocked_snapshot = blocked_facade.capture_claim_snapshot(
        "target", canonical_authority=_canonical()
    )

    def mutate_policy() -> None:
        metadata = blocked_project.load_project()
        metadata["truth_policy_version"] = "P2"
        blocked_project.save_project(metadata)

    with pytest.raises(TruthMutationBlocked) as caught:
        _promote(
            blocked_facade,
            blocked_snapshot,
            _artifact(blocked_project),
            hook=mutate_policy,
        )
    blocked = json.loads(caught.value.blocked_path.read_text(encoding="utf-8"))
    assert blocked["status"] == "TRUST_POLICY_CHANGED"

    allowed_project = _project(tmp_path / "allowed")
    allowed_facade = TruthStoreFacade(allowed_project)
    allowed_snapshot = allowed_facade.capture_claim_snapshot(
        "target", canonical_authority=_canonical()
    )

    def mutate_title() -> None:
        theorem = allowed_project.load_theorem("target")
        theorem["title"] = "Presentation-only rename"
        allowed_project.update_theorem(theorem)

    theorem, _, _, receipt = _promote(
        allowed_facade,
        allowed_snapshot,
        _artifact(allowed_project),
        hook=mutate_title,
    )
    assert theorem["status"] == "PROVED"
    assert receipt.resulting_status == "PROVED"


def test_promotion_rejects_audit_bound_to_another_snapshot(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())
    wrong_hash = "sha256:" + "f" * 64

    with pytest.raises(ProjectError, match="exact promotion ClaimSnapshot"):
        facade.compare_and_transition(
            "target",
            claim_snapshot=snapshot,
            gate=_passing_gate(wrong_hash),
            actor="Archivist",
            reason="wrong binding",
            audit_artifacts=[_artifact(project)],
            canonical_authority=_canonical(),
        )

    assert project.load_theorem("target")["status"] == "AUDITING"
