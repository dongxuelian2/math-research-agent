from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprover.math_research.project import ProjectError, ProjectStore
from openprover.math_research.truth_store import TruthStoreFacade, TruthValidationError


def _project(tmp_path: Path) -> ProjectStore:
    project = ProjectStore.initialize(tmp_path / "project", "Truth facade", demo=True)
    premise_source = project.root / "sources" / "premise.md"
    premise_source.write_text("n is a positive integer", encoding="utf-8")
    project.add_premise(
        "P1",
        "Positive integer premise",
        "n is a positive integer",
        source_file="sources/premise.md",
        provenance=[{"source": "sources/premise.md", "kind": "HUMAN"}],
    )
    project.add_theorem(
        "D1",
        "Dependency",
        "For every n, n = n.",
        status="PROVED",
        claim_type="equality",
    )
    project.add_theorem(
        "target",
        "Target",
        "For every positive integer n, n = n.",
        dependencies=["P1", "D1"],
        claim_type="equality",
        notation_scope="integers",
    )
    return project


def _canonical(*, status: str = "RESOLVED_CANONICAL", digest: str = "a" * 64) -> list[dict]:
    return [
        {
            "requirement": {
                "logical_name": "campaign-source",
                "canonical_filename": "campaign.md",
                "purpose": "proof_authority",
                "requesting_obligation_id": "target",
            },
            "resolution_status": status,
            "computed_sha256": f"sha256:{digest}" if status == "RESOLVED_CANONICAL" else None,
            "expected_sha256": f"sha256:{'a' * 64}",
            "checkpoint_sha256": None,
            "authority_source": "test-registry",
            "authority_record": {"registry_id": "test"},
            "resolved_source_location": "sources/campaign.md",
        }
    ]


def test_capture_is_immutable_content_addressed_and_premise_kind_is_preserved(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)

    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())
    repeated = facade.capture_claim_snapshot("target", canonical_authority=_canonical())

    assert repeated == snapshot
    path = facade.claim_snapshot_path(snapshot.claim_snapshot_hash)
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["object_type"] == "CLAIM_SNAPSHOT"
    entries = {item.dependency_id: item for item in snapshot.dependency_snapshot.dependencies}
    assert entries["P1"].kind == "PREMISE"
    assert entries["P1"].captured_status == "ACTIVE"
    assert entries["D1"].kind == "THEOREM"
    assert entries["D1"].captured_status == "PROVED"


def test_root_statement_mutation_is_hard_stale(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())
    theorem = project.load_theorem("target")
    theorem["statement"] = "For every positive integer n, n = n + 1."
    project.update_theorem(theorem)

    comparison = facade.compare_claim_snapshot(snapshot, canonical_authority=_canonical())

    assert comparison.status == "ASSERTION_CHANGED"
    assert comparison.disposition == "HARD_STALE"
    with pytest.raises(TruthValidationError, match="PROMOTION rejected"):
        facade.validate_snapshot_for_promotion(snapshot, canonical_authority=_canonical())


def test_dependency_status_mutation_requires_revalidation(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())
    project.transition("D1", "FROZEN", actor="Human", reason="Dependency withdrawn")

    comparison = facade.compare_claim_snapshot(snapshot, canonical_authority=_canonical())

    assert comparison.status == "DEPENDENCY_CHANGED"
    assert comparison.disposition == "REVALIDATION_REQUIRED"


def test_canonical_authority_mutation_blocks(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())

    comparison = facade.compare_claim_snapshot(
        snapshot,
        canonical_authority=_canonical(status="HASH_MISMATCH"),
    )

    assert comparison.status == "UNRESOLVABLE_AUTHORITY"
    assert comparison.disposition == "BLOCKED"


def test_trust_policy_change_requires_revalidation_but_metadata_does_not(tmp_path: Path):
    project = _project(tmp_path)
    facade = TruthStoreFacade(project)
    snapshot = facade.capture_claim_snapshot("target", canonical_authority=_canonical())

    facade.update_metadata("target", {"title": "Renamed UI title"})
    assert (
        facade.compare_claim_snapshot(snapshot, canonical_authority=_canonical()).status == "MATCH"
    )

    project_meta = project.load_project()
    project_meta["truth_policy_version"] = "P2"
    project.save_project(project_meta)
    comparison = facade.compare_claim_snapshot(snapshot, canonical_authority=_canonical())
    assert comparison.status == "TRUST_POLICY_CHANGED"


def test_facade_rejects_identity_critical_metadata_update(tmp_path: Path):
    facade = TruthStoreFacade(_project(tmp_path))

    with pytest.raises(ProjectError, match="Identity-critical"):
        facade.update_metadata("target", {"statement": "different"})
