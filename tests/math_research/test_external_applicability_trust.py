from __future__ import annotations

import hashlib
import json

import pytest

from math_research_agent.research.literature import (
    ExternalAuthorityRegistry,
    assumption_snapshot_hash,
)
from math_research_agent.research.pipelines import AsyncDAGScheduler
from math_research_agent.research.project import ProjectError


def _verified_source(tmp_path, authority_id="ext-source"):
    root = tmp_path / authority_id
    root.mkdir(parents=True, exist_ok=True)
    text = "If H, then C."
    source = root / "source.txt"
    source.write_bytes(text.encode())
    source_hash = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    extracted = root / "source-text.txt"
    extracted.write_bytes(text.encode())
    text_hash = "sha256:" + hashlib.sha256(extracted.read_bytes()).hexdigest()
    statement_hash = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    extraction = {
        "schema_version": 1,
        "source_artifact_sha256": source_hash,
        "text_artifact_sha256": text_hash,
        "extractions": [
            {
                "extraction_id": "span-0-13",
                "theorem_label": "Theorem 1",
                "location": "p. 1",
                "span_start": 0,
                "span_end": 13,
                "raw_extracted_text": text,
                "normalized_extracted_text": text,
                "extracted_statement_sha256": statement_hash,
            }
        ],
    }
    extraction_path = root / "THEOREM_EXTRACTION.json"
    extraction_path.write_text(json.dumps(extraction, sort_keys=True), encoding="utf-8")
    extraction_hash = "sha256:" + hashlib.sha256(extraction_path.read_bytes()).hexdigest()
    registry = ExternalAuthorityRegistry(root)
    record = registry.register(
        {
            "authority_id": authority_id,
            "title": "Retrieved theorem fixture",
            "authors": ["A. Author"],
            "year": 2026,
            "source": "Fixture Journal",
            "DOI_or_stable_identifier": f"doi:10.1000/{authority_id}",
            "version": "published",
            "theorem_number": "Theorem 1",
            "page_or_section": "p. 1",
            "exact_statement": text,
            "normalized_statement": text,
            "hypotheses": ["H"],
            "notation_map": {},
            "retrieval_source": "https://example.org/public-source",
            "retrieved_at": "2026-08-13T00:00:00Z",
            "reader_verdict": "THEOREM_EXTRACTED",
            "authority_verifier_verdict": "PENDING",
            "used_by_obligations": [],
            "source_type": "published_version",
            "content_scope": "THEOREM_PAGE",
            "retrieved_content_path": "source.txt",
            "retrieved_content_sha256": source_hash,
            "text_artifact_path": "source-text.txt",
            "text_artifact_sha256": text_hash,
            "extraction_artifact_path": "THEOREM_EXTRACTION.json",
            "extraction_artifact_sha256": extraction_hash,
            "extraction_id": "span-0-13",
            "span_start": 0,
            "span_end": 13,
            "extracted_statement_sha256": statement_hash,
            "extractor_version": "test-v1",
        }
    )
    verified = registry.verify(
        record["authority_id"],
        {
            "verdict": "VERIFIED_SOURCE_THEOREM",
            "source_identity_match": True,
            "bibliographic_metadata_match": True,
            "claimed_source_type": "published_version",
        },
    )
    assert verified["status"] == "VERIFIED_SOURCE_THEOREM"
    return registry, verified


def _reconstruction(
    authority,
    *,
    obligation_id="O",
    target="If H, then C.",
    assumptions=None,
    mapping_status="PROVED",
    direction_status="PROVED",
    exception_status="NOT_APPLICABLE",
    call_id="call-reconstructor",
    lemmas=None,
):
    assumptions = assumptions or []
    lemmas = lemmas or []
    return {
        "obligation_id": obligation_id,
        "authority_id": authority["authority_id"],
        "current_target": target,
        "current_assumptions": assumptions,
        "external_statement": authority["exact_statement"],
        "external_hypotheses": ["H"],
        "notation_map": {"paper.H": "project.H"},
        "hypothesis_mapping": [
            {
                "external_hypothesis": "H",
                "satisfied_by": "the target carries H as its antecedent",
                "status": mapping_status,
                "evidence": ["exact conditional statement comparison"],
            }
        ],
        "conclusion_mapping": {
            "external_conclusion": "C",
            "target": target,
            "bridge_steps": ["identity"],
            "status": "PROVED",
        },
        "exception_analysis": {
            "excluded_cases": [],
            "analysis": "no exception in source statement",
            "status": exception_status,
        },
        "direction_analysis": {
            "direction": "external conclusion => target",
            "analysis": "same implication",
            "status": direction_status,
        },
        "normalization_analysis": {"analysis": "whitespace only", "status": "PROVED"},
        "required_local_lemmas": [],
        "authorized_local_lemmas": lemmas,
        "unresolved_conditions": [],
        "reconstructor_call_id": call_id,
        "reconstructor_model": "sol-high",
        "reconstructor_tier": "research",
        "assumption_snapshot_hash": assumption_snapshot_hash(
            obligation_id,
            target,
            assumptions,
            lemmas,
        ),
    }


