from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from math_research_agent.research.claim_snapshot import SnapshotComparison
from math_research_agent.research.orchestrator import ResearchOrchestrator
from math_research_agent.research.phase7 import Phase7Error, Phase7Store
from math_research_agent.research.project import ProjectStore
from math_research_agent.research.research_store import ResearchStoreFacade
from math_research_agent.research.state_machine import AuditGate
from math_research_agent.research.truth_store import TruthStoreFacade


def _demo_project(tmp_path: Path) -> tuple[Path, ProjectStore]:
    repository_root = Path(__file__).resolve().parents[2]
    project_root = tmp_path / "project"
    shutil.copytree(repository_root / "projects" / "demo", project_root)
    theorem_path = project_root / "theorems" / "demo-odd-sum.json"
    theorem = json.loads(theorem_path.read_text(encoding="utf-8"))
    theorem.update({"status": "OPEN", "proof_file": "", "last_run": "", "history": []})
    theorem_path.write_text(json.dumps(theorem, indent=2) + "\n", encoding="utf-8")
    return repository_root, ProjectStore(project_root)


def test_phase7_normal_path_persists_and_resumes(tmp_path):
    repository_root, project = _demo_project(tmp_path)
    orchestrator = ResearchOrchestrator(
        project,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
    )
    state = orchestrator.run()

    assert state["phase7_implementation_status"] == "COMPLETE"
    assert state["phase7_state"] == "PROMOTION_CLOSED"
    assert state["phase"] == "COMPLETE"
    assert state["owner_override_phase7_implementation"] is True
    assert state["pre_root_synthesis_certified"] is False
    assert state["phase7_formally_authorized"] is False
    assert state["final_system_certified"] is False

    phase7 = Phase7Store(project)
    synthesis = phase7.load_root_synthesis(state["phase7_root_synthesis_hash"])
    consolidation = phase7.load_final_consolidation(state["phase7_final_consolidation_hash"])
    closure = phase7.verify_promotion_closure(
        state["phase7_promotion_closure_hash"], truth_store=TruthStoreFacade(project)
    )
    session_closure = ResearchStoreFacade(project).load_session_closure(
        state["tactical_session_id"]
    )
    intent = TruthStoreFacade(project).load_mutation_intent(state["truth_mutation_id"])

    assert synthesis.root_claim_snapshot_hash == session_closure.root_claim_snapshot_hash
    assert synthesis.audited_claim_snapshot_hash == intent.claim_snapshot_hash
    assert consolidation.root_synthesis_hash == synthesis.synthesis_hash
    assert closure.final_consolidation_hash == consolidation.consolidation_hash
    assert closure.resulting_status == "PROVED"
    proof_path = phase7.final_proof_path(consolidation.consolidation_hash)
    assert (
        project.load_theorem("demo-odd-sum")["proof_file"]
        == proof_path.relative_to(project.root).as_posix()
    )
    assert b"MRA_PHASE7_FINAL_CONSOLIDATION" in proof_path.read_bytes()

    resumed = ResearchOrchestrator(
        project,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
        resume=orchestrator.run_dir,
    ).run()
    assert resumed["phase"] == "COMPLETE"
    assert resumed["phase7_promotion_closure_hash"] == state["phase7_promotion_closure_hash"]


def test_phase7_recovery_closes_after_durable_truth_promotion(tmp_path):
    repository_root, project = _demo_project(tmp_path)
    orchestrator = ResearchOrchestrator(
        project,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
    )
    state = orchestrator.run()
    state_path = orchestrator.run_dir / "state.json"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    persisted["phase"] = "AUDITS_READY"
    persisted["phase7_state"] = "TRUTH_PROMOTED"
    persisted["phase7_implementation_status"] = "IN_PROGRESS"
    for key in (
        "phase7_promotion_closure_id",
        "phase7_promotion_closure_hash",
        "phase7_promotion_closure_file",
    ):
        persisted.pop(key, None)
    state_path.write_text(json.dumps(persisted, indent=2) + "\n", encoding="utf-8")

    recovered = ResearchOrchestrator(
        project,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
        resume=orchestrator.run_dir,
    ).run()
    assert recovered["phase"] == "COMPLETE"
    assert recovered["phase7_implementation_status"] == "COMPLETE"
    assert recovered["phase7_promotion_closure_hash"] == state["phase7_promotion_closure_hash"]


