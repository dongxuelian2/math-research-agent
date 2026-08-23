from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from math_research_agent.research.checkpoint_migration import (
    CheckpointClassification,
    LegacyCheckpointMigrator,
    checkpoint_policy_fingerprint,
)
from math_research_agent.research.orchestrator import ResearchOrchestrator
from math_research_agent.research.project import ProjectError, ProjectStore


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _project(tmp_path: Path) -> ProjectStore:
    project = ProjectStore.initialize(tmp_path / "project", "Checkpoint migration")
    project.add_theorem("target", "Target", "Prove the target.")
    return project


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "models.mock.json"


def _migrator(project: ProjectStore) -> LegacyCheckpointMigrator:
    config = json.loads(_config_path().read_text(encoding="utf-8"))
    return LegacyCheckpointMigrator(
        project,
        config_path=_config_path(),
        target_policy_fingerprint=checkpoint_policy_fingerprint(config),
    )


def _write_checkpoint(project: ProjectStore, name: str, state: dict) -> tuple[Path, bytes]:
    run_dir = project.root / "runs" / name
    run_dir.mkdir(parents=True)
    raw = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
    (run_dir / "state.json").write_bytes(raw)
    return run_dir, raw


def _direct_compatibility() -> dict[str, bool]:
    return {
        "assertion_identity_exact": True,
        "artifact_hashes_valid": True,
        "dependency_snapshot_reconstructable": True,
        "authority_compatible": True,
        "verifier_provenance_sufficient": True,
        "trust_policy_compatible": True,
        "runtime_state_compatible": True,
    }


def test_legacy_exact_compatible_is_direct_import_and_source_is_immutable(tmp_path: Path):
    project = _project(tmp_path)
    source_dir, original = _write_checkpoint(
        project,
        "legacy-exact",
        {
            "schema_version": 1,
            "target_id": "target",
            "phase": "CREATED",
            "migration_compatibility": _direct_compatibility(),
        },
    )

    result = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )
    repeated = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )

    assert result.classification == CheckpointClassification.DIRECT_IMPORT.value
    assert result.resumable is True
    assert result.runtime_run_dir != source_dir
    assert (source_dir / "state.json").read_bytes() == original
    assert result.source_snapshot_path.read_bytes() == original
    assert repeated.provenance_path == result.provenance_path
    migrated = json.loads(result.migrated_checkpoint_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["checkpoint_migration_classification"] == "DIRECT_IMPORT"


def test_revalidation_keeps_legacy_evidence_and_separates_provider_provenance(tmp_path: Path):
    project = _project(tmp_path)
    source_dir, _ = _write_checkpoint(
        project,
        "legacy-verified",
        {
            "schema_version": 1,
            "target_id": "target",
            "phase": "CANDIDATE_READY",
            "status": "PROVED",
            "provider_provenance": {"provider": "codex_cli", "model": "gpt-5"},
            "verifier_provenance": {"provider": "openai", "model": "gpt-5.1"},
        },
    )

    result = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )
    migrated = json.loads(result.migrated_checkpoint_path.read_text(encoding="utf-8"))

    assert result.classification == "REVALIDATION_REQUIRED"
    assert migrated["status"] == "RUNNING"
    assert migrated["phase"] == "CREATED"
    assert migrated["legacy_evidence_state"] == "LEGACY_VERIFIED"
    assert migrated["status"] not in {"PROVED", "TRUSTED_EVIDENCE"}
    assert migrated["execution_provider_provenance"]["provider"] == "codex_cli"
    assert migrated["evidence_provider_provenance"]["provider"] == "openai"


def test_archive_only_retains_source_and_cannot_drive_production(tmp_path: Path):
    project = _project(tmp_path)
    source_dir, original = _write_checkpoint(
        project,
        "legacy-archive",
        {
            "schema_version": 0,
            "target_id": "target",
            "migration_compatibility": {"runtime_ontology_recoverable": False},
        },
    )

    result = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )

    assert result.classification == "ARCHIVE_ONLY"
    assert result.resumable is False
    assert result.source_snapshot_path.read_bytes() == original
    assert (source_dir / "state.json").read_bytes() == original
    with pytest.raises(ProjectError, match="classified ARCHIVE_ONLY"):
        ResearchOrchestrator(project, "target", config_path=_config_path(), resume=source_dir)


