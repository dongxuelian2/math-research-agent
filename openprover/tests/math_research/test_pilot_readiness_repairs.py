from __future__ import annotations

import copy
import hashlib
import json
import time

import pytest

from openprover.math_research.literature import (
    ExternalAuthorityRegistry,
    LiteratureTaskExecutor,
)
from openprover.math_research.pipelines import (
    AsyncDAGScheduler,
    AsynchronousPipelineRuntime,
    AtomicResourceBudget,
)
from openprover.math_research.project import ProjectError
from openprover.math_research.routing import ModelRouter
from openprover.math_research.scholarly import (
    FullTextRetriever,
    ScholarlyRecord,
)
from scripts.live_provider_validation import evaluate_cancellation_evidence
from scripts.readiness_audit import audit


class _Adapter:
    def search(self, query, **_kwargs):
        return [ScholarlyRecord(
            source_id="work-1", provider="test", title="Public theorem",
            authors=["A. Author"], year=2020, doi="10.1000/public",
            source_url="https://example.test/public",
            full_text_url="https://example.test/public.html",
            source_type="published_version", query=query,
        )]


def _executor(tmp_path, scheduler, *, adapter=None, retriever=None, registry=None):
    return LiteratureTaskExecutor(
        scheduler,
        ModelRouter({"provider": "mock", "model": "mock"}),
        client_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the deterministic public-query path must not construct an LLM")
        ),
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "work",
        external_transmission_approved=False,
        authority_registry=registry,
        scholarly_adapter=adapter,
        document_retriever=retriever,
    )


def _request(blocking="blocking", hints=None):
    return {
        "obligation_id": "O",
        "requested_statement": "public smoke target",
        "why_needed": "find the public theorem",
        "blocking_or_nonblocking": blocking,
        "expected_impact": "close the synthetic obligation",
        "search_hints": hints if hints is not None else {
            "public_query": "Pythagorean theorem",
            "doi": "10.1000/public",
        },
    }


def test_literature_lead_public_query_handoff(tmp_path):
    scheduler = AsyncDAGScheduler(config={"literature": {
        "external_public_search_approved": True,
        "public_search_approval_source": "operator:test",
    }, "literature_budget": {"initial_literature_searchers": 1}})
    scheduler.add_obligation("O", target_statement="private theorem body")
    scheduler.add_literature_request(_request())
    lead = next(
        task for task in scheduler.dispatch_window({"proof": 0, "literature": 1, "verification": 0})["literature"]
        if task["role"] == "literature_lead"
    )
    executor = _executor(tmp_path, scheduler, adapter=_Adapter())
    plan = executor(lead)
    scheduler.complete_task(lead["task_id"], plan)
    searcher = scheduler.dispatch_window({"proof": 0, "literature": 1, "verification": 0})["literature"][0]
    payload = searcher["payload"]
    assert payload["public_query"] == "Pythagorean theorem"
    assert payload["external_search_approved"] is True
    assert payload["approval_source"] == "operator:test"
    assert payload["approval_timestamp"]
    assert payload["query_hash"].startswith("sha256:")
    assert payload["literature_request_id"].startswith("litreq-")
    assert executor(searcher)["search_status"] == "NETWORK_DISCOVERY_PASS"


def test_literature_lead_rejects_empty_public_query(tmp_path):
    scheduler = AsyncDAGScheduler()
    scheduler.add_obligation("O", target_statement="private theorem body")
    scheduler.add_literature_request(_request(hints={"public_query": ""}))
    lead = scheduler.dispatch_window({"proof": 0, "literature": 1, "verification": 0})["literature"][0]
    with pytest.raises(ProjectError, match="non-empty public_query"):
        _executor(tmp_path, scheduler)(lead)


def test_searcher_rejects_unapproved_query(tmp_path):
    scheduler = AsyncDAGScheduler()
    scheduler.add_obligation("O", target_statement="target")
    task = scheduler.create_task("literature", "O", role="literature_searcher", payload={
        "public_query": "public theorem", "external_search_approved": False,
    })
    result = _executor(tmp_path, scheduler, adapter=_Adapter())(task)
    assert result["search_status"] == "SEARCH_NOT_AUTHORIZED"
    assert "literature_verdict" not in result


