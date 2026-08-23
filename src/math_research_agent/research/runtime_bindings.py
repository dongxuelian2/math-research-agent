"""Immutable bindings between runtime work and semantic cross-plane state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .project import ProjectError
from .runtime_model import content_hash


def _text(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"{name} must be null or a non-empty string")
    return value.strip()


def _hash(value: str | None, name: str) -> str | None:
    value = _text(value, name)
    if value is None:
        return None
    digest = value.removeprefix("sha256:").casefold()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ProjectError(f"{name} must be a SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class CrossPlaneExecutionBinding:
    """The immutable semantic context attached to one runtime execution."""

    root_claim_snapshot_hash: str
    research_map_id: str | None = None
    research_map_version: int | None = None
    research_map_hash: str | None = None
    research_obligation_id: str | None = None
    directive_id: str | None = None
    tactical_session_id: str | None = None
    governance_object_type: str | None = None
    governance_object_id: str | None = None
    governance_source_hash: str | None = None

    @classmethod
    def capture(
        cls,
        *,
        root_claim_snapshot_hash: str,
        research_map_id: str | None = None,
        research_map_version: int | None = None,
        research_map_hash: str | None = None,
        research_obligation_id: str | None = None,
        directive_id: str | None = None,
        tactical_session_id: str | None = None,
        governance_object_type: str | None = None,
        governance_object_id: str | None = None,
        governance_source_hash: str | None = None,
    ) -> "CrossPlaneExecutionBinding":
        map_values = (research_map_id, research_map_version, research_map_hash)
        if any(value is not None for value in map_values) and not all(
            value is not None for value in map_values
        ):
            raise ProjectError(
                "CrossPlaneExecutionBinding requires map id, version, and hash together"
            )
        if research_map_version is not None and (
            isinstance(research_map_version, bool) or research_map_version < 1
        ):
            raise ProjectError("CrossPlaneExecutionBinding.research_map_version must be positive")
        governance_values = (
            governance_object_type,
            governance_object_id,
            governance_source_hash,
        )
        if any(value is not None for value in governance_values) and not all(
            value is not None for value in governance_values
        ):
            raise ProjectError("CrossPlaneExecutionBinding requires complete governance identity")
        return cls(
            root_claim_snapshot_hash=_hash(root_claim_snapshot_hash, "root_claim_snapshot_hash")
            or "",
            research_map_id=_text(research_map_id, "research_map_id"),
            research_map_version=research_map_version,
            research_map_hash=_hash(research_map_hash, "research_map_hash"),
            research_obligation_id=_text(research_obligation_id, "research_obligation_id"),
            directive_id=_text(directive_id, "directive_id"),
            tactical_session_id=_text(tactical_session_id, "tactical_session_id"),
            governance_object_type=_text(governance_object_type, "governance_object_type"),
            governance_object_id=_text(governance_object_id, "governance_object_id"),
            governance_source_hash=_hash(governance_source_hash, "governance_source_hash"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CrossPlaneExecutionBinding":
        expected = {
            "root_claim_snapshot_hash",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "research_obligation_id",
            "directive_id",
            "tactical_session_id",
            "governance_object_type",
            "governance_object_id",
            "governance_source_hash",
        }
        if set(value) != expected:
            raise ProjectError(
                "CrossPlaneExecutionBinding fields do not match schema; "
                f"missing={sorted(expected - set(value))}, unknown={sorted(set(value) - expected)}"
            )
        return cls.capture(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_claim_snapshot_hash": self.root_claim_snapshot_hash,
            "research_map_id": self.research_map_id,
            "research_map_version": self.research_map_version,
            "research_map_hash": self.research_map_hash,
            "research_obligation_id": self.research_obligation_id,
            "directive_id": self.directive_id,
            "tactical_session_id": self.tactical_session_id,
            "governance_object_type": self.governance_object_type,
            "governance_object_id": self.governance_object_id,
            "governance_source_hash": self.governance_source_hash,
        }

    @property
    def binding_hash(self) -> str:
        return content_hash(self.to_dict())

    def matches(self, other: "CrossPlaneExecutionBinding | None") -> bool:
        return other is not None and self.to_dict() == other.to_dict()


def coerce_binding(
    value: CrossPlaneExecutionBinding | Mapping[str, Any] | None,
) -> CrossPlaneExecutionBinding | None:
    if value is None:
        return None
    if isinstance(value, CrossPlaneExecutionBinding):
        return value
    if isinstance(value, Mapping):
        return CrossPlaneExecutionBinding.from_dict(value)
    raise ProjectError("execution_binding must be CrossPlaneExecutionBinding or an object")


def binding_json(value: CrossPlaneExecutionBinding | None) -> str | None:
    if value is None:
        return None
    import json

    return json.dumps(value.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
