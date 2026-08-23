import json

import pytest

from math_research_agent.research.project import ProjectStore
from math_research_agent.research.retrieval import ContextBuilder
from math_research_agent.research.trust_kernel import (
    DependencyAuthorityResolver,
    FoundationRegistry,
    RegistryError,
    SemanticRegistry,
    content_hash,
    file_sha256,
)


def write_registry(path, data):
    data["registry_hash"] = content_hash(data, excluded={"registry_hash"})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def semantic_registry(tmp_path, *, notation_scope="scope-v1"):
    source = tmp_path / "sources" / "definitions.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "# Definitions\n\n在此正规形中，primitive core 当且仅当 h=1。\n",
        encoding="utf-8",
    )
    item = {
        "id": "SEM-TEST-01",
        "statement": "In this normal form, primitive core iff h=1.",
        "authority_kind": "iff",
        "notation_scope": notation_scope,
        "notation_version": "v1",
        "provenance": {
            "source_file": "sources/definitions.md",
            "source_hash": file_sha256(source),
            "source_section": "Definitions",
            "authority_source": "historical_source_body",
        },
        "version": "1.0.0",
        "content_hash": "",
    }
    item["content_hash"] = content_hash(item, excluded={"content_hash"})
    data = {
        "schema_version": 1,
        "registry_type": "SEMANTIC",
        "registry_id": "test-semantics",
        "version": "1.0.0",
        "items": [item],
        "registry_hash": "",
    }
    path = write_registry(tmp_path / "semantic.json", data)
    return SemanticRegistry.load(path, source_root=tmp_path), path


def test_foundation_registry_loads_and_is_versioned():
    registry = FoundationRegistry.load_builtin()
    assert registry.version == "1.0.0"
    assert registry.registry_hash.startswith("sha256:")
    assert registry.get("FOUND-NT-QR-02").content_hash.startswith("sha256:")


def test_invalid_foundation_id_is_rejected(tmp_path):
    source = FoundationRegistry.builtin_path()
    data = json.loads(source.read_text(encoding="utf-8"))
    data["items"][0]["id"] = "BAD-ID"
    data["items"][0]["content_hash"] = content_hash(data["items"][0], excluded={"content_hash"})
    path = write_registry(tmp_path / "bad-foundation.json", data)
    with pytest.raises(RegistryError, match="Invalid foundation ID"):
        FoundationRegistry.load(path)


def test_semantic_registry_loads_and_checks_source_hash(tmp_path):
    registry, _ = semantic_registry(tmp_path)
    item = registry.get("SEM-TEST-01", notation_scope="scope-v1")
    assert item.authority_kind == "iff"
    assert item.provenance["source_hash"]


def test_semantic_notation_scope_mismatch_is_rejected(tmp_path):
    registry, _ = semantic_registry(tmp_path)
    with pytest.raises(RegistryError, match="scoped to"):
        registry.get("SEM-TEST-01", notation_scope="scope-v2")


def test_package_metadata_cannot_prove_a_theorem_claim():
    resolver = DependencyAuthorityResolver(
        foundations=FoundationRegistry.load_builtin(),
        semantics=None,
        project=None,
    )
    report = resolver.resolve(
        [
            {
                "claim": "G_prim implies h=1",
                "claim_class": "PROJECT_THEOREM",
                "authority_id": "authoritative-package-metadata",
                "authority_type": "package_metadata",
            }
        ]
    )
    assert not report.admissible
    assert "metadata is not proof authority" in report.errors[0]


def test_local_proof_does_not_require_registry_authority():
    resolver = DependencyAuthorityResolver(
        foundations=FoundationRegistry.load_builtin(),
        semantics=None,
        project=None,
    )
    report = resolver.resolve(
        [
            {
                "claim": "Auxiliary parity lemma",
                "claim_class": "LOCAL_PROOF",
                "authority_id": "",
                "authority_type": "local",
                "proof_location": "Candidate §3, Lemma 2",
            }
        ]
    )
    assert report.admissible
    assert report.local_proofs


def test_project_foundation_and_semantic_dependencies_resolve(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Trust Test")
    store.add_theorem("UPSTREAM", "Upstream", "A implies B.", status="PROVED")
    semantics, _ = semantic_registry(tmp_path)
    resolver = DependencyAuthorityResolver(
        foundations=FoundationRegistry.load_builtin(),
        semantics=semantics,
        project=store,
        notation_scope="scope-v1",
    )
    report = resolver.resolve(
        [
            {
                "claim": "Jacobi reciprocity for 5",
                "claim_class": "FOUNDATIONAL_THEOREM",
                "authority_id": "FOUND-NT-QR-02",
                "authority_type": "foundation",
            },
            {
                "claim": "primitive core iff h=1",
                "claim_class": "SEMANTIC_DEFINITION",
                "authority_id": "SEM-TEST-01",
                "authority_type": "semantic",
            },
            {
                "claim": "A implies B",
                "claim_class": "PROJECT_THEOREM",
                "authority_id": "UPSTREAM",
                "authority_type": "project_theorem",
            },
        ]
    )
    assert report.admissible
    assert report.foundation_ids_used == ["FOUND-NT-QR-02"]
    assert report.semantics == ["SEM-TEST-01"]
    assert report.project_theorems == ["UPSTREAM"]


def test_context_separates_all_three_authority_layers(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Context Trust")
    source = store.root / "sources" / "semantic.md"
    source.write_text("Definition: core iff h=1.", encoding="utf-8")
    item = {
        "id": "SEM-CONTEXT-01",
        "statement": "core iff h=1",
        "authority_kind": "iff",
        "notation_scope": "scope-v1",
        "notation_version": "v1",
        "provenance": {
            "source_file": "sources/semantic.md",
            "source_hash": file_sha256(source),
            "source_section": "Definition",
            "authority_source": "historical_source_body",
        },
        "version": "1.0.0",
        "content_hash": "",
    }
    item["content_hash"] = content_hash(item, excluded={"content_hash"})
    registry = {
        "schema_version": 1,
        "registry_type": "SEMANTIC",
        "registry_id": "context-semantics",
        "version": "1.0.0",
        "items": [item],
        "registry_hash": "",
    }
    write_registry(store.root / "semantics" / "registry.json", registry)
    store.add_theorem("UP", "Upstream", "A.", status="PROVED")
    store.add_theorem(
        "TARGET",
        "Target",
        "B.",
        dependencies=["UP"],
        notation_scope="scope-v1",
    )
    package = ContextBuilder(store).build("TARGET")
    assert "## Foundations" in package.markdown
    assert "## Semantics" in package.markdown
    assert "## Project Theorems" in package.markdown
    assert "SEM-CONTEXT-01" in package.markdown