def test_nonblocking_literature_query_executes(tmp_path):
    scheduler = AsyncDAGScheduler(config={"literature": {
        "external_public_search_approved": True,
        "public_search_approval_source": "operator:test",
    }, "literature_budget": {"initial_literature_searchers": 1}})
    scheduler.add_obligation("O", target_statement="private theorem body")
    scheduler.add_literature_request(_request(blocking="nonblocking"))
    assert any(
        task["pipeline"] == "proof" and task["status"] == "READY"
        for task in scheduler.snapshot()["tasks"].values()
    )
    lead = scheduler.dispatch_window({"proof": 0, "literature": 1, "verification": 0})["literature"][0]
    executor = _executor(tmp_path, scheduler, adapter=_Adapter())
    scheduler.complete_task(lead["task_id"], executor(lead))
    searcher = scheduler.dispatch_window({"proof": 0, "literature": 1, "verification": 0})["literature"][0]
    assert executor(searcher)["search_status"] == "NETWORK_DISCOVERY_PASS"


def test_real_executor_end_to_end_literature_pipeline(tmp_path):
    body = (
        b"<html><body>Theorem 2. If H is true, then conclusion C follows for "
        b"every admissible object in the stated domain. Proof:</body></html>"
    )

    def request(_url, _headers, _timeout):
        return 200, {"Content-Type": "text/html"}, body

    scheduler = AsyncDAGScheduler(config={
        "literature": {
            "external_public_search_approved": True,
            "public_search_approval_source": "operator:integration-test",
        },
        "literature_budget": {
            "initial_literature_searchers": 1, "max_literature_searchers": 1,
        },
    })
    scheduler.add_obligation(
        "O", target_statement="synthetic public target",
        context={"expected_theorem_label": "Theorem 2"},
    )
    scheduler.add_literature_request(_request())
    registry = ExternalAuthorityRegistry(tmp_path / "literature")
    executor = _executor(
        tmp_path, scheduler, adapter=_Adapter(), registry=registry,
        retriever=FullTextRetriever(
            tmp_path / "literature" / "fulltext", request_fn=request
        ),
    )

    def verification(task, _context):
        snapshot_hash = scheduler.applicability_context("O")["assumption_snapshot_hash"]
        if task["role"] == "reconstruction":
            return {
                "verdict": "APPLICABILITY_CANDIDATE",
                "applicability_id": "app-O",
                "assumption_snapshot_hash": snapshot_hash,
                "result_artifact": "reconstruction.json",
                "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0},
            }
        return {
            "verdict": "APPLICABLE",
            "authority_status": "APPLICABLE_EXTERNAL_AUTHORITY",
            "applicability_status": "APPLICABLE_EXTERNAL_AUTHORITY",
            "applicability_id": "app-O",
            "assumption_snapshot_hash": snapshot_hash,
            "deterministic_applicability_promotion": True,
            "authority_id": scheduler.snapshot()["obligations"]["O"].get(
                "authority_candidate", {}
            ).get("authority_id"),
            "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0},
        }

    runtime = AsynchronousPipelineRuntime(
        scheduler, {"literature": executor, "verification": verification},
        max_workers=2,
    )
    try:
        for _ in range(80):
            runtime.start_window({"proof": 0, "literature": 2, "verification": 1})
            runtime.poll()
            if scheduler.snapshot()["obligations"]["O"]["status"] == "CLOSED":
                break
            time.sleep(0.005)
        snapshot = scheduler.snapshot()
        assert snapshot["obligations"]["O"]["status"] == "CLOSED"
        roles = {task["role"] for task in snapshot["tasks"].values()}
        assert {
            "literature_lead", "literature_searcher", "literature_reader",
            "literature_synthesizer", "literature_authority_auditor",
            "reconstruction", "theorem_verifier",
        } <= roles
        assert registry.verified()
    finally:
        runtime.shutdown(wait=True)


