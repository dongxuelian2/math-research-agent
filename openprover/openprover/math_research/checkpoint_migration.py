"""Conservative, provenance-first migration for legacy run checkpoints.

Legacy state is forensic input.  It is hashed and snapshotted before a new
current-schema artifact is created; the source checkpoint is never rewritten.
Unknown compatibility requires revalidation, while only an explicit, known
semantic conflict is classified as incompatible.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .project import ProjectError, ProjectStore, utc_now


CURRENT_RUN_STATE_SCHEMA = 2
MIGRATION_IMPLEMENTATION_VERSION = 1
_DIRECT_IMPORT_CHECKS = (
    "assertion_identity_exact",
    "artifact_hashes_valid",
    "dependency_snapshot_reconstructable",
    "authority_compatible",
    "verifier_provenance_sufficient",
    "trust_policy_compatible",
    "runtime_state_compatible",
)
_CURRENT_TRUST_STATES = {"PROVED", "TRUSTED_EVIDENCE", "TrustedEvidence"}


class CheckpointClassification(str, Enum):
    DIRECT_IMPORT = "DIRECT_IMPORT"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    ARCHIVE_ONLY = "ARCHIVE_ONLY"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class CheckpointInspection:
    classification: str
    reason: str
    source_schema: int | str | None
    compatibility_checks: dict[str, bool | None] = field(default_factory=dict)
    canonical_body_bound: bool | None = None


@dataclass(frozen=True, slots=True)
class MigrationProvenance:
    source_artifact_path: str
    source_hash: str
    source_schema: int | str | None
    target_schema: int
    source_policy_fingerprint: str | None
    target_policy_fingerprint: str
    classification: str
    reason: str
    migration_timestamp: str
    migration_implementation_version: int
    source_snapshot_path: str
    migrated_checkpoint_path: str | None
    execution_provider_provenance: Any = None
    evidence_provider_provenance: Any = None
    compatibility_checks: dict[str, bool | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CheckpointMigrationResult:
    classification: str
    reason: str
    provenance_path: Path
    source_snapshot_path: Path
    migrated_checkpoint_path: Path | None = None
    runtime_run_dir: Path | None = None

    @property
    def resumable(self) -> bool:
        return self.runtime_run_dir is not None


def checkpoint_policy_fingerprint(config: Mapping[str, Any]) -> str:
    """Fingerprint the minimal current compatibility boundary, not truth state."""

    boundary = {
        "run_state_schema": CURRENT_RUN_STATE_SCHEMA,
        "routing_state_schema": 2,
        "pipeline_state_schema": 3,
        "canonical_authority_policy": 1,
        "provider_contract": 1,
        "model_config": config,
    }
    encoded = json.dumps(
        boundary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _normalized_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    digest = value.strip().casefold()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    return "sha256:" + digest


def _immutable_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ProjectError(f"Immutable migration artifact collision: {path}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")


def _compatibility(state: Mapping[str, Any]) -> dict[str, Any]:
    raw = state.get("migration_compatibility")
    return dict(raw) if isinstance(raw, dict) else {}


def _safe_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _artifact_hashes_valid(state: Mapping[str, Any], source_dir: Path) -> bool | None:
    compatibility = _compatibility(state)
    artifacts = state.get("artifacts")
    if artifacts is None:
        explicit = compatibility.get("artifact_hashes_valid")
        return explicit if isinstance(explicit, bool) else None
    if not isinstance(artifacts, list):
        return False
    for item in artifacts:
        if not isinstance(item, dict):
            return False
        relative = item.get("path") or item.get("artifact_path")
        expected = _normalized_sha256(item.get("sha256") or item.get("hash"))
        if not relative or not expected:
            return False
        path = Path(str(relative))
        if path.is_absolute():
            return False
        resolved = (source_dir / path).resolve()
        try:
            resolved.relative_to(source_dir.resolve())
        except ValueError:
            return False
        if not resolved.is_file() or _sha256(resolved.read_bytes()) != expected:
            return False
    return True


def _declared_artifact_body_missing(state: Mapping[str, Any], source_dir: Path) -> bool:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        relative = item.get("path") or item.get("artifact_path")
        if not relative:
            continue
        path = Path(str(relative))
        if path.is_absolute():
            continue
        resolved = (source_dir / path).resolve()
        try:
            resolved.relative_to(source_dir.resolve())
        except ValueError:
            continue
        if not resolved.is_file():
            return True
    return False


def _canonical_body_bound(state: Mapping[str, Any]) -> bool | None:
    requirements = state.get("canonical_source_requirements")
    resolutions = state.get("canonical_authority")
    if not isinstance(requirements, list) or not requirements:
        requirements = []
        if isinstance(resolutions, list):
            for item in resolutions:
                if not isinstance(item, dict):
                    continue
                nested = item.get("requirement")
                requirement = nested if isinstance(nested, dict) else item
                purpose = requirement.get("purpose") or item.get("authority_level")
                if purpose in {"proof_authority", "replay_authority"}:
                    requirements.append(requirement)
        if not requirements:
            return None
    if not isinstance(resolutions, list):
        return False
    bound: set[tuple[str, str]] = set()
    for item in resolutions:
        if not isinstance(item, dict) or not isinstance(item.get("body"), str):
            continue
        requirement = item.get("requirement") or {}
        expected = _normalized_sha256(item.get("computed_sha256"))
        if not expected or _sha256(item["body"].encode("utf-8")) != expected:
            continue
        bound.add(
            (
                str(requirement.get("logical_name") or ""),
                str(
                    item.get("requesting_obligation_id")
                    or requirement.get("requesting_obligation_id")
                    or ""
                ),
            )
        )
    required = {
        (str(item.get("logical_name") or ""), str(item.get("requesting_obligation_id") or ""))
        for item in requirements
        if isinstance(item, dict) and item.get("purpose") in {"proof_authority", "replay_authority"}
    }
    return required <= bound


def inspect_legacy_checkpoint(
    state: Mapping[str, Any],
    *,
    source_dir: Path,
    expected_target_id: str,
    expected_campaign_id: str | None,
    target_policy_fingerprint: str,
) -> CheckpointInspection:
    """Classify a parsed checkpoint without mutating it or project truth state."""

    raw_schema = state.get("schema_version")
    schema = raw_schema if isinstance(raw_schema, (int, str)) or raw_schema is None else "UNKNOWN"
    compatibility = _compatibility(state)
    source_target = state.get("target_id")
    source_campaign = state.get("campaign_id")
    if source_target and source_target != expected_target_id:
        return CheckpointInspection(
            CheckpointClassification.INCOMPATIBLE.value,
            "known semantic conflict: checkpoint target differs from requested target",
            schema,
        )
    if expected_campaign_id is not None and source_campaign not in {None, expected_campaign_id}:
        return CheckpointInspection(
            CheckpointClassification.INCOMPATIBLE.value,
            "known semantic conflict: checkpoint campaign differs from requested campaign",
            schema,
        )
    if compatibility.get("known_semantic_conflict") is True:
        return CheckpointInspection(
            CheckpointClassification.INCOMPATIBLE.value,
            str(compatibility.get("conflict_reason") or "known semantic conflict declared"),
            schema,
        )

    archive_reasons = {
        "runtime_ontology_recoverable": "legacy runtime ontology is unrecoverable",
        "mathematical_artifact_bodies_present": "mathematical artifact bodies are missing",
        "trust_provenance_recoverable": "trust provenance is unrecoverable",
        "checkpoint_semantically_usable": "checkpoint cannot drive current production state",
    }
    for key, reason in archive_reasons.items():
        if compatibility.get(key) is False:
            return CheckpointInspection(CheckpointClassification.ARCHIVE_ONLY.value, reason, schema)
    if compatibility.get("assertion_identity_ambiguous") is True:
        return CheckpointInspection(
            CheckpointClassification.ARCHIVE_ONLY.value,
            "assertion identity is ambiguous",
            schema,
        )
    if _declared_artifact_body_missing(state, source_dir):
        return CheckpointInspection(
            CheckpointClassification.ARCHIVE_ONLY.value,
            "declared mathematical artifact body is missing",
            schema,
        )

    checks: dict[str, bool | None] = {
        key: (compatibility.get(key) if isinstance(compatibility.get(key), bool) else None)
        for key in _DIRECT_IMPORT_CHECKS
    }
    if source_target == expected_target_id and checks["assertion_identity_exact"] is None:
        checks["assertion_identity_exact"] = True
    checks["artifact_hashes_valid"] = _artifact_hashes_valid(state, source_dir)
    source_policy = state.get("policy_fingerprint") or state.get("replay_policy_hash")
    if source_policy and source_policy == target_policy_fingerprint:
        checks["trust_policy_compatible"] = True
    body_bound = _canonical_body_bound(state)
    if body_bound is False:
        checks["authority_compatible"] = False

    source_phase = str(state.get("phase") or "CREATED")
    if source_phase not in {"CREATED", "CHECKPOINT"}:
        checks["runtime_state_compatible"] = False
    if all(checks.get(key) is True for key in _DIRECT_IMPORT_CHECKS):
        return CheckpointInspection(
            CheckpointClassification.DIRECT_IMPORT.value,
            "all direct-import identity, artifact, dependency, authority, verifier, policy, and runtime checks passed",
            schema,
            checks,
            body_bound,
        )

    failed = sorted(key for key, value in checks.items() if value is False)
    unknown = sorted(key for key, value in checks.items() if value is None)
    detail = []
    if failed:
        detail.append("failed=" + ",".join(failed))
    if unknown:
        detail.append("unknown=" + ",".join(unknown))
    return CheckpointInspection(
        CheckpointClassification.REVALIDATION_REQUIRED.value,
        "legacy compatibility requires current validation"
        + (": " + "; ".join(detail) if detail else ""),
        schema if schema is not None else "UNKNOWN",
        checks,
        body_bound,
    )


def _provider_provenance(state: Mapping[str, Any]) -> tuple[Any, Any]:
    execution = state.get("execution_provider_provenance")
    if execution is None:
        execution = state.get("provider_provenance") or state.get("routing_provenance")
    evidence = state.get("evidence_provider_provenance")
    if evidence is None:
        evidence = state.get("verifier_provenance") or state.get("audit_provenance")
    return copy.deepcopy(execution), copy.deepcopy(evidence)


def _canonical_requirements(state: Mapping[str, Any], target_id: str) -> list[dict[str, Any]]:
    raw = state.get("canonical_source_requirements")
    if isinstance(raw, list):
        required = {
            "logical_name",
            "canonical_filename",
            "purpose",
            "requesting_obligation_id",
        }
        return [
            copy.deepcopy(item)
            for item in raw
            if isinstance(item, dict) and required <= item.keys()
        ]

    result = []
    authorities = state.get("canonical_authority")
    if not isinstance(authorities, list):
        return result
    for index, item in enumerate(authorities):
        if not isinstance(item, dict):
            continue
        nested = item.get("requirement")
        authority = nested if isinstance(nested, dict) else item
        purpose = authority.get("purpose") or item.get("authority_level")
        filename = authority.get("canonical_filename") or authority.get("filename")
        if purpose not in {"proof_authority", "replay_authority"} or not filename:
            continue
        result.append(
            {
                "logical_name": str(
                    authority.get("logical_name") or f"legacy-authority-{index + 1}"
                ),
                "canonical_filename": Path(str(filename)).name,
                "canonical_path": authority.get("canonical_path"),
                "expected_sha256": (
                    authority.get("expected_sha256")
                    or item.get("computed_sha256")
                    or item.get("hash")
                ),
                "authority_source": "legacy-checkpoint",
                "registry_record": copy.deepcopy(authority.get("registry_record") or {}),
                "purpose": purpose,
                "requesting_obligation_id": str(
                    authority.get("requesting_obligation_id")
                    or item.get("requesting_obligation_id")
                    or target_id
                ),
                "contextual_paths": copy.deepcopy(authority.get("contextual_paths") or []),
            }
        )
    return result


def _migrated_state(
    state: Mapping[str, Any],
    *,
    run_id: str,
    expected_target_id: str,
    expected_campaign_id: str | None,
    config_path: Path,
    inspection: CheckpointInspection,
    provenance_path: Path,
    target_policy_fingerprint: str,
) -> dict[str, Any]:
    execution_provider, evidence_provider = _provider_provenance(state)
    source_trust = state.get("trust_state") or state.get("evidence_state") or state.get("status")
    if inspection.classification == CheckpointClassification.REVALIDATION_REQUIRED.value:
        legacy_state = (
            "LEGACY_VERIFIED"
            if str(source_trust) in _CURRENT_TRUST_STATES or state.get("verified") is True
            else "LEGACY_EVIDENCE"
        )
    else:
        legacy_state = "LEGACY_EVIDENCE"
    now = utc_now()
    worker_count = _safe_int(state.get("worker_count"), 3, minimum=1)
    initial_worker_count = _safe_int(state.get("initial_worker_count"), worker_count, minimum=1)
    roots = state.get("canonical_source_roots")
    roots = [str(item) for item in roots] if isinstance(roots, list) else []
    authority = state.get("canonical_authority")
    authority = copy.deepcopy(authority) if isinstance(authority, list) else []
    migrated = {
        "schema_version": CURRENT_RUN_STATE_SCHEMA,
        "run_id": run_id,
        "target_id": expected_target_id,
        "phase": "CREATED",
        "status": "RUNNING",
        "dry_run": False,
        "config_path": str(config_path),
        "worker_count": worker_count,
        "created_at": now,
        "last_updated": now,
        "metrics": {},
        "failure_reasons": [],
        "campaign_id": expected_campaign_id,
        "legacy_campaign_id": copy.deepcopy(state.get("campaign_id")),
        "parent_run_id": state.get("parent_run_id"),
        "repair_cycle": _safe_int(state.get("repair_cycle"), 0),
        "hard_submit_gate": bool(state.get("hard_submit_gate", False)),
        "replay_policy_hash": state.get("replay_policy_hash"),
        "budget_limit_seconds": state.get("budget_limit_seconds"),
        "initial_worker_count": initial_worker_count,
        "role_scheduling": bool(state.get("role_scheduling", False)),
        "secondary_verification": bool(state.get("secondary_verification", False)),
        "routing_state_file": "routing_state.json",
        "pipeline_state_file": "pipeline_state.json",
        "checkpoint_migration_classification": inspection.classification,
        "checkpoint_migration_provenance_file": str(provenance_path),
        "target_policy_fingerprint": target_policy_fingerprint,
        "legacy_evidence_state": legacy_state,
        "legacy_source_trust_state": copy.deepcopy(source_trust),
        "execution_provider_provenance": execution_provider,
        "evidence_provider_provenance": evidence_provider,
        "canonical_source_requirements": _canonical_requirements(state, expected_target_id),
        "canonical_source_roots": roots,
        "canonical_authority": authority,
    }
    return migrated


class LegacyCheckpointMigrator:
    """Snapshot, classify, and create a separate current-schema run artifact."""

    def __init__(
        self,
        project: ProjectStore,
        *,
        config_path: str | Path,
        target_policy_fingerprint: str,
    ):
        self.project = project
        self.config_path = Path(config_path).resolve()
        self.target_policy_fingerprint = target_policy_fingerprint

    def prepare(
        self,
        source_run_dir: str | Path,
        *,
        expected_target_id: str,
        expected_campaign_id: str | None,
    ) -> CheckpointMigrationResult:
        source_dir = Path(source_run_dir).resolve()
        source_path = source_dir / "state.json"
        if not source_path.is_file():
            raise ProjectError(f"Legacy checkpoint state not found: {source_path}")
        source_raw = source_path.read_bytes()
        source_hash = _sha256(source_raw)
        digest = source_hash.removeprefix("sha256:")
        migration_context = _json_bytes(
            {
                "target_id": expected_target_id,
                "campaign_id": expected_campaign_id,
                "target_policy_fingerprint": self.target_policy_fingerprint,
            }
        )
        context_digest = hashlib.sha256(migration_context).hexdigest()
        record_dir = self.project.root / "migrations" / "checkpoints" / digest / context_digest
        source_snapshot = record_dir / "source_checkpoint.json"
        provenance_path = record_dir / "migration_provenance.json"
        _immutable_write(source_snapshot, source_raw)

        try:
            parsed = json.loads(source_raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
            inspection = CheckpointInspection(
                CheckpointClassification.ARCHIVE_ONLY.value,
                "checkpoint is not a readable JSON object",
                "INVALID_JSON",
            )
        else:
            if not isinstance(parsed, dict):
                parsed = {}
                inspection = CheckpointInspection(
                    CheckpointClassification.ARCHIVE_ONLY.value,
                    "checkpoint root is not an object",
                    "NON_OBJECT_JSON",
                )
            else:
                inspection = inspect_legacy_checkpoint(
                    parsed,
                    source_dir=source_dir,
                    expected_target_id=expected_target_id,
                    expected_campaign_id=expected_campaign_id,
                    target_policy_fingerprint=self.target_policy_fingerprint,
                )

        execution_provider, evidence_provider = _provider_provenance(parsed)
        resumable = inspection.classification in {
            CheckpointClassification.DIRECT_IMPORT.value,
            CheckpointClassification.REVALIDATION_REQUIRED.value,
        }
        run_id = f"{expected_target_id[:96]}-migrated-{digest[:12]}-{context_digest[:8]}"
        runtime_dir = self.project.root / "runs" / run_id if resumable else None
        migrated_snapshot = record_dir / "migrated_state.json" if resumable else None
        migration_timestamp = utc_now()
        if provenance_path.exists():
            existing_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            migration_timestamp = str(existing_provenance["migration_timestamp"])
        provenance = MigrationProvenance(
            source_artifact_path=str(source_path),
            source_hash=source_hash,
            source_schema=inspection.source_schema,
            target_schema=CURRENT_RUN_STATE_SCHEMA,
            source_policy_fingerprint=(
                str(parsed.get("policy_fingerprint") or parsed.get("replay_policy_hash"))
                if parsed.get("policy_fingerprint") or parsed.get("replay_policy_hash")
                else None
            ),
            target_policy_fingerprint=self.target_policy_fingerprint,
            classification=inspection.classification,
            reason=inspection.reason,
            migration_timestamp=migration_timestamp,
            migration_implementation_version=MIGRATION_IMPLEMENTATION_VERSION,
            source_snapshot_path=str(source_snapshot),
            migrated_checkpoint_path=str(migrated_snapshot) if migrated_snapshot else None,
            execution_provider_provenance=execution_provider,
            evidence_provider_provenance=evidence_provider,
            compatibility_checks=inspection.compatibility_checks,
        )
        _immutable_write(provenance_path, _json_bytes(provenance.to_dict()))

        if not resumable:
            return CheckpointMigrationResult(
                inspection.classification,
                inspection.reason,
                provenance_path,
                source_snapshot,
            )

        migrated = _migrated_state(
            parsed,
            run_id=run_id,
            expected_target_id=expected_target_id,
            expected_campaign_id=expected_campaign_id,
            config_path=self.config_path,
            inspection=inspection,
            provenance_path=provenance_path,
            target_policy_fingerprint=self.target_policy_fingerprint,
        )
        assert migrated_snapshot is not None and runtime_dir is not None
        _immutable_write(migrated_snapshot, _json_bytes(migrated))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        runtime_state = runtime_dir / "state.json"
        if not runtime_state.exists():
            runtime_state.write_bytes(migrated_snapshot.read_bytes())

        self._materialize_canonical_bodies(parsed, runtime_dir)
        return CheckpointMigrationResult(
            inspection.classification,
            inspection.reason,
            provenance_path,
            source_snapshot,
            migrated_snapshot,
            runtime_dir,
        )

    @staticmethod
    def _materialize_canonical_bodies(state: Mapping[str, Any], runtime_dir: Path) -> None:
        resolutions = state.get("canonical_authority")
        if not isinstance(resolutions, list):
            return
        for item in resolutions:
            if not isinstance(item, dict) or not isinstance(item.get("body"), str):
                continue
            digest = _normalized_sha256(item.get("computed_sha256"))
            raw = item["body"].encode("utf-8")
            if not digest or _sha256(raw) != digest:
                continue
            object_path = runtime_dir / "canonical_authority" / "objects" / digest[7:]
            _immutable_write(object_path, raw)
