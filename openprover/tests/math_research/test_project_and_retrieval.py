import json

from openprover.math_research.project import ProjectStore
from openprover.math_research.retrieval import ContextBuilder


def test_dependency_slice_and_failed_route(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Retrieval Test")
    store.add_theorem("proved-lemma", "Allowed", "A is true.", status="PROVED")
    store.add_theorem("partial-lemma", "Blocked", "B might be true.", status="PARTIAL")
    store.add_theorem(
        "target",
        "Target",
        "Prove C.",
        dependencies=["proved-lemma", "partial-lemma"],
        tags=["valuation"],
    )
    store.record_failed_route(
        route_id="route-one",
        strategy="valuation",
        target="target",
        obtained="A bound",
        failure_point="Cannot control the final factor",
        insufficiency="Missing coprimality",
        recovery_conditions="A coprimality lemma becomes PROVED",
        theorem_ids=["target"],
        tags=["valuation"],
    )
    package = ContextBuilder(store).build("target")
    assert [item["id"] for item in package.data["allowed_dependencies"]] == ["proved-lemma"]
    assert [item["id"] for item in package.data["blocked_dependencies"]] == ["partial-lemma"]
    assert package.data["failed_routes"][0]["id"] == "route-one"
    assert "must not be used as theorems" in package.markdown


def test_import_never_infers_proved_from_filename(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Import Test")
    source = store.root / "inbox" / "proved_results_resolution.md"
    source.write_text("# Allegedly proved results\n\nA statement requiring review.\n", encoding="utf-8")
    candidates = store.import_markdown(store.root / "inbox")
    assert len(candidates) == 1
    theorem = store.load_theorem(candidates[0]["id"])
    assert theorem["status"] == "UNCLASSIFIED"
    assert theorem["audit_status"] == "NOT_AUDITED"

