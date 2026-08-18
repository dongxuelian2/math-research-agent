from __future__ import annotations

import pytest
import hashlib
import json

from openprover.math_research.literature import (
    ExternalAuthorityRegistry,
    LiteratureMemory,
    LiteratureSynthesis,
    LiteratureTaskExecutor,
)
from openprover.math_research.pipelines import AsyncDAGScheduler
from openprover.math_research.routing import ModelRouter
from openprover.math_research.project import ProjectError


def authority_record(authority_id="ext-real", root=None, **overrides):
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
        artifact = root / "retrieved-source.txt"
        artifact.write_text("If H, then C.", encoding="utf-8")
        artifact_hash = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
        text_artifact = root / "retrieved-source-text.txt"
        text_artifact.write_text("If H, then C.", encoding="utf-8")
        text_hash = "sha256:" + hashlib.sha256(text_artifact.read_bytes()).hexdigest()
        statement_digest = "sha256:" + hashlib.sha256(b"If H, then C.").hexdigest()
        extraction = {
            "schema_version": 1,
            "source_artifact_sha256": artifact_hash,
            "text_artifact_sha256": text_hash,
            "extractions": [
                {
                    "extraction_id": "span-0-13",
                    "theorem_label": "Theorem 2",
                    "location": "p. 10",
                    "span_start": 0,
                    "span_end": 13,
                    "raw_extracted_text": "If H, then C.",
                    "normalized_extracted_text": "If H, then C.",
                    "extracted_statement_sha256": statement_digest,
                }
            ],
        }
        extraction_path = root / "THEOREM_EXTRACTION.json"
        extraction_path.write_text(json.dumps(extraction, sort_keys=True), encoding="utf-8")
        extraction_hash = "sha256:" + hashlib.sha256(extraction_path.read_bytes()).hexdigest()
    else:
        artifact_hash = "sha256:" + "0" * 64
        text_hash = extraction_hash = statement_digest = artifact_hash
    value = {
        "authority_id": authority_id,
        "title": "A real retrieved paper",
        "authors": ["A. Author"],
        "year": 2020,
        "source": "Journal",
        "DOI_or_stable_identifier": "doi:10.1000/real",
        "version": "published",
        "theorem_number": "Theorem 2",
        "page_or_section": "p. 10",
        "exact_statement": "If H, then C.",
        "normalized_statement": "If H, then C.",
        "hypotheses": ["H"],
        "notation_map": {"x": "x"},
        "retrieval_source": "https://publisher.example/paper.pdf",
        "retrieved_at": "2026-08-13T00:00:00Z",
        "reader_verdict": "THEOREM_EXTRACTED",
        "authority_verifier_verdict": "PENDING",
        "used_by_obligations": [],
        "source_type": "original_paper",
        "content_scope": "THEOREM_PAGE",
        "retrieved_content_path": "retrieved-source.txt",
        "retrieved_content_sha256": artifact_hash,
        "text_artifact_path": "retrieved-source-text.txt",
        "text_artifact_sha256": text_hash,
        "extraction_artifact_path": "THEOREM_EXTRACTION.json",
        "extraction_artifact_sha256": extraction_hash,
        "extraction_id": "span-0-13",
        "span_start": 0,
        "span_end": 13,
        "extracted_statement_sha256": statement_digest,
        "extractor_version": "test-v1",
    }
    value.update(overrides)
    return value


def verification(**overrides):
    value = {
        "verdict": "VERIFIED_SOURCE_THEOREM",
        "source_identity_match": True,
        "bibliographic_metadata_match": True,
        "exact_statement_match": True,
        "claimed_source_type": "original_paper",
    }
    value.update(overrides)
    return value


def test_fabricated_and_abstract_only_citations_cannot_promote(tmp_path):
    registry = ExternalAuthorityRegistry(tmp_path)
    with pytest.raises(ProjectError, match="Fabricated"):
        registry.register(authority_record(authority_id="fake-placeholder"))

    abstract = registry.register(
        authority_record(
            authority_id="ext-abstract",
            root=tmp_path,
            DOI_or_stable_identifier="doi:10.1000/abstract",
            reader_verdict="ABSTRACT_ONLY",
            content_scope="ABSTRACT",
        )
    )
    rejected = registry.verify(abstract["authority_id"], verification())
    assert rejected["status"] == "AUTHORITY_VERIFICATION_FAILED"
    assert any("abstract" in error for error in rejected["authority_verification_errors"])
    with pytest.raises(ProjectError, match="not VERIFIED_SOURCE_THEOREM"):
        registry.require_verified_source(abstract["authority_id"])