def _verify(registry, candidate, verdict="APPLICABLE", call_id="call-verifier"):
    return registry.verify_applicability(
        candidate["applicability_id"],
        {
            "verdict": verdict,
            "detail": "independent structured review",
            "verifier_call_id": call_id,
            "verifier_model": "sol-high",
            "verifier_tier": "research",
        },
    )


def _scheduler(tmp_path, *, dual=False, context=None):
    scheduler = AsyncDAGScheduler(
        state_path=tmp_path / "pipeline_state.json",
        config={"routing": {"allow_dual_track": True}},
    )
    scheduler.add_obligation(
        "O",
        target_statement="If H, then C.",
        literature_first=True,
        dual_track=dual,
        context=context,
    )
    return scheduler


def _source_found(scheduler):
    scheduler.apply_literature_result(
        "O",
        verdict="EXACT_RESULT_FOUND",
        authority_status="VERIFIED_SOURCE_THEOREM",
    )
    return scheduler.snapshot()


def _complete_applicability(scheduler, *, applicable=True):
    reconstruction = next(
        task
        for task in scheduler.snapshot()["tasks"].values()
        if task["obligation_id"] == "O" and task["role"] == "reconstruction"
    )
    scheduler.dispatch_window({"proof": 0, "literature": 0, "verification": 10})
    snapshot_hash = scheduler.applicability_context("O")["assumption_snapshot_hash"]
    scheduler.complete_task(
        reconstruction["task_id"],
        {
            "verdict": "APPLICABILITY_CANDIDATE",
            "applicability_id": "app-test",
            "assumption_snapshot_hash": snapshot_hash,
            "result_artifact": "reconstruction.json",
        },
    )
    verifier = next(
        task
        for task in scheduler.dispatch_window({"proof": 0, "literature": 0, "verification": 10})[
            "verification"
        ]
        if task["obligation_id"] == "O" and task["role"] == "theorem_verifier"
    )
    scheduler.complete_task(
        verifier["task_id"],
        {
            "verdict": "APPLICABLE" if applicable else "HYPOTHESIS_MISMATCH",
            "authority_status": "APPLICABLE_EXTERNAL_AUTHORITY"
            if applicable
            else "APPLICABILITY_REJECTED",
            "applicability_status": "APPLICABLE_EXTERNAL_AUTHORITY"
            if applicable
            else "APPLICABILITY_REJECTED",
            "applicability_id": "app-test",
            "assumption_snapshot_hash": snapshot_hash,
            "deterministic_applicability_promotion": applicable,
        },
    )


def test_source_theorem_does_not_close_obligation(tmp_path):
    scheduler = _scheduler(tmp_path)
    snapshot = _source_found(scheduler)
    assert snapshot["obligations"]["O"]["status"] != "CLOSED"
    assert snapshot["obligations"]["O"]["authority_status"] == "VERIFIED_SOURCE_THEOREM"


def test_missing_applicability_defaults_fail_closed(tmp_path):
    scheduler = _scheduler(tmp_path)
    _source_found(scheduler)
    verifier = scheduler.create_task("verification", "O", role="theorem_verifier")
    scheduler.dispatch_window({"proof": 0, "literature": 0, "verification": 10})
    scheduler.complete_task(verifier["task_id"], {"verdict": "CORRECT", "all_required_gates": True})
    assert scheduler.snapshot()["obligations"]["O"]["status"] != "CLOSED"


def test_context_true_flags_cannot_bypass_applicability(tmp_path):
    scheduler = _scheduler(
        tmp_path,
        context={
            "hypotheses_match": True,
            "implication_direction_match": True,
            "exception_check_pass": True,
        },
    )
    snapshot = _source_found(scheduler)
    obligation = snapshot["obligations"]["O"]
    assert obligation.get("applicability_status") != "APPLICABLE_EXTERNAL_AUTHORITY"
    assert obligation["status"] != "CLOSED"


def test_applicability_positive_exact_match(tmp_path):
    registry, authority = _verified_source(tmp_path)
    candidate = registry.register_applicability_reconstruction(_reconstruction(authority))
    promoted = _verify(registry, candidate)
    assert promoted["status"] == "APPLICABLE_EXTERNAL_AUTHORITY"