def _bound_authority(tmp_path):
    body = (
        b"<html><body>Theorem 2. If H is true, then conclusion C follows for "
        b"every admissible object in the stated domain. Proof:</body></html>"
    )

    def request(_url, _headers, _timeout):
        return 200, {"Content-Type": "text/html"}, body

    registry = ExternalAuthorityRegistry(tmp_path)
    artifact = FullTextRetriever(
        tmp_path / "fulltext", request_fn=request
    ).retrieve("https://example.test/theorem.html", source_id="10.1000/bound")
    extraction = artifact.theorem_extracts[0]

    def relative(value):
        return str((__import__("pathlib").Path(value)).relative_to(tmp_path)).replace("\\", "/")

    record = {
        "authority_id": "ext-bound", "title": "Bound theorem",
        "authors": ["A. Author"], "year": 2020, "source": "Journal",
        "DOI_or_stable_identifier": "10.1000/bound",
        "version": artifact.sha256, "theorem_number": extraction["theorem_label"],
        "page_or_section": extraction["location"],
        "exact_statement": extraction["normalized_extracted_text"],
        "normalized_statement": extraction["normalized_extracted_text"],
        "hypotheses": ["H"], "notation_map": {},
        "retrieval_source": artifact.requested_url, "retrieved_at": artifact.retrieved_at,
        "reader_verdict": "THEOREM_EXTRACTED", "authority_verifier_verdict": "PENDING",
        "used_by_obligations": ["O"], "source_type": "published_version",
        "content_scope": "FULL_TEXT",
        "retrieved_content_path": relative(artifact.local_path),
        "retrieved_content_sha256": artifact.to_dict()["sha256"],
        "text_artifact_path": relative(artifact.text_path),
        "text_artifact_sha256": artifact.to_dict()["text_sha256"],
        "extraction_artifact_path": relative(artifact.extraction_artifact_path),
        "extraction_artifact_sha256": artifact.to_dict()["extraction_artifact_sha256"],
        "extraction_id": extraction["extraction_id"],
        "span_start": extraction["span_start"], "span_end": extraction["span_end"],
        "extracted_statement_sha256": extraction["extracted_statement_sha256"],
        "extractor_version": extraction["extractor_version"],
    }
    verification = {
        "verdict": "VERIFIED_SOURCE_THEOREM",
        "source_identity_match": True, "bibliographic_metadata_match": True,
        "claimed_source_type": "published_version",
    }
    return registry, record, verification


def test_authority_rejects_fake_statement_real_pdf(tmp_path):
    registry, record, verification = _bound_authority(tmp_path)
    record["exact_statement"] = record["normalized_statement"] = "A fake theorem statement."
    result = registry.verify(registry.register(record)["authority_id"], verification)
    assert result["status"] == "REJECTED_EXTERNAL_AUTHORITY"
    assert "does not match the extracted span" in " ".join(result["authority_verification_errors"])


def test_authority_rejects_wrong_span(tmp_path):
    registry, record, verification = _bound_authority(tmp_path)
    record["span_start"] += 1
    result = registry.verify(registry.register(record)["authority_id"], verification)
    assert result["status"] == "REJECTED_EXTERNAL_AUTHORITY"
    assert "authority span" in " ".join(result["authority_verification_errors"])


def test_authority_rejects_modified_text_artifact(tmp_path):
    registry, record, verification = _bound_authority(tmp_path)
    registered = registry.register(record)
    (tmp_path / record["text_artifact_path"]).write_text("modified", encoding="utf-8")
    result = registry.verify(registered["authority_id"], verification)
    assert result["status"] == "REJECTED_EXTERNAL_AUTHORITY"
    assert "text artifact hash mismatch" in result["authority_verification_errors"]


def test_authority_accepts_correct_artifact_span_and_statement(tmp_path):
    registry, record, verification = _bound_authority(tmp_path)
    result = registry.verify(registry.register(record)["authority_id"], verification)
    assert result["status"] == "VERIFIED_SOURCE_THEOREM"


