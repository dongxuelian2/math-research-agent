from __future__ import annotations

import hashlib
import json
from pathlib import Path

from math_research_agent.research.canonical_artifacts import (
    CanonicalArtifactResolver,
    CanonicalResolutionStatus,
    CanonicalSourceRequirement,
    authority_promotion_decision,
)
from math_research_agent.research.pipelines import AsyncDAGScheduler
from math_research_agent.research.campaign import ReplayPolicy
from math_research_agent.research.orchestrator import ResearchOrchestrator
from math_research_agent.research.project import ProjectStore
from math_research_agent.research.retrieval import ContextBuilder


def _sha(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _file_sha(path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path) -> ProjectStore:
    project = ProjectStore.initialize(tmp_path / "project", "Canonical authority")
    project.add_theorem("target", "Target", "Prove the target.")
    return project


def _requirement(**updates) -> CanonicalSourceRequirement:
    value = {
        "logical_name": "campaign-definition",
        "canonical_filename": "campaign.md",
        "canonical_path": "sources/campaign.md",
        "expected_sha256": None,
        "authority_source": "replay-manifest-v1",
        "registry_record": {"registry_id": "campaign-source-registry"},
        "purpose": "proof_authority",
        "requesting_obligation_id": "target",
    }
    value.update(updates)
    return CanonicalSourceRequirement.from_dict(value)


def test_a_body_present_matching_hash_reaches_worker_verifier_planner_and_auditor(tmp_path):
    project = _project(tmp_path)
    body = "# Canonical campaign\n\nC = 5^{c0}\n"
    source = project.root / "sources" / "campaign.md"
    source.write_text(body, encoding="utf-8")
    actual_body = source.read_bytes().decode("utf-8")
    requirement = _requirement(expected_sha256=_file_sha(source))
    resolution = CanonicalArtifactResolver(project, run_dir=tmp_path / "run").resolve(requirement)
    raw = resolution.to_dict()

    assert raw["resolution_status"] == "RESOLVED_CANONICAL"
    assert raw["body"] == actual_body
    assert raw["computed_sha256"] == _file_sha(source)

    scheduler = AsyncDAGScheduler(state_path=tmp_path / "pipeline.json")
    scheduler.add_obligation(
        "target",
        target_statement="Prove the target.",
        canonical_source_requirements=[requirement.to_dict()],
        canonical_authority=[raw],
    )
    proof = scheduler.dispatch_window({"proof": 1, "literature": 0, "verification": 0})["proof"][0]
    worker_source = proof["payload"]["canonical_authority_sources"][0]
    scheduler.complete_task(proof["task_id"], {"proof_candidate": True})
    verifier = scheduler.dispatch_window({"proof": 0, "literature": 0, "verification": 1})[
        "verification"
    ][0]
    verifier_source = verifier["payload"]["canonical_authority_sources"][0]
    assert verifier_source == worker_source

    package = ContextBuilder(project).build("target", canonical_authority=[raw])
    assert raw in package.data["canonical_authority"]
    assert "CANONICAL AUTHORITY SOURCE" in package.markdown
    assert actual_body in package.markdown


def test_b_missing_body_with_extract_is_noncanonical_and_blocks_promotion(tmp_path):
    project = _project(tmp_path)
    extract = project.root / "reports" / "migration-extract.md"
    extract.write_text("summary pretending to be a theorem", encoding="utf-8")
    requirement = _requirement(contextual_paths=["reports/migration-extract.md"])
    raw = CanonicalArtifactResolver(project).resolve(requirement).to_dict()

    assert raw["resolution_status"] == "NONCANONICAL_ONLY"
    assert raw["body"] is None
    scheduler = AsyncDAGScheduler(state_path=tmp_path / "pipeline.json")
    obligation = scheduler.add_obligation(
        "target",
        target_statement="dependent",
        canonical_source_requirements=[requirement.to_dict()],
        canonical_authority=[raw],
    )
    assert obligation["status"] == "BLOCKED_AUTHORITY_NONCANONICAL_ONLY"
    assert authority_promotion_decision([raw])[0] is False


def test_c_wrong_hash_blocks_and_keeps_candidate_diagnostic_only(tmp_path):
    project = _project(tmp_path)
    (project.root / "sources" / "campaign.md").write_text("wrong", encoding="utf-8")
    diagnostic = project.root / "reports" / "candidate.md"
    diagnostic.write_text("candidate evidence", encoding="utf-8")
    requirement = _requirement(
        expected_sha256=_sha("expected"), contextual_paths=["reports/candidate.md"]
    )
    raw = CanonicalArtifactResolver(project).resolve(requirement).to_dict()

    assert raw["resolution_status"] == "HASH_MISMATCH"
    assert raw["body"] is None
    assert str(diagnostic) in raw["diagnostic_locations"]
    assert authority_promotion_decision([raw])[1][0]["type"] == ("BLOCKED_AUTHORITY_HASH_MISMATCH")


def test_d_same_filename_uses_hash_or_reports_ambiguity(tmp_path):
    project = _project(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "campaign.md").write_text("wrong", encoding="utf-8")
    (second / "campaign.md").write_text("right", encoding="utf-8")
    resolver = CanonicalArtifactResolver(project, configured_roots=[first, second])

    matched = resolver.resolve(_requirement(canonical_path=None, expected_sha256=_sha("right")))
    assert matched.resolution_status == "RESOLVED_CANONICAL"
    assert matched.body == "right"
    ambiguous = resolver.resolve(_requirement(canonical_path=None))
    assert ambiguous.resolution_status == "AMBIGUOUS_CANONICAL"


def test_e_filename_hash_manifest_without_body_is_not_resolved(tmp_path):
    project = _project(tmp_path)
    raw = CanonicalArtifactResolver(project).resolve(
        _requirement(expected_sha256=_sha("body that is not present"))
    )
    assert raw.resolution_status == CanonicalResolutionStatus.MISSING_CANONICAL.value
    assert raw.body is None


def test_f_authority_block_is_branch_scoped_and_independent_branch_runs(tmp_path):
    project = _project(tmp_path)
    missing = CanonicalArtifactResolver(project).resolve(_requirement()).to_dict()
    scheduler = AsyncDAGScheduler(state_path=tmp_path / "pipeline.json")
    scheduler.add_obligation(
        "target",
        target_statement="dependent",
        canonical_authority=[missing],
    )
    scheduler.add_obligation("independent", target_statement="independent")

    window = scheduler.dispatch_window({"proof": 2, "literature": 0, "verification": 0})
    assert [task["obligation_id"] for task in window["proof"]] == ["independent"]
    assert scheduler.snapshot()["obligations"]["target"]["status"].startswith("BLOCKED_AUTHORITY_")


def test_g_checkpoint_resume_reuses_immutable_body_or_detects_mutation(tmp_path):
    project = _project(tmp_path)
    source = project.root / "sources" / "campaign.md"
    source.write_text("version one", encoding="utf-8")
    requirement = _requirement()
    run_dir = tmp_path / "run"
    first = CanonicalArtifactResolver(project, run_dir=run_dir).resolve(requirement).to_dict()
    source.write_text("mutated version", encoding="utf-8")

    resumed = CanonicalArtifactResolver(project, run_dir=run_dir).resolve_all(
        [requirement], previous_resolutions=[first]
    )[0]
    assert resumed.resolution_status == "RESOLVED_CANONICAL"
    assert resumed.body == "version one"
    assert resumed.checkpoint_sha256 == first["computed_sha256"]

    uncached = CanonicalArtifactResolver(
        project, immutable_cache=tmp_path / "empty-cache"
    ).resolve_all([requirement], previous_resolutions=[first])[0]
    assert uncached.resolution_status == "HASH_MISMATCH"


def test_h_canonical_formula_wins_and_wrong_extract_never_reconstructs_authority(tmp_path):
    project = _project(tmp_path)
    canonical = "C = 5^{c0}\ns = c0 - F\nw = p - J*l*5^s\n"
    wrong_extract = "w = p - J*l*C\n"
    source = project.root / "sources" / "campaign.md"
    source.write_text(canonical, encoding="utf-8")
    extract = project.root / "reports" / "migration-extract.md"
    extract.write_text(wrong_extract, encoding="utf-8")
    actual_canonical = source.read_bytes().decode("utf-8")
    requirement = _requirement(
        expected_sha256=_file_sha(source), contextual_paths=["reports/migration-extract.md"]
    )

    resolved = CanonicalArtifactResolver(project).resolve(requirement)
    assert resolved.body == actual_canonical
    assert wrong_extract not in resolved.body
    source.unlink()
    blocked = CanonicalArtifactResolver(project).resolve(requirement)
    assert blocked.resolution_status == "NONCANONICAL_ONLY"
    assert blocked.body is None


def test_t8_production_resume_accepts_unchanged_claim(tmp_path):
    project = _project(tmp_path)
    source = project.root / "sources" / "campaign.md"
    source.write_text("canonical production body", encoding="utf-8")
    manifest = tmp_path / "replay.json"
    manifest.write_text(
        json.dumps(
            {
                "canonical_source_requirements": [
                    _requirement(expected_sha256=_file_sha(source)).to_dict()
                ]
            }
        ),
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[2]
    orchestrator = ResearchOrchestrator(
        project,
        "target",
        config_path=repository_root / "tests" / "fixtures" / "models.mock.toml",
        replay_policy=ReplayPolicy.from_manifest(manifest),
        hard_submit_gate=True,
    )
    try:
        state = orchestrator.run(stop_after="context")
        assert state["phase"] == "CONTEXT_READY"
        context = json.loads(
            (orchestrator.run_dir / "context" / "context.json").read_text(encoding="utf-8")
        )
        assert context["canonical_authority"][0]["body"] == "canonical production body"
        task = next(iter(orchestrator.pipeline_scheduler.snapshot()["tasks"].values()))
        assert task["payload"]["canonical_authority_sources"][0]["computed_sha256"] == (
            _file_sha(source)
        )
    finally:
        orchestrator.close()
    source.unlink()
    resumed = ResearchOrchestrator(
        project,
        "target",
        config_path=repository_root / "tests" / "fixtures" / "models.mock.toml",
        resume=orchestrator.run_dir,
    )
    try:
        assert resumed.canonical_authority[0]["resolution_status"] == "RESOLVED_CANONICAL"
        assert resumed.canonical_authority[0]["body"] == "canonical production body"
        assert resumed.state["canonical_source_requirements"][0]["purpose"] == "proof_authority"
        assert resumed.truth_resume_blocked is False
        assert resumed.state["truth_resume_validation"]["status"] == "MATCH"
    finally:
        resumed.close()


def test_t9_production_resume_blocks_when_root_assertion_changed(tmp_path):
    project = _project(tmp_path)
    repository_root = Path(__file__).resolve().parents[2]
    orchestrator = ResearchOrchestrator(
        project,
        "target",
        config_path=repository_root / "tests" / "fixtures" / "models.mock.toml",
    )
    try:
        assert orchestrator.run(stop_after="context")["phase"] == "CONTEXT_READY"
        run_dir = orchestrator.run_dir
    finally:
        orchestrator.close()

    theorem = project.load_theorem("target")
    theorem["statement"] = "A materially different target assertion."
    project.update_theorem(theorem)

    resumed = ResearchOrchestrator(
        project,
        "target",
        config_path=repository_root / "tests" / "fixtures" / "models.mock.toml",
        resume=run_dir,
    )
    try:
        state = resumed.run()
        assert resumed.truth_resume_blocked is True
        assert state["phase"] == "CHECKPOINT"
        assert state["status"] == "BLOCKED_CLAIM_SNAPSHOT_STALE"
        assert state["truth_resume_validation"]["status"] == "ASSERTION_CHANGED"
        assert not (run_dir / "CANDIDATE_PROOF.md").exists()
    finally:
        resumed.close()


def test_production_missing_authority_checkpoints_without_candidate(tmp_path):
    project = _project(tmp_path)
    manifest = tmp_path / "replay.json"
    manifest.write_text(
        json.dumps({"canonical_source_requirements": [_requirement().to_dict()]}),
        encoding="utf-8",
    )
    repository_root = Path(__file__).resolve().parents[2]
    orchestrator = ResearchOrchestrator(
        project,
        "target",
        config_path=repository_root / "tests" / "fixtures" / "models.mock.toml",
        replay_policy=ReplayPolicy.from_manifest(manifest),
        hard_submit_gate=True,
    )
    try:
        state = orchestrator.run()
        assert state["phase"] == "CHECKPOINT"
        assert state["status"] == "BLOCKED_AUTHORITY_SOURCE_UNAVAILABLE"
        assert not (orchestrator.run_dir / "CANDIDATE_PROOF.md").exists()
        assert (
            orchestrator.pipeline_scheduler.snapshot()["obligations"]["target"]["status"]
            == "BLOCKED_AUTHORITY_SOURCE_UNAVAILABLE"
        )
    finally:
        orchestrator.close()
