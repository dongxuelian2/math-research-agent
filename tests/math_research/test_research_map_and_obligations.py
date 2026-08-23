from __future__ import annotations

import json

import pytest

from math_research_agent.research.project import ProjectError, ProjectStore
from math_research_agent.research.research_map import MapRevisionReason
from math_research_agent.research.research_obligation import ObligationDisposition
from math_research_agent.research.research_store import (
    ResearchMapRootStale,
    ResearchStoreFacade,
    classify_legacy_checkpoint_research_frontier,
    research_checkpoint_projection,
)
from math_research_agent.research.truth_store import TruthStoreFacade


def _research(tmp_path, obligation_ids=("O1",)):
    project = ProjectStore.initialize(tmp_path / "project", "Research Plane")
    project.add_theorem("T1", "Root theorem", "For every n, P(n).")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    store = ResearchStoreFacade(project, truth_store=truth)
    research_map = store.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": item,
                "title": f"Obligation {item}",
                "statement": f"Establish the scoped claim {item}",
                "obligation_kind": "LEMMA",
                "scope": [f"scope:{item}"],
            }
            for item in obligation_ids
        ],
        created_by="test",
        strategic_thesis="Decompose the root into explicit durable obligations.",
    )
    return project, truth, store, snapshot, research_map


def test_r1_immutable_map_versions_and_unknown_fields_fail_closed(tmp_path):
    _, _, store, _, v1 = _research(tmp_path)
    with pytest.raises(ProjectError, match="DESTRUCTIVE_REFRAME_REQUIRES_GOVERNANCE"):
        store.revise_map(
            v1,
            created_by="test",
            revision_reason=MapRevisionReason.HUMAN_STEERING.value,
            strategic_thesis="Keep the same scope with a refined thesis.",
        )
    v2 = store.revise_map(
        v1,
        created_by="test",
        revision_reason=MapRevisionReason.HUMAN_STEERING.value,
    )
    assert v1.version == 1
    assert v2.version == 2
    assert v2.parent_version_ref == v1.research_map_hash
    assert store.load_map(v1.research_map_hash) == v1
    assert store.load_current_map(v1.research_map_id) == v2

    path = next((store.maps_root / v1.research_map_id / "versions").glob("00000001-*.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["theorem_proved"] = True
    with pytest.raises(ProjectError, match="unknown=.*theorem_proved"):
        type(v1).from_dict(value)


def test_r2_map_is_non_authoritative_and_cannot_mutate_theorem_truth(tmp_path):
    project, _, store, _, v1 = _research(tmp_path)
    before = project.load_theorem("T1")
    with pytest.raises(ProjectError, match="DESTRUCTIVE_REFRAME_REQUIRES_GOVERNANCE"):
        store.revise_map(
            v1,
            created_by="test",
            revision_reason=MapRevisionReason.EVIDENCE_INTEGRATION.value,
            strategic_thesis="Research judgment only; no Truth Plane mutation.",
        )
    after = project.load_theorem("T1")
    assert before["status"] == after["status"] == "OPEN"
    assert before == after
    assert store.load_current_map(v1.research_map_id) == v1


def test_r3_r4_r5_obligation_survives_task_session_and_crash_lifecycle(tmp_path):
    _, _, store, _, v1 = _research(tmp_path)
    ref = v1.obligation_ref("O1")
    obligation = store.load_obligation(ref.obligation_hash)

    execution_task = {"task_id": "task-1", "status": "RUNNING", "obligation_id": "O1"}
    del execution_task
    reloaded = ResearchStoreFacade(store.project).load_current_map(v1.research_map_id)
    assert reloaded.obligation_ref("O1") == ref
    assert store.load_obligation(ref.obligation_hash) == obligation

    with pytest.raises(ProjectError, match="Execution state"):
        ObligationDisposition.capture(
            obligation_id="O1",
            obligation_hash=obligation.obligation_hash,
            disposition="RUNNING",
            recorded_at=obligation.created_at,
            recorded_by="test",
        )


def test_r8_multi_obligation_revision_cannot_silently_drop_scope(tmp_path):
    _, _, store, _, v1 = _research(tmp_path, ("O1", "O2", "O3"))
    o1 = v1.obligation_ref("O1")
    with pytest.raises(ProjectError, match="NO_SCOPE_LOSS"):
        store.revise_map(
            v1,
            obligation_refs=[o1],
            created_by="test",
            revision_reason=MapRevisionReason.HUMAN_STEERING.value,
        )
    assert store.load_current_map(v1.research_map_id) == v1


def test_r9_explicit_supersession_preserves_scope_and_provenance(tmp_path):
    _, _, store, _, v1 = _research(tmp_path, ("O1", "O2"))
    _, v2 = store.add_obligation(
        v1.research_map_id,
        obligation_id="O3",
        title="Replacement obligation",
        statement="A sharper formulation of O2",
        obligation_kind="LEMMA",
        created_by="test",
    )
    decision, v3 = store.record_disposition(
        v2.research_map_id,
        "O2",
        disposition="SUPERSEDED",
        superseded_by=("O3",),
        reason="O3 covers the exact research scope with a sharper statement",
        recorded_by="test",
        revision_reason=MapRevisionReason.SCOPE_SUPERSESSION.value,
    )
    assert decision.previous_disposition_hash
    assert decision.superseded_by == ("O3",)
    assert {item.obligation_id for item in v3.obligation_refs} == {"O1", "O2", "O3"}
    assert v3.obligation_ref("O2").disposition == "SUPERSEDED"


def test_r12_stale_root_blocks_revision_until_explicit_rebase(tmp_path):
    project, truth, store, old_snapshot, v1 = _research(tmp_path, ("O1", "O2"))
    theorem = project.load_theorem("T1")
    theorem["statement"] = "For every n, Q(n)."
    project.update_theorem(theorem)
    new_snapshot = truth.capture_claim_snapshot("T1")

    with pytest.raises(ResearchMapRootStale, match="RESEARCH_MAP_ROOT_STALE"):
        store.revise_map(
            v1,
            created_by="test",
            revision_reason=MapRevisionReason.HUMAN_STEERING.value,
        )

    rebase, v2 = store.rebase_research_map(
        v1.research_map_id,
        new_claim_snapshot_hash=new_snapshot.claim_snapshot_hash,
        carried_obligation_ids=("O1",),
        revalidation_required_obligation_ids=("O2",),
        invalid_obligation_ids=(),
        reason="Human-approved continuation under the changed root assertion",
        created_by="test",
    )
    assert rebase.old_claim_snapshot_hash == old_snapshot.claim_snapshot_hash
    assert rebase.new_claim_snapshot_hash == new_snapshot.claim_snapshot_hash
    assert rebase.compatibility_status == "ASSERTION_CHANGED"
    assert v2.revision_reason == "ROOT_REBASE"
    assert v2.obligation_ref("O1").disposition == "OPEN"
    assert v2.obligation_ref("O2").disposition == "BLOCKED"


def test_r16_checkpoint_frontier_roundtrip_and_legacy_revalidation(tmp_path):
    _, _, store, _, v1 = _research(tmp_path, ("O1", "O2"))
    projection = research_checkpoint_projection(
        store, v1.research_map_id, active_directive_id="directive-1"
    )
    assert projection["research_map_version"] == 1
    assert projection["open_obligation_ids"] == ["O1", "O2"]
    assert projection["root_claim_snapshot_hash"] == v1.root_claim_snapshot_hash
    assert classify_legacy_checkpoint_research_frontier(projection) == "DIRECT_IMPORT"
    assert classify_legacy_checkpoint_research_frontier({"candidate_file": "candidate.md"}) == (
        "REVALIDATION_REQUIRED"
    )
