from __future__ import annotations

import json
import shutil
from pathlib import Path

from openprover.math_research.campaign import CampaignStore
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectStore
from openprover.math_research.research_evidence import ProviderProvenance
from openprover.math_research.research_store import ResearchStoreFacade
from openprover.math_research.truth_store import TruthStoreFacade


def test_r19_production_research_plane_and_separate_truth_mutation_e2e(tmp_path):
    repository_root = Path(__file__).resolve().parents[3]
    project_root = tmp_path / "project"
    shutil.copytree(repository_root / "projects" / "demo", project_root)
    theorem_path = project_root / "theorems" / "demo-odd-sum.json"
    theorem = json.loads(theorem_path.read_text(encoding="utf-8"))
    theorem.update({"status": "OPEN", "proof_file": "", "last_run": "", "history": []})
    theorem_path.write_text(json.dumps(theorem, indent=2) + "\n", encoding="utf-8")
    project = ProjectStore(project_root)

    state = ResearchOrchestrator(
        project,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
    ).run()

    assert state["obligation_resolution_status"] == "RESOLUTION_ACCEPTED"
    assert state["research_map_version"] == 2
    assert state["open_obligation_ids"] == []
    research_store = ResearchStoreFacade(project)
    current = research_store.load_current_map(state["research_map_id"])
    assert current.obligation_refs[0].disposition == "RESOLVED"
    closure = research_store.load_session_closure(state["tactical_session_id"])
    assert {item.evidence_kind for item in closure.validated_evidence} >= {
        "CANDIDATE",
        "VERIFIER",
        "AUDIT",
    }
    assert len(closure.raw_artifacts) >= 5

    # Research resolution did not promote truth. The same run performed an
    # independent, receipt-backed PHASE 3 TruthMutation afterwards.
    assert state["truth_mutation_id"]
    assert state["truth_mutation_receipt_hash"]
    assert project.load_theorem("demo-odd-sum")["status"] == "PROVED"


def test_r20_multi_obligation_session_has_no_scope_loss(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Multi obligation")
    project.add_theorem("T1", "Root", "P, Q, and R all hold.")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    store = ResearchStoreFacade(project, truth_store=truth)
    v1 = store.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": oid,
                "title": oid,
                "statement": f"Resolve {oid}",
                "obligation_kind": "LEMMA",
                "scope": [oid],
            }
            for oid in ("O1", "O2", "O3")
        ],
        created_by="test",
    )
    directive = store.create_directive(
        v1.research_map_id,
        "O1",
        tactical_goal="Resolve O1 only",
        allowed_scope=("O1",),
        created_by="test",
    )
    session = store.bind_tactical_session(
        directive.directive_id, execution_run_id="run-O1", execution_status="RUNNING"
    )
    run = project.root / "runs" / "run-O1"
    run.mkdir(parents=True)
    candidate = run / "candidate.md"
    verifier = run / "verifier.json"
    audit = run / "audit.json"
    candidate.write_text("candidate", encoding="utf-8")
    verifier.write_text('{"verdict":"CORRECT"}', encoding="utf-8")
    audit.write_text('{"outcome":"PASS"}', encoding="utf-8")
    store.close_tactical_session(
        session.tactical_session_id,
        execution_status="COMPLETED",
        raw_artifacts=tuple(
            {"path": path, "artifact_kind": kind, "producer": "test"}
            for path, kind in (
                (candidate, "CANDIDATE"),
                (verifier, "VERIFIER"),
                (audit, "AUDIT"),
            )
        ),
        evidence_specs=(
            {"artifact_path": candidate, "evidence_kind": "CANDIDATE"},
            {
                "artifact_path": verifier,
                "evidence_kind": "VERIFIER",
                "verifier_status": "PASS",
            },
            {
                "artifact_path": audit,
                "evidence_kind": "AUDIT",
                "audit_status": "PASS",
                "authority_status": "TRUSTED",
            },
        ),
        provider_provenance=(
            ProviderProvenance.capture(provider="mock", model="mock", call_refs=()),
        ),
        closed_by="test",
    )
    decision, v2 = store.resolve_session_closure(
        session.tactical_session_id, recorded_by="test"
    )
    assert decision.status == "RESOLUTION_ACCEPTED"
    assert v2 is not None
    assert {item.obligation_id: item.disposition for item in v2.obligation_refs} == {
        "O1": "RESOLVED",
        "O2": "OPEN",
        "O3": "OPEN",
    }
    assert project.load_theorem("T1")["status"] == "OPEN"


def test_successor_run_is_execution_lineage_for_same_research_frontier(tmp_path):
    project = ProjectStore.initialize(tmp_path / "project", "Successor lineage")
    project.add_theorem("T1", "Root", "P holds.")
    campaigns = CampaignStore(project)
    campaign = campaigns.create(
        "campaign-T1",
        target_id="T1",
        max_repair_cycles=1,
        auto_successor=True,
    )
    campaigns.register_initial("campaign-T1", "run-1")
    campaigns.mark_run("campaign-T1", "run-1", status="REJECTED")
    child = campaigns.create_successor("campaign-T1", parent_run_id="run-1")
    loaded = campaigns.load("campaign-T1")
    parent = loaded["runs"][0]
    assert child["research_binding"] == parent["research_binding"]
    assert child["research_binding"]["research_map_id"] == campaign["research_map_id"]
    assert child["research_binding"]["semantic_role"] == "EXECUTION_LINEAGE_ONLY"