def test_source_verification_ignores_applicability_flags_but_rejects_source_masquerade(tmp_path):
    registry = ExternalAuthorityRegistry(tmp_path / "source")
    root = tmp_path / "source"
    record = registry.register(
        authority_record(
            authority_id="ext-source",
            root=root,
            DOI_or_stable_identifier="doi:10.1000/source",
        )
    )
    verified = registry.verify(
        record["authority_id"],
        verification(
            hypotheses_match=False,
            implication_direction_match=False,
            exception_check_pass=False,
        ),
    )
    assert verified["status"] == "VERIFIED_SOURCE_THEOREM"

    registry2 = ExternalAuthorityRegistry(tmp_path / "masquerade")
    record2 = registry2.register(
        authority_record(
            authority_id="ext-masquerade",
            root=tmp_path / "masquerade",
            DOI_or_stable_identifier="doi:10.1000/masquerade",
        )
    )
    rejected = registry2.verify(
        record2["authority_id"], verification(claimed_source_type="later_explicit_restatement")
    )
    assert rejected["status"] == "AUTHORITY_VERIFICATION_FAILED"
    assert any("masquerade" in error for error in rejected["authority_verification_errors"])


def test_verified_authority_enters_separate_memory_but_not_project_registry(tmp_path):
    registry = ExternalAuthorityRegistry(tmp_path)
    record = registry.register(authority_record(root=tmp_path))
    verified = registry.verify(record["authority_id"], verification())
    assert verified["status"] == "VERIFIED_SOURCE_THEOREM"
    used = registry.require_verified_source(record["authority_id"], obligation_id="O1")
    assert used["used_by_obligations"] == ["O1"]

    memory = LiteratureMemory(tmp_path)
    entry = memory.add_verified_authority(used, concepts=["norm equation"], keywords=["valuation"])
    assert memory.search(concepts=["Norm Equation"])[0]["authority_id"] == entry["authority_id"]
    assert not (tmp_path / "theorems").exists()


def test_retrieved_artifact_hash_mismatch_blocks_authority(tmp_path):
    registry = ExternalAuthorityRegistry(tmp_path)
    record = registry.register(
        authority_record(
            authority_id="ext-bad-hash",
            root=tmp_path,
            DOI_or_stable_identifier="doi:10.1000/bad-hash",
            retrieved_content_sha256="sha256:" + "f" * 64,
        )
    )
    rejected = registry.verify(record["authority_id"], verification())
    assert rejected["status"] == "AUTHORITY_VERIFICATION_FAILED"
    assert "artifact hash mismatch" in " ".join(rejected["authority_verification_errors"])


def test_synthesis_separates_solved_frontier_and_budget_exhaustion_semantics():
    synthesis = LiteratureSynthesis(
        current_obligation="O: prove C from H",
        what_is_already_solved=["External theorem proves C under H and E."],
        what_still_needs_proof=["Prove exceptional case not-E."],
        literature_verdict="PARTIAL_RESULT_FOUND",
    )
    rendered = synthesis.render()
    assert "WHAT_IS_ALREADY_SOLVED" in rendered
    assert "WHAT_STILL_NEEDS_PROOF" in rendered
    assert rendered.index("WHAT_IS_ALREADY_SOLVED") < rendered.index("WHAT_STILL_NEEDS_PROOF")

    with pytest.raises(ProjectError, match="INSUFFICIENT_SEARCH"):
        LiteratureSynthesis(
            current_obligation="O",
            conflicts_and_uncertainty=["search budget exhausted"],
            literature_verdict="NO_SUFFICIENT_RESULT_FOUND",
        )


def test_literature_executor_requires_explicit_minimized_transmission(tmp_path):
    scheduler = AsyncDAGScheduler()
    scheduler.add_obligation("O", target_statement="private target", literature_first=True)
    task = scheduler.dispatch_window(
        {
            "proof": 0,
            "literature": 1,
            "verification": 0,
        }
    )["literature"][0]
    router = ModelRouter({"provider": "mock", "model": "mock", "reasoning_effort": "low"})
    executor = LiteratureTaskExecutor(
        scheduler,
        router,
        client_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be constructed")
        ),
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "work",
        external_transmission_approved=False,
    )
    result = executor(task)
    assert result == {
        "literature_verdict": "LITERATURE_PROVIDER_UNAVAILABLE",
        "reason": "no approved structured literature request is available",
    }