def test_phase7_rejects_stale_root_open_frontier_and_failed_gate(tmp_path):
    repository_root, project = _demo_project(tmp_path)
    orchestrator = ResearchOrchestrator(
        project,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
    )
    state = orchestrator.run()
    phase7 = Phase7Store(project)
    truth = TruthStoreFacade(project)
    research = ResearchStoreFacade(project)
    intent = truth.load_mutation_intent(state["truth_mutation_id"])
    audited_snapshot = truth.load_claim_snapshot(intent.claim_snapshot_hash)
    comparison = SnapshotComparison(
        schema_version=1,
        object_type="CLAIM_SNAPSHOT_COMPARISON",
        status="MATCH",
        disposition="COMPATIBLE",
        reason="test fixture",
        stored_claim_snapshot_hash=audited_snapshot.claim_snapshot_hash,
        current_claim_snapshot_hash=audited_snapshot.claim_snapshot_hash,
    )
    gate_data = json.loads((orchestrator.run_dir / "audits" / "gate.json").read_text())
    gate = AuditGate(
        **{key: value for key, value in gate_data.items() if key in AuditGate.__dataclass_fields__}
    )
    current_map = research.load_current_map(state["research_map_id"])
    closure = research.load_session_closure(state["tactical_session_id"])
    candidate = orchestrator.run_dir / "CANDIDATE_PROOF.md"
    audit_artifacts = tuple((orchestrator.run_dir / "audits").glob("*.json")) + (candidate,)

    with pytest.raises(Phase7Error, match="current ClaimSnapshot"):
        phase7.synthesize_root(
            theorem_id="demo-odd-sum",
            claim_snapshot=audited_snapshot,
            snapshot_comparison=replace(
                comparison, status="ASSERTION_CHANGED", disposition="HARD_STALE"
            ),
            research_map=current_map,
            session_closure=closure,
            gate=gate,
            candidate_path=candidate,
            audit_artifacts=audit_artifacts,
        )

    open_map = replace(
        current_map,
        obligation_refs=(replace(current_map.obligation_refs[0], disposition="OPEN"),),
    )
    with pytest.raises(Phase7Error, match="closed ResearchMap frontier"):
        phase7.synthesize_root(
            theorem_id="demo-odd-sum",
            claim_snapshot=audited_snapshot,
            snapshot_comparison=comparison,
            research_map=open_map,
            session_closure=closure,
            gate=gate,
            candidate_path=candidate,
            audit_artifacts=audit_artifacts,
        )

    with pytest.raises(Phase7Error, match="passing AuditGate"):
        phase7.synthesize_root(
            theorem_id="demo-odd-sum",
            claim_snapshot=audited_snapshot,
            snapshot_comparison=comparison,
            research_map=current_map,
            session_closure=closure,
            gate=replace(gate, final_auditor_pass=False),
            candidate_path=candidate,
            audit_artifacts=audit_artifacts,
        )


def test_phase7_resume_rejects_tampered_final_proof(tmp_path):
    repository_root, project = _demo_project(tmp_path)
    orchestrator = ResearchOrchestrator(
        project,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
    )
    state = orchestrator.run()
    proof_path = Path(state["phase7_final_proof_file"])
    proof_path.write_bytes(proof_path.read_bytes() + b"\nTAMPERED\n")

    with pytest.raises(Phase7Error, match="proof body is missing or has changed"):
        ResearchOrchestrator(
            project,
            "demo-odd-sum",
            config_path=repository_root / "configs" / "models.mock.json",
            worker_count=3,
            resume=orchestrator.run_dir,
        ).run()