def test_missing_declared_mathematical_artifact_is_archive_only(tmp_path: Path):
    project = _project(tmp_path)
    source_dir, _ = _write_checkpoint(
        project,
        "legacy-missing-artifact",
        {
            "schema_version": 1,
            "target_id": "target",
            "artifacts": [{"path": "proof.md", "sha256": _sha(b"missing proof body")}],
        },
    )

    result = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )

    assert result.classification == "ARCHIVE_ONLY"
    assert "artifact body is missing" in result.reason


def test_only_known_semantic_conflict_is_incompatible(tmp_path: Path):
    project = _project(tmp_path)
    source_dir, _ = _write_checkpoint(
        project,
        "legacy-conflict",
        {
            "schema_version": 1,
            "target_id": "different-target",
            "migration_compatibility": {},
        },
    )

    result = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )
    assert result.classification == "INCOMPATIBLE"
    assert "known semantic conflict" in result.reason


def test_unknown_schema_defaults_to_revalidation_required(tmp_path: Path):
    project = _project(tmp_path)
    source_dir, _ = _write_checkpoint(
        project,
        "legacy-unknown",
        {
            "schema_version": 947,
            "target_id": "target",
            "worker_count": "not-an-integer",
            "canonical_source_roots": 17,
            "opaque": {"value": 1},
        },
    )

    result = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )

    assert result.classification == "REVALIDATION_REQUIRED"
    provenance = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert provenance["source_schema"] == 947
    assert provenance["source_hash"] == _sha((source_dir / "state.json").read_bytes())


def test_hash_only_authority_cannot_be_declared_direct_import(tmp_path: Path):
    project = _project(tmp_path)
    compatibility = _direct_compatibility()
    source_dir, _ = _write_checkpoint(
        project,
        "legacy-hash-only-flat",
        {
            "schema_version": 1,
            "target_id": "target",
            "phase": "CREATED",
            "migration_compatibility": compatibility,
            "canonical_authority": [
                {
                    "logical_name": "legacy-proof-source",
                    "canonical_filename": "proof.md",
                    "purpose": "proof_authority",
                    "requesting_obligation_id": "target",
                    "hash": _sha(b"unavailable body"),
                    "summary": "A summary is not authority.",
                }
            ],
        },
    )

    result = _migrator(project).prepare(
        source_dir, expected_target_id="target", expected_campaign_id=None
    )
    migrated = json.loads(result.migrated_checkpoint_path.read_text(encoding="utf-8"))

    assert result.classification == "REVALIDATION_REQUIRED"
    assert migrated["canonical_source_requirements"][0]["purpose"] == "proof_authority"
    assert migrated["canonical_source_requirements"][0]["expected_sha256"] == (
        _sha(b"unavailable body")
    )


def test_production_resume_migrates_then_revalidates_canonical_body(tmp_path: Path):
    project = _project(tmp_path)
    body = b"canonical legacy dependency\n"
    canonical = project.root / "sources" / "campaign.md"
    canonical.write_bytes(body)
    requirement = {
        "logical_name": "legacy-campaign",
        "canonical_filename": "campaign.md",
        "canonical_path": "sources/campaign.md",
        "expected_sha256": _sha(body),
        "authority_source": "legacy-registry",
        "registry_record": {"registry_id": "legacy-source"},
        "purpose": "proof_authority",
        "requesting_obligation_id": "target",
        "contextual_paths": [],
    }
    source_dir, original = _write_checkpoint(
        project,
        "legacy-hash-only",
        {
            "schema_version": 1,
            "target_id": "target",
            "phase": "CHECKPOINT",
            "provider_provenance": {"provider": "gemini", "model": "gemini-legacy"},
            "canonical_source_requirements": [requirement],
            "canonical_authority": [
                {
                    "requirement": requirement,
                    "resolution_status": "RESOLVED_CANONICAL",
                    "computed_sha256": _sha(body),
                    "body": None,
                    "requesting_obligation_id": "target",
                }
            ],
        },
    )

    orchestrator = ResearchOrchestrator(
        project, "target", config_path=_config_path(), resume=source_dir
    )
    try:
        assert orchestrator.run_dir != source_dir
        assert orchestrator.state["checkpoint_migration_classification"] == (
            "REVALIDATION_REQUIRED"
        )
        assert orchestrator.state["execution_provider_provenance"]["provider"] == "gemini"
        assert orchestrator.canonical_authority[0]["resolution_status"] == ("RESOLVED_CANONICAL")
        assert orchestrator.canonical_authority[0]["body"] == body.decode()
        assert (source_dir / "state.json").read_bytes() == original
    finally:
        orchestrator.close()
