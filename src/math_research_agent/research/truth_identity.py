"""Typed, domain-separated identities for the PHASE 3 Truth Plane.

The canonicalization here is deliberately conservative.  It normalizes text
encoding and layout, but never guesses mathematical equivalence.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .project import ProjectError


SCHEMA_VERSION = 1


class AssertionKind(str, Enum):
    PROJECT_THEOREM = "PROJECT_THEOREM"
    PREMISE = "PREMISE"
    LOCAL_LEMMA = "LOCAL_LEMMA"
    EXTERNAL_THEOREM = "EXTERNAL_THEOREM"
    COMPUTATIONAL_CLAIM = "COMPUTATIONAL_CLAIM"


class AuthorityKind(str, Enum):
    PROJECT_THEOREM = "PROJECT_THEOREM"
    PREMISE = "PREMISE"
    FOUNDATION_REGISTRY = "FOUNDATION_REGISTRY"
    SEMANTIC_REGISTRY = "SEMANTIC_REGISTRY"
    EXTERNAL_THEOREM = "EXTERNAL_THEOREM"
    LOCAL_VERIFIED_EVIDENCE = "LOCAL_VERIFIED_EVIDENCE"
    CANONICAL_SOURCE = "CANONICAL_SOURCE"


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a typed value deterministically for a declared hash domain."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def domain_hash(domain: str, value: Any, *, schema_version: int = SCHEMA_VERSION) -> str:
    """Hash one typed domain; callers cannot obtain an unlabelled truth hash."""

    if not domain.strip():
        raise ProjectError("hash domain is required")
    envelope = {
        "hash_domain": domain,
        "schema_version": schema_version,
        "value": value,
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()


def source_artifact_sha256(source: bytes | bytearray | str | Path) -> str:
    """Return a byte hash in the explicitly named source-artifact domain."""

    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = bytes(source)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonicalize_assertion_text(statement: str) -> str:
    """Apply only NFC, newline, trailing-space, and outer-space normalization."""

    if not isinstance(statement, str) or not statement.strip():
        raise ProjectError("assertion statement is required")
    normalized = unicodedata.normalize("NFC", statement).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    canonical = "\n".join(lines)
    if not canonical:
        raise ProjectError("assertion statement is empty after canonicalization")
    return canonical


def _strict_keys(value: Mapping[str, Any], expected: set[str], object_type: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ProjectError(
            f"{object_type} fields do not match schema 1; missing={missing}, unknown={unknown}"
        )


@dataclass(frozen=True, slots=True)
class AssertionIdentity:
    schema_version: int
    object_type: str
    assertion_kind: str
    stable_id: str
    canonical_statement: str
    claim_type: str
    notation_scope: str
    assertion_identity_hash: str

    @classmethod
    def capture(
        cls,
        *,
        assertion_kind: str,
        stable_id: str,
        statement: str,
        claim_type: str,
        notation_scope: str = "",
    ) -> "AssertionIdentity":
        if assertion_kind not in {item.value for item in AssertionKind}:
            raise ProjectError(f"Unsupported assertion kind: {assertion_kind}")
        if not stable_id.strip():
            raise ProjectError("AssertionIdentity.stable_id is required")
        canonical_statement = canonicalize_assertion_text(statement)
        normalized_claim_type = unicodedata.normalize("NFC", str(claim_type).strip())
        normalized_scope = canonicalize_optional_text(notation_scope)
        identity = {
            "canonical_statement": canonical_statement,
            "claim_type": normalized_claim_type,
            "notation_scope": normalized_scope,
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            object_type="ASSERTION_IDENTITY",
            assertion_kind=assertion_kind,
            stable_id=stable_id,
            canonical_statement=canonical_statement,
            claim_type=normalized_claim_type,
            notation_scope=normalized_scope,
            assertion_identity_hash=domain_hash("assertion_identity", identity),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AssertionIdentity":
        expected = {
            "schema_version",
            "object_type",
            "assertion_kind",
            "stable_id",
            "canonical_statement",
            "claim_type",
            "notation_scope",
            "assertion_identity_hash",
        }
        _strict_keys(value, expected, "AssertionIdentity")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError("AssertionIdentity migration is required")
        if value.get("object_type") != "ASSERTION_IDENTITY":
            raise ProjectError("Invalid AssertionIdentity.object_type")
        captured = cls.capture(
            assertion_kind=str(value["assertion_kind"]),
            stable_id=str(value["stable_id"]),
            statement=str(value["canonical_statement"]),
            claim_type=str(value["claim_type"]),
            notation_scope=str(value["notation_scope"]),
        )
        if captured.assertion_identity_hash != value.get("assertion_identity_hash"):
            raise ProjectError("AssertionIdentity hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonicalize_optional_text(value: str | None) -> str:
    if value is None or not str(value).strip():
        return ""
    normalized = unicodedata.normalize("NFC", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


@dataclass(frozen=True, slots=True)
class AuthorityBinding:
    schema_version: int
    object_type: str
    authority_kind: str
    authority_id: str
    assertion_identity_hash: str
    authority_content_hash: str
    authority_status: str
    provenance: dict[str, Any]
    binding_hash: str

    @classmethod
    def capture(
        cls,
        *,
        authority_kind: str,
        authority_id: str,
        assertion_identity_hash: str,
        authority_content_hash: str,
        authority_status: str,
        provenance: Mapping[str, Any] | None = None,
    ) -> "AuthorityBinding":
        if authority_kind not in {item.value for item in AuthorityKind}:
            raise ProjectError(f"Unsupported authority kind: {authority_kind}")
        if not authority_id.strip():
            raise ProjectError("AuthorityBinding.authority_id is required")
        for name, digest in (
            ("assertion_identity_hash", assertion_identity_hash),
            ("authority_content_hash", authority_content_hash),
        ):
            _validate_sha256(digest, name)
        stable_provenance = json.loads(canonical_json_bytes(dict(provenance or {})))
        content = {
            "authority_kind": authority_kind,
            "authority_id": authority_id,
            "assertion_identity_hash": assertion_identity_hash,
            "authority_content_hash": authority_content_hash,
            "authority_status": str(authority_status),
            "provenance": stable_provenance,
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            object_type="AUTHORITY_BINDING",
            binding_hash=domain_hash("authority_binding", content),
            provenance=stable_provenance,
            **{key: content[key] for key in content if key != "provenance"},
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuthorityBinding":
        expected = {
            "schema_version",
            "object_type",
            "authority_kind",
            "authority_id",
            "assertion_identity_hash",
            "authority_content_hash",
            "authority_status",
            "provenance",
            "binding_hash",
        }
        _strict_keys(value, expected, "AuthorityBinding")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError("AuthorityBinding migration is required")
        if value.get("object_type") != "AUTHORITY_BINDING":
            raise ProjectError("Invalid AuthorityBinding.object_type")
        provenance = value.get("provenance")
        if not isinstance(provenance, dict):
            raise ProjectError("AuthorityBinding.provenance must be an object")
        captured = cls.capture(
            authority_kind=str(value["authority_kind"]),
            authority_id=str(value["authority_id"]),
            assertion_identity_hash=str(value["assertion_identity_hash"]),
            authority_content_hash=str(value["authority_content_hash"]),
            authority_status=str(value["authority_status"]),
            provenance=provenance,
        )
        if captured.binding_hash != value.get("binding_hash"):
            raise ProjectError("AuthorityBinding hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DependencyEntry:
    dependency_id: str
    kind: str
    assertion_identity_hash: str
    authority_binding_hash: str
    captured_status: str

    def __post_init__(self) -> None:
        if self.kind not in {"THEOREM", "PREMISE"}:
            raise ProjectError(f"Unsupported dependency kind: {self.kind}")
        _validate_sha256(self.assertion_identity_hash, "assertion_identity_hash")
        _validate_sha256(self.authority_binding_hash, "authority_binding_hash")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DependencyEntry":
        expected = {
            "dependency_id",
            "kind",
            "assertion_identity_hash",
            "authority_binding_hash",
            "captured_status",
        }
        _strict_keys(value, expected, "DependencyEntry")
        return cls(**{key: str(value[key]) for key in expected})

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DependencySnapshot:
    schema_version: int
    object_type: str
    target_assertion_hash: str
    dependencies: tuple[DependencyEntry, ...]
    dependency_snapshot_hash: str

    @classmethod
    def capture(
        cls,
        *,
        target_assertion_hash: str,
        dependencies: Iterable[DependencyEntry],
    ) -> "DependencySnapshot":
        _validate_sha256(target_assertion_hash, "target_assertion_hash")
        ordered = tuple(sorted(dependencies, key=lambda item: (item.dependency_id, item.kind)))
        keys = [(item.dependency_id, item.kind) for item in ordered]
        if len(keys) != len(set(keys)):
            raise ProjectError("DependencySnapshot contains duplicate dependency identities")
        content = {
            "target_assertion_hash": target_assertion_hash,
            "dependencies": [item.to_dict() for item in ordered],
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            object_type="DEPENDENCY_SNAPSHOT",
            target_assertion_hash=target_assertion_hash,
            dependencies=ordered,
            dependency_snapshot_hash=domain_hash("dependency_snapshot", content),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DependencySnapshot":
        expected = {
            "schema_version",
            "object_type",
            "target_assertion_hash",
            "dependencies",
            "dependency_snapshot_hash",
        }
        _strict_keys(value, expected, "DependencySnapshot")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError("DependencySnapshot migration is required")
        if value.get("object_type") != "DEPENDENCY_SNAPSHOT":
            raise ProjectError("Invalid DependencySnapshot.object_type")
        if not isinstance(value.get("dependencies"), list):
            raise ProjectError("DependencySnapshot.dependencies must be a list")
        captured = cls.capture(
            target_assertion_hash=str(value["target_assertion_hash"]),
            dependencies=[DependencyEntry.from_dict(item) for item in value["dependencies"]],
        )
        if captured.dependency_snapshot_hash != value.get("dependency_snapshot_hash"):
            raise ProjectError("DependencySnapshot hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["dependencies"] = [item.to_dict() for item in self.dependencies]
        return value


@dataclass(frozen=True, slots=True)
class AssumptionSnapshot:
    schema_version: int
    object_type: str
    target_id: str
    assumptions: tuple[dict[str, Any], ...]
    semantic_scope: dict[str, Any]
    assumption_snapshot_hash: str

    @classmethod
    def capture(
        cls,
        *,
        target_id: str,
        assumptions: Iterable[Mapping[str, Any]],
        semantic_scope: Mapping[str, Any],
    ) -> "AssumptionSnapshot":
        stable_assumptions = tuple(
            sorted(
                (json.loads(canonical_json_bytes(dict(item))) for item in assumptions),
                key=lambda item: canonical_json_bytes(item),
            )
        )
        stable_scope = json.loads(canonical_json_bytes(dict(semantic_scope)))
        content = {
            "target_id": target_id,
            "assumptions": list(stable_assumptions),
            "semantic_scope": stable_scope,
        }
        return cls(
            schema_version=SCHEMA_VERSION,
            object_type="ASSUMPTION_SNAPSHOT",
            target_id=target_id,
            assumptions=stable_assumptions,
            semantic_scope=stable_scope,
            assumption_snapshot_hash=domain_hash("assumption_snapshot", content),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AssumptionSnapshot":
        expected = {
            "schema_version",
            "object_type",
            "target_id",
            "assumptions",
            "semantic_scope",
            "assumption_snapshot_hash",
        }
        _strict_keys(value, expected, "AssumptionSnapshot")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError("AssumptionSnapshot migration is required")
        if value.get("object_type") != "ASSUMPTION_SNAPSHOT":
            raise ProjectError("Invalid AssumptionSnapshot.object_type")
        if not isinstance(value.get("assumptions"), list) or not isinstance(
            value.get("semantic_scope"), dict
        ):
            raise ProjectError("Malformed AssumptionSnapshot")
        captured = cls.capture(
            target_id=str(value["target_id"]),
            assumptions=value["assumptions"],
            semantic_scope=value["semantic_scope"],
        )
        if captured.assumption_snapshot_hash != value.get("assumption_snapshot_hash"):
            raise ProjectError("AssumptionSnapshot hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["assumptions"] = list(self.assumptions)
        return value


def aggregate_authority_binding_hash(bindings: Iterable[AuthorityBinding]) -> str:
    ordered = sorted((binding.binding_hash for binding in bindings))
    if len(ordered) != len(set(ordered)):
        raise ProjectError("Duplicate authority binding hash")
    return domain_hash("authority_binding_set", ordered)


def semantic_input_hash(
    *,
    assertion_identity_hash: str,
    dependency_snapshot_hash: str,
    assumption_snapshot_hash: str,
    authority_binding_hash: str,
    trust_policy_fingerprint: str,
) -> str:
    content = {
        "assertion_identity_hash": assertion_identity_hash,
        "dependency_snapshot_hash": dependency_snapshot_hash,
        "assumption_snapshot_hash": assumption_snapshot_hash,
        "authority_binding_hash": authority_binding_hash,
        "trust_policy_fingerprint": trust_policy_fingerprint,
    }
    return domain_hash("semantic_input", content)


def prompt_projection_hash(value: str | Mapping[str, Any]) -> str:
    projection = value if isinstance(value, str) else json.loads(canonical_json_bytes(dict(value)))
    return domain_hash("prompt_projection", projection)


def trust_policy_fingerprint(value: Mapping[str, Any]) -> str:
    return domain_hash("trust_policy", json.loads(canonical_json_bytes(dict(value))))


def project_record_hash(value: Mapping[str, Any]) -> str:
    return domain_hash("project_record", json.loads(canonical_json_bytes(dict(value))))


def _validate_sha256(value: str, name: str) -> None:
    raw = str(value)
    if raw.startswith("sha256:"):
        raw = raw[7:]
    if len(raw) != 64 or any(char not in "0123456789abcdefABCDEF" for char in raw):
        raise ProjectError(f"{name} must be a SHA-256 digest")
