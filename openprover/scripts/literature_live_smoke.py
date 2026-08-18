"""Public scholarly search, full-text/PDF, and authority-trust smoke.

The query and source are public.  The script never sends project statements or
repository files to a scholarly provider; it only records normalized metadata
and a content-hashed public artifact.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path

from openprover.math_research.literature import ExternalAuthorityRegistry
from openprover.math_research.pipelines import AsyncDAGScheduler, AsynchronousPipelineRuntime
from openprover.math_research.scholarly import (
    FullTextRetriever,
    OpenAlexProvider,
    ScholarlyProviderError,
    ScholarlySearchAdapter,
)


QUERY = "Pythagorean theorem"
DOI = "10.1073/pnas.032677199"
PMCID = "PMC123622"
PDF_URL = "https://europepmc.org/articles/PMC123622?pdf=render"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_production_literature_chain(
    root: Path, *, adapter: ScholarlySearchAdapter, exact_statement: str,
    authority_id: str,
) -> dict:
    """Run the literature Lead→Searcher→Reader→Authority→reconstruction path.

    Handlers are deterministic local functions; the actual public metadata and
    PDF artifact were already obtained above, so this does not make another
    network request or start a proof model.
    """

    scheduler = AsyncDAGScheduler(
        state_path=root / "production-chain" / "pipeline_state.json",
        config={
            "routing": {"allow_dual_track": True, "allow_proof_fallback_when_literature_unavailable": True},
            "literature_budget": {"initial_literature_searchers": 1, "max_literature_searchers": 1, "max_citation_chain_depth": 1},
        },
    )
    scheduler.add_obligation(
        "public-pythagorean-smoke",
        target_statement="Pythagorean theorem public literature smoke",
        literature_first=True,
    )

    def handler(task, _context):
        role = task["role"]
        if role == "literature_lead":
            return {"search_strategies": ["exact_theorem"]}
        if role == "literature_searcher":
            records = adapter.search(QUERY, provider_names=["openalex"], limit=10, force_refresh=False)
            source = next(record for record in records if (record.doi or "").casefold() == DOI)
            value = source.to_literature_source()
            value["deep_read_required"] = True
            value["full_text_url"] = PDF_URL
            return {"sources": [value], "create_reader": True}
        if role in {"literature_reader", "literature_deep_reader"}:
            return {
                "theorems": [{"authority_id": authority_id, "statement": exact_statement, "location": "page 1"}],
                "literature_verdict": "EXACT_RESULT_FOUND",
                "authority_status": "UNVERIFIED_REFERENCE",
            }
        if role == "literature_authority_auditor":
            return {
                "literature_verdict": "EXACT_RESULT_FOUND",
                "authority_status": "VERIFIED_SOURCE_THEOREM",
                "deterministic_verification": True,
                "authority_id": authority_id,
            }
        if role == "literature_synthesizer":
            return {"synthesis_path": "production-chain/LITERATURE_SYNTHESIS.md"}
        if role == "reconstruction":
            return {"verdict": "CORRECT", "all_required_gates": True, "authority_id": authority_id}
        if role == "theorem_verifier":
            return {"verdict": "CORRECT", "all_required_gates": True, "authority_id": authority_id}
        raise RuntimeError(f"unexpected production literature role: {role}")

    runtime = AsynchronousPipelineRuntime(
        scheduler,
        {"literature": handler, "verification": handler, "proof": handler},
        max_workers=3,
    )
    try:
        for _ in range(160):
            runtime.start_window({"proof": 0, "literature": 1, "verification": 1})
            runtime.poll()
            snapshot = scheduler.snapshot()
            ready = any(
                snapshot["tasks"].get(task_id, {}).get("status") in {"READY", "RETRY_READY"}
                for queue in snapshot["queues"].values()
                for task_id in queue
            )
            if not runtime.pending() and not ready:
                break
        snapshot = scheduler.snapshot()
        obligation = snapshot["obligations"]["public-pythagorean-smoke"]
        roles = [task.get("role") for task in snapshot["tasks"].values() if task.get("obligation_id") == "public-pythagorean-smoke"]
        events = [event.get("type") for event in snapshot.get("events", [])]
        return {
            "status": "PASS" if obligation.get("status") == "CLOSED" else "FAIL",
            "obligation_status": obligation.get("status"),
            "task_roles": roles,
            "event_types": events,
            "authority_id": authority_id,
            "runtime_pending": runtime.pending(),
            "provider_network_calls_after_cache": False,
        }
    finally:
        runtime.shutdown(wait=True)


def run(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    provider = OpenAlexProvider(cache_dir=root / "scholarly-cache", minimum_interval=0)
    adapter = ScholarlySearchAdapter([provider])
    records = adapter.search(QUERY, provider_names=["openalex"], limit=10, force_refresh=True)
    selected = next((record for record in records if (record.doi or "").casefold() == DOI), None)
    if selected is None:
        raise RuntimeError(f"OpenAlex did not return the selected public work {DOI}")
    phase_d = {
        "phase": "D",
        "status": "PASS",
        "provider": "openalex",
        "query": QUERY,
        "record_count": len(records),
        "selected_source_id": selected.source_id,
        "selected_doi": selected.doi,
        "selected_title": selected.title,
        "selected_authors": selected.authors,
        "selected_year": selected.year,
        "selected_venue": selected.venue,
        "selected_related_versions": selected.related_versions,
        "cache_dir": str(root / "scholarly-cache"),
        "query_payload_minimized": True,
        "project_context_transmitted": False,
    }
    _write(root / "phase-D.json", phase_d)

    # OpenAlex relates this DOI to the public PMC copy; Europe PMC provides a
    # stable PDF render endpoint for the same PMCID.
    source = copy.deepcopy(selected)
    source.full_text_url = PDF_URL
    retriever = FullTextRetriever(root / "fulltext-cache", timeout=45)
    artifact = retriever.retrieve(source, source_id=DOI, force_refresh=True)
    extracts = [item for item in artifact.theorem_extracts if item.get("label", "").casefold() == "proposition 1"]
    if artifact.media_type != "application/pdf" or artifact.extraction_method != "pdftotext" or not extracts:
        raise RuntimeError("public PDF was retrieved but Proposition 1 extraction was incomplete")
    proposition = extracts[0]
    phase_e = {
        "phase": "E",
        "status": "PASS",
        "source_id": DOI,
        "pmcid": PMCID,
        "requested_url": PDF_URL,
        "artifact": {key: value for key, value in artifact.to_dict().items() if key != "extracted_text"},
        "theorem_extract_count": len(artifact.theorem_extracts),
        "selected_extract": proposition,
        "pdf_magic_verified": Path(artifact.local_path).read_bytes().startswith(b"%PDF-"),
    }
    _write(root / "phase-E.json", phase_e)

    registry_root = root / "authority"
    registry_root.mkdir(parents=True, exist_ok=True)
    authority_artifact = registry_root / "artifacts" / Path(artifact.local_path).name
    authority_artifact.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(artifact.local_path, authority_artifact)
    artifact_hash = "sha256:" + hashlib.sha256(authority_artifact.read_bytes()).hexdigest()
    exact_statement = str(proposition["statement"]).strip()
    authority = {
        "authority_id": "kadison-pythagorean-proposition-1",
        "title": selected.title,
        "authors": selected.authors or ["Richard V. Kadison"],
        "year": selected.year or 2002,
        "source": selected.source_url,
        "DOI_or_stable_identifier": DOI,
        "version": f"published version; {PMCID} public PDF render",
        "theorem_number": "Proposition 1",
        "page_or_section": proposition.get("location", "page 1"),
        "exact_statement": exact_statement,
        "normalized_statement": " ".join(exact_statement.casefold().split()),
        "hypotheses": "{e_a} is an orthonormal basis for Hilbert space H; t_a are real and non-negative with sum t_a^2 = 1.",
        "notation_map": {"H": "Hilbert space", "e_a": "orthonormal basis", "t_a": "specified non-negative reals"},
        "retrieval_source": PDF_URL,
        "retrieved_at": artifact.retrieved_at,
        "reader_verdict": "THEOREM_EXTRACTED",
        "authority_verifier_verdict": "PENDING",
        "used_by_obligations": ["public-pythagorean-smoke"],
        "source_type": "published_version",
        "content_scope": "FULL_TEXT",
        "retrieved_content_path": str(authority_artifact.relative_to(registry_root)).replace("\\", "/"),
        "retrieved_content_sha256": artifact_hash,
    }
    registry = ExternalAuthorityRegistry(registry_root)
    registered = registry.register(authority)
    verification = {
        "verdict": "VERIFIED_SOURCE_THEOREM",
        "source_identity_match": True,
        "bibliographic_metadata_match": True,
        "exact_statement_match": True,
        "claimed_source_type": "published_version",
    }
    verified = registry.verify(registered["authority_id"], verification)
    reconstruction = {
        "authority_id": verified["authority_id"],
        "source_identity": DOI,
        "theorem_number": verified["theorem_number"],
        "location": verified["page_or_section"],
        "exact_statement_match": verified["exact_statement"] == exact_statement,
        "applicability_status": "NOT_EVALUATED",
        "mathematical_truth_promoted": False,
    }
    phase_f = {
        "phase": "F",
        "status": "PASS" if verified.get("status") == "VERIFIED_SOURCE_THEOREM" else "FAIL",
        "provider": "openalex",
        "authority_status": verified.get("status"),
        "authority_id": verified.get("authority_id"),
        "authority_verification_errors": verified.get("authority_verification_errors", []),
        "verification": verification,
        "reconstruction": reconstruction,
        "exact_statement_snippet": exact_statement[:600],
        "artifact_sha256": artifact_hash,
        "metadata_source": "OpenAlex public metadata",
        "full_text_source": PDF_URL,
        "project_truth_unchanged": True,
    }
    phase_f["production_literature_chain"] = _run_production_literature_chain(
        root,
        adapter=adapter,
        exact_statement=exact_statement,
        authority_id=verified["authority_id"],
    )
    if phase_f["production_literature_chain"]["status"] != "PASS":
        phase_f["status"] = "FAIL"
    _write(root / "phase-F.json", phase_f)
    return {"phase_D": phase_d, "phase_E": phase_e, "phase_F": phase_f}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.root), ensure_ascii=True, indent=2))
    except ScholarlyProviderError as exc:
        output = {"phase": "D/E/F", "status": "BLOCKED", "provider_error": exc.to_dict()}
        _write(args.root / "phase-D-E-F.json", output)
        print(json.dumps(output, ensure_ascii=True, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
