import json

import pytest

from math_research_agent.research.migration import (
    apply_dependency_repairs,
    validate_staged_batch,
)
from math_research_agent.research.project import ProjectError, ProjectStore
from math_research_agent.research.retrieval import ContextBuilder


BATCH = "batch_01_foundational_global"
SPHERE_OLD = "rational-unit-sphere-parametrization"
SPHERE_NEW = "rational-unit-sphere-primitive-representation"


def make_store(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Premise Test")
    source = store.root / "inbox" / "source.md"
    source.write_text("# Primary source\n\nExplicit proof and provenance.\n", encoding="utf-8")
    return store


def add_premise(store, premise_id="original-problem"):
    return store.add_premise(
        premise_id,
        "Root problem",
        "The project's fixed research premise.",
        node_type="PROJECT_PREMISE",
        source_file="inbox/source.md",
        provenance=[
            {
                "source": "inbox/source.md",
                "section": "Primary source",
                "role": "canonical project statement",
            }
        ],
    )


def record(theorem_id, dependencies=None, *, batch=BATCH):
    return {
        "id": theorem_id,
        "title": theorem_id,
        "statement": f"Statement for {theorem_id}",
        "type": "LEMMA",
        "proposed_status": "PROPOSED_PROVED",
        "confidence": "HIGH",
        "approval_eligible": True,
        "known_conflicts": [],
        "audit_blockers": [],
        "primary_source": "source.md",
        "dependencies": list(dependencies or []),
        "batch": batch,
    }


def batch_one_records():
    return [
        record("T1", ["original-problem"]),
        record("T10", ["original-problem"]),
        record("T2", ["T1"]),
        record("T3", ["T1"]),
        record("T4", [SPHERE_OLD]),
        record("T5", ["original-problem"]),
        record("T6", ["T4"]),
        record("T7", ["T2"]),
        record("T8", ["T1", "T2"]),
        record("T9", ["T1", "T2"]),
    ]


def test_theorem_can_depend_on_active_project_premise(tmp_path):
    store = make_store(tmp_path)
    add_premise(store)
    store.add_theorem("target", "Target", "Statement", dependencies=["original-problem"])
    package = ContextBuilder(store).build("target")
    assert [item["id"] for item in package.data["satisfied_premises"]] == ["original-problem"]
    assert package.data["allowed_dependencies"] == []


def test_theorem_cannot_depend_on_missing_premise(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ProjectError, match="Unknown dependency for target"):
        store.add_theorem("target", "Target", "Statement", dependencies=["missing-premise"])


def test_premise_does_not_count_as_proved_theorem(tmp_path):
    store = make_store(tmp_path)
    add_premise(store)
    assert store.list_theorems() == []
    assert len(store.list_premises()) == 1
    index = json.loads((store.root / "index.json").read_text(encoding="utf-8"))
    assert index["theorems"] == []


def test_premise_does_not_enter_theorem_lifecycle(tmp_path):
    store = make_store(tmp_path)
    premise = add_premise(store)
    assert "status" not in premise
    assert premise["lifecycle_managed"] is False
    with pytest.raises(ProjectError, match="Missing required file"):
        store.transition("original-problem", "PROVED", actor="Archivist", reason="invalid")


def test_theorem_dependency_must_be_proved_for_migration(tmp_path):
    store = make_store(tmp_path)
    store.add_theorem("open-dependency", "Open", "Open statement", status="OPEN")
    result = validate_staged_batch(
        store,
        [record("target", ["open-dependency"])],
        batch=BATCH,
        source_root=store.root / "inbox",
    )
    assert result.approved == []
    assert result.rejected[0]["reasons"] == ["dependency_not_PROVED:open-dependency"]


def test_same_batch_topological_dependency_is_valid(tmp_path):
    store = make_store(tmp_path)
    result = validate_staged_batch(
        store,
        [record("root"), record("child", ["root"])],
        batch=BATCH,
        source_root=store.root / "inbox",
    )
    assert result.passed
    assert result.topological_order == ["root", "child"]


def test_premise_resolution_is_generic_not_string_hardcoded(tmp_path):
    store = make_store(tmp_path)
    add_premise(store, "arbitrary-project-premise")
    generic = validate_staged_batch(
        store,
        [record("generic-target", ["arbitrary-project-premise"])],
        batch=BATCH,
        source_root=store.root / "inbox",
    )
    missing_special_name = validate_staged_batch(
        store,
        [record("special-name-target", ["original-problem"])],
        batch=BATCH,
        source_root=store.root / "inbox",
    )
    assert generic.passed
    assert missing_special_name.unknown_roots == ["original-problem"]


def test_foundational_sphere_lemma_closes_t4_dependency(tmp_path):
    store = make_store(tmp_path)
    store.add_theorem(SPHERE_NEW, "Sphere lemma", "Primitive representation", status="PROVED")
    result = validate_staged_batch(
        store,
        [record("T4", [SPHERE_NEW])],
        batch=BATCH,
        source_root=store.root / "inbox",
    )
    assert result.passed


def test_original_batch_one_failure_regression(tmp_path):
    store = make_store(tmp_path)
    result = validate_staged_batch(
        store,
        batch_one_records(),
        batch=BATCH,
        source_root=store.root / "inbox",
    )
    assert result.approved == []
    assert len(result.rejected) == 10
    assert result.unknown_roots == ["original-problem", SPHERE_OLD]


def test_root_repair_makes_batch_one_dry_run_pass(tmp_path):
    store = make_store(tmp_path)
    add_premise(store)
    store.add_theorem(SPHERE_NEW, "Sphere lemma", "Primitive representation", status="PROVED")
    repaired = apply_dependency_repairs(
        batch_one_records(),
        [
            {
                "canonical_id": "T4",
                "old_dependency": SPHERE_OLD,
                "new_dependency": SPHERE_NEW,
            }
        ],
    )
    result = validate_staged_batch(
        store,
        repaired,
        batch=BATCH,
        source_root=store.root / "inbox",
    )
    assert result.passed
    assert len(result.approved) == 10
    assert result.unknown_roots == []