def test_cancellation_completed_before_cancel_not_pass():
    evidence = {
        "status": "PASS", "task_A_id": "A", "task_B_id": "B",
        "task_a_status": "COMPLETED_BEFORE_CANCEL", "task_b_status": "COMPLETE",
        "records": {
            "A": {"cancel_request_timestamp": "1", "interrupt_dispatch_timestamp": "2", "process_exit_timestamp": "3"},
            "B": {"final_task_state": "COMPLETE"},
        },
    }
    assert evaluate_cancellation_evidence(evidence)["verdict"] == "INCONCLUSIVE"


def test_budget_underestimate_commit():
    budget = AtomicResourceBudget({"provider_calls": 2, "total_tokens": 1000})
    reservation = budget.reserve({"provider_calls": 1, "input_tokens": 60, "output_tokens": 40})
    result = budget.reconcile(reservation, {
        "input_tokens": 90, "output_tokens": 60, "reasoning_tokens": 0,
        "cached_tokens": 0, "total_tokens": 150,
    }, usage_known=True)
    assert result["additional_commit"]["total_tokens"] == 50
    assert budget.snapshot()["committed_total_tokens"] == 150


def test_budget_overestimate_release():
    budget = AtomicResourceBudget({"provider_calls": 2, "total_tokens": 1000})
    reservation = budget.reserve({"provider_calls": 1, "input_tokens": 100, "output_tokens": 100})
    result = budget.reconcile(reservation, {
        "input_tokens": 80, "output_tokens": 40, "reasoning_tokens": 0,
        "cached_tokens": 0, "total_tokens": 120,
    }, usage_known=True)
    assert result["released"]["total_tokens"] == 80
    assert budget.snapshot()["reserved_total_tokens"] == 0


def test_budget_unknown_usage_after_interrupt():
    budget = AtomicResourceBudget({"provider_calls": 2, "total_tokens": 1000})
    reservation = budget.reserve({"provider_calls": 1, "input_tokens": 70, "output_tokens": 30})
    result = budget.reconcile(reservation, None, usage_known=False)
    assert result["status"] == "USAGE_UNKNOWN_AFTER_INTERRUPT"
    assert result["actual"]["total_tokens"] == 100
    assert result["unknown_usage_policy"] == "reserved_as_committed"


def test_budget_global_hard_cap_after_reconcile():
    budget = AtomicResourceBudget({"provider_calls": 2, "total_tokens": 100})
    reservation = budget.reserve({"provider_calls": 1, "input_tokens": 40, "output_tokens": 40})
    result = budget.reconcile(reservation, {
        "input_tokens": 80, "output_tokens": 70, "reasoning_tokens": 0,
        "cached_tokens": 0, "total_tokens": 150,
    }, usage_known=True)
    assert result["status"] == "HARD_BUDGET_EXCEEDED_BY_COMPLETED_CALL"
    assert budget.snapshot()["halted"] is True
    with pytest.raises(ProjectError, match="halted"):
        budget.reserve({"provider_calls": 1, "total_tokens": 1})


def test_budget_concurrent_reservations_cannot_overshoot():
    budget = AtomicResourceBudget({"provider_calls": 1, "total_tokens": 100})
    budget.reserve({"provider_calls": 1, "total_tokens": 100})
    with pytest.raises(ProjectError, match="exhausted"):
        budget.reserve({"provider_calls": 1, "total_tokens": 1})


