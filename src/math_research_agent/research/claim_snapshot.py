"""Immutable root-claim snapshots and typed stale comparison semantics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

from .project import ProjectError
from .truth_identity import (
    SCHEMA_VERSION,
    AssertionIdentity,
    AssumptionSnapshot,
    AuthorityBinding,
    DependencySnapshot,
    _strict_keys,
    aggregate_authority_binding_hash,
    domain_hash,
    semantic_input_hash,
)


class SnapshotComparisonStatus(str, Enum):
    MATCH = "MATCH"
    ASSERTION_CHANGED = "ASSERTION_CHANGED"
    DEPENDENCY_CHANGED = "DEPENDENCY_CHANGED"
    ASSUMPTION_CHANGED = "ASSUMPTION_CHANGED"
    AUTHORITY_CHANGED = "AUTHORITY_CHANGED"
    TRUST_POLICY_CHANGED = "TRUST_POLICY_CHANGED"
    SEMANTIC_INPUT_CHANGED = "SEMANTIC_INPUT_CHANGED"
    TARGET_STATUS_CHANGED = "TARGET_STATUS_CHANGED"
    UNRESOLVABLE_AUTHORITY = "UNRESOLVABLE_AUTHORITY"
    UNKNOWN_COMPATIBILITY = "UNKNOWN_COMPATIBILITY"


class SnapshotDisposition(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    HARD_STALE = "HARD_STALE"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class SnapshotComparison:
    schema_version: int
    object_type: str
    status: str
    disposition: str
    reason: str
    stored_claim_snapshot_hash: str
    current_claim_snapshot_hash: str | None

    @property
    def compatible(self) -> bool:
        return self.status == SnapshotComparisonStatus.MATCH.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ClaimSnapshot:
    schema_version: int
    object_type: str
    theorem_id: str
    assertion_identity: AssertionIdentity
    assertion_identity_hash: str
    dependency_snapshot: DependencySnapshot
    dependency_snapshot_hash: str
    assumption_snapshot: AssumptionSnapshot
    assumption_snapshot_hash: str
    authority_bindings: tuple[AuthorityBinding, ...]
    authority_binding_hash: str
    trust_policy_fingerprint: str
    semantic_input_hash: str
    captured_status: str
    captured_at: str
    project_record_hash: str
    claim_snapshot_hash: str

    @classmethod
    def capture(
        cls,
        *,
        theorem_id: str,
        assertion_identity: AssertionIdentity,
        dependency_snapshot: DependencySnapshot,
        assumption_snapshot: AssumptionSnapshot,
        authority_bindings: tuple[AuthorityBinding, ...],
        trust_policy_fingerprint: str,
        captured_status: str,
        captured_at: str,
        project_record_hash: str,
    ) -> "ClaimSnapshot":
        if assertion_identity.assertion_identity_hash != dependency_snapshot.target_assertion_hash:
            raise ProjectError("DependencySnapshot targets a different assertion")
        ordered_bindings = tuple(sorted(authority_bindings, key=lambda item: item.binding_hash))
        authority_hash = aggregate_authority_binding_hash(ordered_bindings)
        semantic_hash = semantic_input_hash(
            assertion_identity_hash=assertion_identity.assertion_identity_hash,
            dependency_snapshot_hash=dependency_snapshot.dependency_snapshot_hash,
            assumption_snapshot_hash=assumption_snapshot.assumption_snapshot_hash,
            authority_binding_hash=authority_hash,
            trust_policy_fingerprint=trust_policy_fingerprint,
        )
        identity = {
            "theorem_id": theorem_id,
            "assertion_identity_hash": assertion_identity.assertion_identity_hash,
            "dependency_snapshot_hash": dependency_snapshot.dependency_snapshot_hash,
            "assumption_snapshot_hash": assumption_snapshot.assumption_snapshot_hash,
            "authority_binding_hash": authority_hash,
            "trust_policy_fingerprint": trust_policy_fingerprint,
            "semantic_input_hash": semantic_hash,
            "captured_status": captured_status,
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            object_type="CLAIM_SNAPSHOT",
            theorem_id=theorem_id,
            assertion_identity=assertion_identity,
            assertion_identity_hash=assertion_identity.assertion_identity_hash,
            dependency_snapshot=dependency_snapshot,
            dependency_snapshot_hash=dependency_snapshot.dependency_snapshot_hash,
            assumption_snapshot=assumption_snapshot,
            assumption_snapshot_hash=assumption_snapshot.assumption_snapshot_hash,
            authority_bindings=ordered_bindings,
            authority_binding_hash=authority_hash,
            trust_policy_fingerprint=trust_policy_fingerprint,
            semantic_input_hash=semantic_hash,
            captured_status=captured_status,
            captured_at=captured_at,
            project_record_hash=project_record_hash,
            claim_snapshot_hash=domain_hash("claim_snapshot", identity),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ClaimSnapshot":
        expected = {
            "schema_version",
            "object_type",
            "theorem_id",
            "assertion_identity",
            "assertion_identity_hash",
            "dependency_snapshot",
            "dependency_snapshot_hash",
            "assumption_snapshot",
            "assumption_snapshot_hash",
            "authority_bindings",
            "authority_binding_hash",
            "trust_policy_fingerprint",
            "semantic_input_hash",
            "captured_status",
            "captured_at",
            "project_record_hash",
            "claim_snapshot_hash",
        }
        _strict_keys(value, expected, "ClaimSnapshot")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError("ClaimSnapshot migration is required")
        if value.get("object_type") != "CLAIM_SNAPSHOT":
            raise ProjectError("Invalid ClaimSnapshot.object_type")
        if not isinstance(value.get("authority_bindings"), list):
            raise ProjectError("ClaimSnapshot.authority_bindings must be a list")
        assertion = AssertionIdentity.from_dict(value["assertion_identity"])
        dependencies = DependencySnapshot.from_dict(value["dependency_snapshot"])
        assumptions = AssumptionSnapshot.from_dict(value["assumption_snapshot"])
        captured = cls.capture(
            theorem_id=str(value["theorem_id"]),
            assertion_identity=assertion,
            dependency_snapshot=dependencies,
            assumption_snapshot=assumptions,
            authority_bindings=tuple(
                AuthorityBinding.from_dict(item) for item in value["authority_bindings"]
            ),
            trust_policy_fingerprint=str(value["trust_policy_fingerprint"]),
            captured_status=str(value["captured_status"]),
            captured_at=str(value["captured_at"]),
            project_record_hash=str(value["project_record_hash"]),
        )
        redundant = {
            "assertion_identity_hash": captured.assertion_identity_hash,
            "dependency_snapshot_hash": captured.dependency_snapshot_hash,
            "assumption_snapshot_hash": captured.assumption_snapshot_hash,
            "authority_binding_hash": captured.authority_binding_hash,
            "semantic_input_hash": captured.semantic_input_hash,
            "claim_snapshot_hash": captured.claim_snapshot_hash,
        }
        for key, actual in redundant.items():
            if value.get(key) != actual:
                raise ProjectError(f"ClaimSnapshot {key} mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["assertion_identity"] = self.assertion_identity.to_dict()
        value["dependency_snapshot"] = self.dependency_snapshot.to_dict()
        value["assumption_snapshot"] = self.assumption_snapshot.to_dict()
        value["authority_bindings"] = [item.to_dict() for item in self.authority_bindings]
        return value


def compare_claim_snapshots(
    stored: ClaimSnapshot, current: ClaimSnapshot | None
) -> SnapshotComparison:
    """Compare exact truth domains; unknown never becomes an implicit match."""

    if current is None:
        return _comparison(
            stored,
            None,
            SnapshotComparisonStatus.UNKNOWN_COMPATIBILITY,
            SnapshotDisposition.REVALIDATION_REQUIRED,
            "current truth could not be reconstructed",
        )
    if stored.schema_version != current.schema_version:
        return _comparison(
            stored,
            current,
            SnapshotComparisonStatus.UNKNOWN_COMPATIBILITY,
            SnapshotDisposition.REVALIDATION_REQUIRED,
            "claim snapshot schemas are not explicitly compatible",
        )
    if stored.assertion_identity_hash != current.assertion_identity_hash:
        return _comparison(
            stored,
            current,
            SnapshotComparisonStatus.ASSERTION_CHANGED,
            SnapshotDisposition.HARD_STALE,
            "root assertion identity changed",
        )
    if stored.dependency_snapshot_hash != current.dependency_snapshot_hash:
        return _comparison(
            stored,
            current,
            SnapshotComparisonStatus.DEPENDENCY_CHANGED,
            SnapshotDisposition.REVALIDATION_REQUIRED,
            "dependency truth snapshot changed",
        )
    if stored.assumption_snapshot_hash != current.assumption_snapshot_hash:
        return _comparison(
            stored,
            current,
            SnapshotComparisonStatus.ASSUMPTION_CHANGED,
            SnapshotDisposition.REVALIDATION_REQUIRED,
            "assumption or semantic scope changed",
        )
    if stored.authority_binding_hash != current.authority_binding_hash:
        unresolved = any(
            binding.authority_kind == "CANONICAL_SOURCE"
            and binding.authority_status != "RESOLVED_CANONICAL"
            for binding in current.authority_bindings
        )
        return _comparison(
            stored,
            current,
            (
                SnapshotComparisonStatus.UNRESOLVABLE_AUTHORITY
                if unresolved
                else SnapshotComparisonStatus.AUTHORITY_CHANGED
            ),
            (
                SnapshotDisposition.BLOCKED
                if unresolved
                else SnapshotDisposition.REVALIDATION_REQUIRED
            ),
            "required authority is unresolved" if unresolved else "authority binding changed",
        )
    if stored.trust_policy_fingerprint != current.trust_policy_fingerprint:
        return _comparison(
            stored,
            current,
            SnapshotComparisonStatus.TRUST_POLICY_CHANGED,
            SnapshotDisposition.REVALIDATION_REQUIRED,
            "active trust policy fingerprint changed",
        )
    if stored.semantic_input_hash != current.semantic_input_hash:
        return _comparison(
            stored,
            current,
            SnapshotComparisonStatus.SEMANTIC_INPUT_CHANGED,
            SnapshotDisposition.REVALIDATION_REQUIRED,
            "semantic input changed in an unknown subdomain",
        )
    if stored.captured_status != current.captured_status:
        return _comparison(
            stored,
            current,
            SnapshotComparisonStatus.TARGET_STATUS_CHANGED,
            SnapshotDisposition.REVALIDATION_REQUIRED,
            "target theorem status changed",
        )
    return _comparison(
        stored,
        current,
        SnapshotComparisonStatus.MATCH,
        SnapshotDisposition.COMPATIBLE,
        "all truth identity domains match",
    )


def _comparison(
    stored: ClaimSnapshot,
    current: ClaimSnapshot | None,
    status: SnapshotComparisonStatus,
    disposition: SnapshotDisposition,
    reason: str,
) -> SnapshotComparison:
    return SnapshotComparison(
        schema_version=SCHEMA_VERSION,
        object_type="CLAIM_SNAPSHOT_COMPARISON",
        status=status.value,
        disposition=disposition.value,
        reason=reason,
        stored_claim_snapshot_hash=stored.claim_snapshot_hash,
        current_claim_snapshot_hash=(current.claim_snapshot_hash if current else None),
    )


def validate_claim_snapshot_for_root_synthesis(comparison: SnapshotComparison) -> None:
    """Future seam only; PHASE 3 does not implement root synthesis."""

    if not comparison.compatible:
        raise ProjectError(f"ROOT_SYNTHESIS claim snapshot is not current: {comparison.status}")
