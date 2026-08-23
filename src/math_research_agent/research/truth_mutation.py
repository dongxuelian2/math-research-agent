"""Immutable mutation intent/receipt records for Truth Plane status changes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .project import ProjectError
from .truth_identity import canonical_json_bytes, domain_hash, source_artifact_sha256


SCHEMA_VERSION = 1


def _strict_keys(value: Mapping[str, Any], expected: set[str], object_type: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ProjectError(
            f"{object_type} fields do not match schema 1; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )


def _require_sha256(value: str, name: str) -> None:
    digest = value.removeprefix("sha256:").casefold()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ProjectError(f"{name} must be a SHA-256 digest")


def capture_artifact_refs(
    paths: Iterable[str | Path], *, project_root: Path
) -> tuple[dict[str, str], ...]:
    """Hash audit artifacts as bytes and keep paths as non-authoritative locators."""

    refs = []
    root = project_root.resolve()
    for path_value in paths:
        path = Path(path_value).resolve()
        if not path.is_file():
            raise ProjectError(f"Mutation audit artifact is missing: {path}")
        try:
            locator = path.relative_to(root).as_posix()
        except ValueError:
            locator = str(path)
        refs.append({"path": locator, "source_artifact_sha256": source_artifact_sha256(path)})
    return tuple(sorted(refs, key=lambda item: canonical_json_bytes(item)))


@dataclass(frozen=True, slots=True)
class TruthMutationIntent:
    schema_version: int
    object_type: str
    mutation_id: str
    mutation_kind: str
    theorem_id: str
    from_status: str
    requested_to_status: str
    claim_snapshot_hash: str
    assertion_identity_hash: str
    audited_claim_snapshot_hash: str
    trust_policy_fingerprint: str
    audit_artifacts: tuple[dict[str, str], ...]
    requested_by: str
    reason: str
    created_at: str

    @classmethod
    def capture(
        cls,
        *,
        theorem_id: str,
        from_status: str,
        requested_to_status: str,
        claim_snapshot_hash: str,
        assertion_identity_hash: str,
        audited_claim_snapshot_hash: str,
        trust_policy_fingerprint: str,
        audit_artifacts: Iterable[Mapping[str, str]],
        requested_by: str,
        reason: str,
        created_at: str,
    ) -> "TruthMutationIntent":
        for name, digest in (
            ("claim_snapshot_hash", claim_snapshot_hash),
            ("assertion_identity_hash", assertion_identity_hash),
            ("audited_claim_snapshot_hash", audited_claim_snapshot_hash),
            ("trust_policy_fingerprint", trust_policy_fingerprint),
        ):
            _require_sha256(digest, name)
        if audited_claim_snapshot_hash != claim_snapshot_hash:
            raise ProjectError("Audit gate is not bound to the mutation ClaimSnapshot")
        artifacts = tuple(
            sorted(
                ({str(key): str(value) for key, value in item.items()} for item in audit_artifacts),
                key=lambda item: canonical_json_bytes(item),
            )
        )
        for item in artifacts:
            _strict_keys(item, {"path", "source_artifact_sha256"}, "AuditArtifactRef")
            _require_sha256(item["source_artifact_sha256"], "source_artifact_sha256")
        identity = {
            "mutation_kind": "STATUS_TRANSITION",
            "theorem_id": theorem_id,
            "from_status": from_status,
            "requested_to_status": requested_to_status,
            "claim_snapshot_hash": claim_snapshot_hash,
            "assertion_identity_hash": assertion_identity_hash,
            "audited_claim_snapshot_hash": audited_claim_snapshot_hash,
            "trust_policy_fingerprint": trust_policy_fingerprint,
            "audit_artifacts": list(artifacts),
            "requested_by": requested_by,
            "reason": reason,
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            object_type="TRUTH_MUTATION_INTENT",
            mutation_id=domain_hash("truth_mutation_intent", identity),
            mutation_kind="STATUS_TRANSITION",
            theorem_id=theorem_id,
            from_status=from_status,
            requested_to_status=requested_to_status,
            claim_snapshot_hash=claim_snapshot_hash,
            assertion_identity_hash=assertion_identity_hash,
            audited_claim_snapshot_hash=audited_claim_snapshot_hash,
            trust_policy_fingerprint=trust_policy_fingerprint,
            audit_artifacts=artifacts,
            requested_by=requested_by,
            reason=reason,
            created_at=created_at,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TruthMutationIntent":
        expected = {
            "schema_version",
            "object_type",
            "mutation_id",
            "mutation_kind",
            "theorem_id",
            "from_status",
            "requested_to_status",
            "claim_snapshot_hash",
            "assertion_identity_hash",
            "audited_claim_snapshot_hash",
            "trust_policy_fingerprint",
            "audit_artifacts",
            "requested_by",
            "reason",
            "created_at",
        }
        _strict_keys(value, expected, "TruthMutationIntent")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError("TruthMutationIntent migration is required")
        if value.get("object_type") != "TRUTH_MUTATION_INTENT":
            raise ProjectError("Invalid TruthMutationIntent.object_type")
        if not isinstance(value.get("audit_artifacts"), list):
            raise ProjectError("TruthMutationIntent.audit_artifacts must be a list")
        captured = cls.capture(
            theorem_id=str(value["theorem_id"]),
            from_status=str(value["from_status"]),
            requested_to_status=str(value["requested_to_status"]),
            claim_snapshot_hash=str(value["claim_snapshot_hash"]),
            assertion_identity_hash=str(value["assertion_identity_hash"]),
            audited_claim_snapshot_hash=str(value["audited_claim_snapshot_hash"]),
            trust_policy_fingerprint=str(value["trust_policy_fingerprint"]),
            audit_artifacts=value["audit_artifacts"],
            requested_by=str(value["requested_by"]),
            reason=str(value["reason"]),
            created_at=str(value["created_at"]),
        )
        if captured.mutation_id != value.get("mutation_id"):
            raise ProjectError("TruthMutationIntent hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["audit_artifacts"] = list(self.audit_artifacts)
        return value


@dataclass(frozen=True, slots=True)
class TruthMutationReceipt:
    schema_version: int
    object_type: str
    receipt_hash: str
    mutation_id: str
    theorem_id: str
    previous_status: str
    resulting_status: str
    claim_snapshot_hash: str
    resulting_claim_snapshot_hash: str
    project_record_hash_before: str
    project_record_hash_after: str
    actor: str
    applied_at: str

    @classmethod
    def capture(
        cls,
        *,
        mutation_id: str,
        theorem_id: str,
        previous_status: str,
        resulting_status: str,
        claim_snapshot_hash: str,
        resulting_claim_snapshot_hash: str,
        project_record_hash_before: str,
        project_record_hash_after: str,
        actor: str,
        applied_at: str,
    ) -> "TruthMutationReceipt":
        for name, digest in (
            ("mutation_id", mutation_id),
            ("claim_snapshot_hash", claim_snapshot_hash),
            ("resulting_claim_snapshot_hash", resulting_claim_snapshot_hash),
            ("project_record_hash_before", project_record_hash_before),
            ("project_record_hash_after", project_record_hash_after),
        ):
            _require_sha256(digest, name)
        content = {
            "mutation_id": mutation_id,
            "theorem_id": theorem_id,
            "previous_status": previous_status,
            "resulting_status": resulting_status,
            "claim_snapshot_hash": claim_snapshot_hash,
            "resulting_claim_snapshot_hash": resulting_claim_snapshot_hash,
            "project_record_hash_before": project_record_hash_before,
            "project_record_hash_after": project_record_hash_after,
            "actor": actor,
            "applied_at": applied_at,
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            object_type="TRUTH_MUTATION_RECEIPT",
            receipt_hash=domain_hash("truth_mutation_receipt", content),
            **content,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TruthMutationReceipt":
        expected = {
            "schema_version",
            "object_type",
            "receipt_hash",
            "mutation_id",
            "theorem_id",
            "previous_status",
            "resulting_status",
            "claim_snapshot_hash",
            "resulting_claim_snapshot_hash",
            "project_record_hash_before",
            "project_record_hash_after",
            "actor",
            "applied_at",
        }
        _strict_keys(value, expected, "TruthMutationReceipt")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError("TruthMutationReceipt migration is required")
        if value.get("object_type") != "TRUTH_MUTATION_RECEIPT":
            raise ProjectError("Invalid TruthMutationReceipt.object_type")
        captured = cls.capture(
            **{
                key: str(value[key])
                for key in expected
                if key not in {"schema_version", "object_type", "receipt_hash"}
            }
        )
        if captured.receipt_hash != value.get("receipt_hash"):
            raise ProjectError("TruthMutationReceipt hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