def _readiness_fixture(tmp_path, *, completed_before_cancel=False):
    root = tmp_path / "archive"
    root.mkdir()
    entries = {}

    def add(name, relative, value, *, raw=False):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw:
            path.write_bytes(value)
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        entries[name] = {
            "path": relative,
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        return path

    state_a = "COMPLETED_BEFORE_CANCEL" if completed_before_cancel else "INTERRUPTED"
    add("phase_A_result", "validation-evidence/phase-A/result.json", {
        "status": "PASS", "task_A_id": "A", "task_B_id": "B",
        "task_a_status": state_a, "task_b_status": "COMPLETE",
        "records": {
            "A": {"cancel_request_timestamp": "1", "interrupt_dispatch_timestamp": "2",
                  "process_exit_timestamp": "3", "processes": [{"pid": 10}],
                  "provider_error": {"retry_count": 0}, "fallback_count": 0},
            "B": {"final_task_state": "COMPLETE"},
        },
    })
    add("phase_B_result", "validation-evidence/phase-B/result.json", {
        "requested_model": "gpt-5.6-luna", "actual_model": "gpt-5.6-luna",
        "requested_effort": "max", "actual_effort": "max",
        "actual_provider": "codex_cli", "fallback_used": False,
        "processes": [{"returncode": 0}], "usage": {},
    })
    add("phase_C_result", "validation-evidence/phase-C/result.json", {
        "route_classes": {
            "routine": {"actual_model": "gpt-5.6-luna", "reasoning_effort": "max", "fallback": False},
            "research": {"actual_model": "gpt-5.6-sol", "reasoning_effort": "high", "fallback": False},
            "strategic": {"actual_model": "gpt-5.6-sol", "reasoning_effort": "max", "fallback": False},
        },
        "checkpoint_campaign_status": "STOPPED_AT_CHECKPOINT",
        "final_campaign_status": "COMPLETE_PROVED_REPLAY",
        "no_external_provider_calls": True,
    })
    add("phase_D_result", "validation-evidence/phase-D/result.json", {
        "provider": "openalex", "query": "Pythagorean theorem",
        "project_context_transmitted": False,
    })
    add("openalex_response", "validation-evidence/phase-D/artifacts/openalex.json", {"results": [{"id": "W1"}]})
    pdf = add("pdf_artifact", "validation-evidence/phase-E/artifacts/source.pdf", b"%PDF-test", raw=True)
    text = "If H then C."
    text_path = add("text_artifact", "validation-evidence/phase-E/artifacts/source.txt", text.encode(), raw=True)
    pdf_hash = "sha256:" + hashlib.sha256(pdf.read_bytes()).hexdigest()
    text_hash = "sha256:" + hashlib.sha256(text_path.read_bytes()).hexdigest()
    statement_hash_value = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    add("extraction_artifact", "validation-evidence/phase-E/artifacts/extraction.json", {
        "source_artifact_sha256": pdf_hash, "text_artifact_sha256": text_hash,
        "extractions": [{"extraction_id": "span-0-12", "span_start": 0,
                         "span_end": len(text), "normalized_extracted_text": text,
                         "extracted_statement_sha256": statement_hash_value}],
    })
    add("phase_E_result", "validation-evidence/phase-E/result.json", {
        "artifact_sha256": pdf_hash, "text_artifact_sha256": text_hash,
        "selected_extraction_id": "span-0-12",
    })
    components = {name: True for name in (
        "NETWORK_DISCOVERY_PASS", "PDF_RETRIEVAL_PASS", "THEOREM_EXTRACTION_PASS",
        "SOURCE_THEOREM_PROMOTION_PASS", "RECONSTRUCTION_PASS", "VERIFIER_PASS",
        "APPLICABILITY_PROMOTION_PASS", "NEGATIVE_HYPOTHESIS_PASS",
        "NEGATIVE_DIRECTION_PASS",
        "PRODUCTION_PIPELINE_PASS",
    )}
    add("phase_F_result", "validation-evidence/phase-F/result.json", {
        "authority_id": "ext-1", "applicability_id": "app-1",
        "assumption_snapshot_hash": "sha256:snapshot", "components": components,
        "production_executor_class": "openprover.math_research.literature.LiteratureTaskExecutor",
        "smoke_local_handlers": False, "obligation_status": "CLOSED",
        "negative_cases": {
            "negative-hypothesis": {"closed": False, "actual_verdict": "HYPOTHESIS_MISMATCH"},
            "negative-direction": {"closed": False, "actual_verdict": "WRONG_DIRECTION"},
        },
    })
    add("authority_registry", "validation-evidence/phase-F/artifacts/registry.json", {
        "source_theorems": {"ext-1": {"status": "VERIFIED_SOURCE_THEOREM",
                                       "authority_verification_errors": []}},
        "applicability_records": {"app-1": {
            "status": "APPLICABLE_EXTERNAL_AUTHORITY",
            "assumption_snapshot_hash": "sha256:snapshot",
        }},
    })
    reconstruction = {
        "status": "APPLICABILITY_CANDIDATE",
        "hypothesis_mapping": [{"status": "PROVED"}],
        "conclusion_mapping": {"status": "PROVED"},
        "direction_analysis": {"status": "PROVED"},
        "exception_analysis": {"status": "NOT_APPLICABLE"},
        "reconstructor_call_id": "call-reconstructor",
    }
    verifier = {
        "verdict": "APPLICABLE",
        "promotion_status": "APPLICABLE_EXTERNAL_AUTHORITY",
        "verifier_call_id": "call-verifier",
    }
    add("reconstruction_result", "validation-evidence/phase-F/artifacts/reconstruction.json", reconstruction)
    add("verifier_result", "validation-evidence/phase-F/artifacts/verifier.json", verifier)
    add("negative_hypothesis_reconstruction", "validation-evidence/applicability/negative-hypothesis/reconstruction.json", reconstruction)
    add("negative_hypothesis_verifier", "validation-evidence/applicability/negative-hypothesis/verifier.json", {**verifier, "verdict": "HYPOTHESIS_MISMATCH", "promotion_status": "APPLICABILITY_REJECTED"})
    add("negative_direction_reconstruction", "validation-evidence/applicability/negative-direction/reconstruction.json", reconstruction)
    add("negative_direction_verifier", "validation-evidence/applicability/negative-direction/verifier.json", {**verifier, "verdict": "WRONG_DIRECTION", "promotion_status": "APPLICABILITY_REJECTED"})
    junit = b'<testsuite tests="2" failures="0" errors="0" skipped="1"><testcase name="a"/><testcase name="b"><skipped/></testcase></testsuite>'
    add("pytest_full", "validation-evidence/phase-G/pytest-full.xml", junit, raw=True)
    add("pytest_focused", "validation-evidence/phase-G/pytest-focused.xml", junit, raw=True)
    add("model_config", "configs/models.json", {})
    add("model_catalog", "validation-evidence/phase-B/artifacts/catalog.json", {})
    inputs = {"evidence": entries, "required_testcases": []}
    (root / "READINESS_INPUTS.json").write_text(json.dumps(inputs), encoding="utf-8")
    return root


def test_readiness_missing_evidence_fails_closed(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    (root / "READINESS_INPUTS.json").write_text(json.dumps({"evidence": {}}), encoding="utf-8")
    result = audit(root)
    assert result["verdict"] == "PARTIALLY_READY"
    assert result["evidence_complete"] is False


def test_readiness_hash_mismatch_fails_closed(tmp_path):
    root = _readiness_fixture(tmp_path)
    inputs = json.loads((root / "READINESS_INPUTS.json").read_text(encoding="utf-8"))
    inputs["evidence"]["phase_A_result"]["sha256"] = "sha256:" + "0" * 64
    (root / "READINESS_INPUTS.json").write_text(json.dumps(inputs), encoding="utf-8")
    result = audit(root)
    assert result["verdict"] == "PARTIALLY_READY"
    assert result["hashes_verified"] is False


def test_readiness_does_not_trust_phase_pass_string(tmp_path):
    root = _readiness_fixture(tmp_path, completed_before_cancel=True)
    result = audit(root)
    assert result["phase_A"] == "INCONCLUSIVE"
    assert result["verdict"] == "PARTIALLY_READY"


def test_readiness_parses_real_pytest_xml(tmp_path):
    root = _readiness_fixture(tmp_path)
    result = audit(root)
    assert result["regression"] == {"tests": 2, "failures": 0, "errors": 0, "skipped": 1}
    assert result["verdict"] == "READY_FOR_PILOT_CAMPAIGN"