@pytest.mark.parametrize(
    "field,value,verdict",
    [
        ("mapping_status", "FAILED", "HYPOTHESIS_MISMATCH"),
        ("direction_status", "FAILED", "WRONG_DIRECTION"),
        ("exception_status", "FAILED", "EXCEPTION_MISMATCH"),
    ],
)
def test_applicability_rejects_semantic_mismatch(tmp_path, field, value, verdict):
    registry, authority = _verified_source(tmp_path, f"ext-{field}")
    candidate = registry.register_applicability_reconstruction(
        _reconstruction(authority, **{field: value})
    )
    rejected = _verify(registry, candidate, verdict=verdict)
    assert rejected["status"] == "APPLICABILITY_REJECTED"


def test_applicability_hypothesis_mismatch(tmp_path):
    test_applicability_rejects_semantic_mismatch(
        tmp_path, "mapping_status", "FAILED", "HYPOTHESIS_MISMATCH"
    )


def test_applicability_wrong_direction(tmp_path):
    test_applicability_rejects_semantic_mismatch(
        tmp_path, "direction_status", "FAILED", "WRONG_DIRECTION"
    )


def test_applicability_exception_mismatch(tmp_path):
    test_applicability_rejects_semantic_mismatch(
        tmp_path, "exception_status", "FAILED", "EXCEPTION_MISMATCH"
    )


def test_applicability_requires_independent_verifier(tmp_path):
    registry, authority = _verified_source(tmp_path)
    candidate = registry.register_applicability_reconstruction(_reconstruction(authority))
    rejected = _verify(registry, candidate, call_id="call-reconstructor")
    assert rejected["status"] == "APPLICABILITY_REJECTED"
    assert "independent" in " ".join(rejected["applicability_verification_errors"])


def test_same_theorem_different_obligation_has_separate_applicability(tmp_path):
    registry, authority = _verified_source(tmp_path)
    first = registry.register_applicability_reconstruction(
        _reconstruction(authority, obligation_id="A")
    )
    second = registry.register_applicability_reconstruction(
        _reconstruction(authority, obligation_id="B")
    )
    assert first["applicability_id"] != second["applicability_id"]


def test_assumption_snapshot_change_invalidates_applicability(tmp_path):
    registry, authority = _verified_source(tmp_path)
    assumptions = [{"id": "H", "statement": "H", "status": "VERIFIED"}]
    candidate = registry.register_applicability_reconstruction(
        _reconstruction(authority, assumptions=assumptions)
    )
    assert _verify(registry, candidate)["status"] == "APPLICABLE_EXTERNAL_AUTHORITY"
    with pytest.raises(ProjectError, match="not applicable"):
        registry.require_applicable(
            authority["authority_id"],
            "O",
            "If H, then C.",
            assumptions + [{"id": "K", "statement": "K", "status": "VERIFIED"}],
            [],
        )


def test_successor_does_not_reuse_stale_applicability(tmp_path):
    scheduler = _scheduler(
        tmp_path,
        context={
            "authorized_assumptions": [{"id": "H", "statement": "H", "status": "VERIFIED"}],
        },
    )
    _source_found(scheduler)
    _complete_applicability(scheduler)
    state_path = tmp_path / "pipeline_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["obligations"]["O"]["context"]["authorized_assumptions"].append(
        {"id": "K", "statement": "K", "status": "VERIFIED"}
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    resumed = AsyncDAGScheduler(state_path=state_path)
    obligation = resumed.snapshot()["obligations"]["O"]
    assert obligation["applicability_status"] == "NEEDS_REVALIDATION"
    assert obligation["status"] != "CLOSED"


def test_dual_track_not_cancelled_on_source_theorem_only(tmp_path):
    scheduler = _scheduler(tmp_path, dual=True)
    snapshot = _source_found(scheduler)
    proof_id = snapshot["dual_tracks"]["O"]["speculative_proof_task_id"]
    assert snapshot["tasks"][proof_id]["status"] in {"READY", "ACTIVE"}


def test_dual_track_cancelled_after_applicable_authority(tmp_path):
    scheduler = _scheduler(tmp_path, dual=True)
    _source_found(scheduler)
    _complete_applicability(scheduler)
    snapshot = scheduler.snapshot()
    proof_id = snapshot["dual_tracks"]["O"]["speculative_proof_task_id"]
    assert snapshot["tasks"][proof_id]["status"] == "REDIRECTED"
    assert snapshot["obligations"]["O"]["status"] == "CLOSED"
