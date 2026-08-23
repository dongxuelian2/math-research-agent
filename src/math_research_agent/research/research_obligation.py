"""Durable ResearchObligation semantics, separate from execution state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .project import ProjectError
from .research_common import (
    RESEARCH_SCHEMA_VERSION,
    artifact_dict,
    content_id,
    require_hash,
    require_id,
    require_optional_text,
    require_text,
    strict_fields,
    string_tuple,
    validate_envelope,
)
from .truth_identity import domain_hash


class ObligationKind(str, Enum):
    LEMMA = "LEMMA"
    CASE = "CASE"
    OBSTRUCTION = "OBSTRUCTION"
    EXHAUSTIVENESS = "EXHAUSTIVENESS"
    CONVERSE = "CONVERSE"
    BOUNDARY = "BOUNDARY"
    DEPENDENCY = "DEPENDENCY"
    AUTHORITY = "AUTHORITY"
    COMPUTATIONAL = "COMPUTATIONAL"
    STRUCTURAL = "STRUCTURAL"
    OTHER = "OTHER"


class ObligationDispositionKind(str, Enum):
    OPEN = "OPEN"
    BLOCKED = "BLOCKED"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"
    ABANDONED_WITH_REASON = "ABANDONED_WITH_REASON"


EXECUTION_ONLY_DISPOSITIONS = frozenset(
    {
        "RUNNING",
        "RETRY_PENDING",
        "QUEUED",
        "PROVIDER_ERROR",
        "WAITING_FOR_WORKER",
        "READY",
        "ACTIVE",
        "CLOSED",
    }
)


@dataclass(frozen=True, slots=True)
class ResearchObligation:
    schema_version: int
    object_type: str
    obligation_id: str
    semantic_revision: int
    root_claim_snapshot_hash: str
    created_in_map_version: int
    title: str
    statement: str
    obligation_kind: str
    scope: tuple[str, ...]
    dependencies: tuple[str, ...]
    previous_revision_hash: str | None
    created_at: str
    revised_at: str
    obligation_hash: str

    @classmethod
    def capture(
        cls,
        *,
        obligation_id: str,
        root_claim_snapshot_hash: str,
        created_in_map_version: int,
        title: str,
        statement: str,
        obligation_kind: str,
        scope: tuple[str, ...] | list[str] = (),
        dependencies: tuple[str, ...] | list[str] = (),
        created_at: str,
        semantic_revision: int = 1,
        revised_at: str | None = None,
        previous_revision_hash: str | None = None,
    ) -> "ResearchObligation":
        oid = require_id(obligation_id, "ResearchObligation.obligation_id")
        root_hash = require_hash(
            root_claim_snapshot_hash, "ResearchObligation.root_claim_snapshot_hash"
        )
        if obligation_kind not in {item.value for item in ObligationKind}:
            raise ProjectError(f"Unsupported ResearchObligation kind: {obligation_kind}")
        if semantic_revision < 1 or created_in_map_version < 1:
            raise ProjectError("ResearchObligation revisions and map versions start at 1")
        previous = None
        if semantic_revision == 1:
            if previous_revision_hash is not None:
                raise ProjectError("Initial ResearchObligation cannot have a previous revision")
        else:
            previous = require_hash(
                previous_revision_hash, "ResearchObligation.previous_revision_hash"
            )
        normalized_scope = string_tuple(scope, "ResearchObligation.scope")
        normalized_dependencies = string_tuple(
            dependencies, "ResearchObligation.dependencies", allow_empty=False
        )
        identity = {
            "obligation_id": oid,
            "semantic_revision": semantic_revision,
            "root_claim_snapshot_hash": root_hash,
            "created_in_map_version": created_in_map_version,
            "title": require_text(title, "ResearchObligation.title"),
            "statement": require_text(statement, "ResearchObligation.statement"),
            "obligation_kind": obligation_kind,
            "scope": list(normalized_scope),
            "dependencies": list(normalized_dependencies),
            "previous_revision_hash": previous,
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="RESEARCH_OBLIGATION",
            obligation_id=identity["obligation_id"],
            semantic_revision=identity["semantic_revision"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            created_in_map_version=identity["created_in_map_version"],
            title=identity["title"],
            statement=identity["statement"],
            obligation_kind=identity["obligation_kind"],
            scope=normalized_scope,
            dependencies=normalized_dependencies,
            previous_revision_hash=identity["previous_revision_hash"],
            created_at=require_text(created_at, "ResearchObligation.created_at"),
            revised_at=require_text(revised_at or created_at, "ResearchObligation.revised_at"),
            obligation_hash=domain_hash("research_obligation", identity),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchObligation":
        fields = {
            "schema_version",
            "object_type",
            "obligation_id",
            "semantic_revision",
            "root_claim_snapshot_hash",
            "created_in_map_version",
            "title",
            "statement",
            "obligation_kind",
            "scope",
            "dependencies",
            "previous_revision_hash",
            "created_at",
            "revised_at",
            "obligation_hash",
        }
        strict_fields(value, fields, "ResearchObligation")
        validate_envelope(value, object_type="RESEARCH_OBLIGATION", name="ResearchObligation")
        captured = cls.capture(
            obligation_id=value["obligation_id"],
            semantic_revision=value["semantic_revision"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            created_in_map_version=value["created_in_map_version"],
            title=value["title"],
            statement=value["statement"],
            obligation_kind=value["obligation_kind"],
            scope=value["scope"],
            dependencies=value["dependencies"],
            previous_revision_hash=value["previous_revision_hash"],
            created_at=value["created_at"],
            revised_at=value["revised_at"],
        )
        if captured.obligation_hash != value.get("obligation_hash"):
            raise ProjectError("ResearchObligation hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ObligationDisposition:
    schema_version: int
    object_type: str
    disposition_id: str
    obligation_id: str
    obligation_hash: str
    disposition: str
    blocker_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    route_failure_refs: tuple[str, ...]
    resolution_basis: str
    superseded_by: tuple[str, ...]
    reason: str
    previous_disposition_hash: str | None
    recorded_at: str
    recorded_by: str
    disposition_hash: str

    @classmethod
    def capture(
        cls,
        *,
        obligation_id: str,
        obligation_hash: str,
        disposition: str,
        recorded_at: str,
        recorded_by: str,
        blocker_refs: tuple[str, ...] | list[str] = (),
        evidence_refs: tuple[str, ...] | list[str] = (),
        route_failure_refs: tuple[str, ...] | list[str] = (),
        resolution_basis: str = "",
        superseded_by: tuple[str, ...] | list[str] = (),
        reason: str = "",
        previous_disposition_hash: str | None = None,
    ) -> "ObligationDisposition":
        if disposition in EXECUTION_ONLY_DISPOSITIONS:
            raise ProjectError(f"Execution state cannot be a research disposition: {disposition}")
        if disposition not in {item.value for item in ObligationDispositionKind}:
            raise ProjectError(f"Unsupported obligation disposition: {disposition}")
        blockers = string_tuple(blocker_refs, "ObligationDisposition.blocker_refs")
        evidence = string_tuple(evidence_refs, "ObligationDisposition.evidence_refs")
        failures = string_tuple(route_failure_refs, "ObligationDisposition.route_failure_refs")
        replacements = string_tuple(
            superseded_by, "ObligationDisposition.superseded_by", allow_empty=False
        )
        basis = require_optional_text(resolution_basis, "ObligationDisposition.resolution_basis")
        normalized_reason = require_optional_text(reason, "ObligationDisposition.reason")
        if disposition == ObligationDispositionKind.BLOCKED.value and not blockers:
            raise ProjectError("BLOCKED disposition requires blocker_refs")
        if disposition == ObligationDispositionKind.RESOLVED.value and not (evidence and basis):
            raise ProjectError("RESOLVED disposition requires evidence_refs and resolution_basis")
        if disposition == ObligationDispositionKind.SUPERSEDED.value and not replacements:
            raise ProjectError("SUPERSEDED disposition requires superseded_by")
        if (
            disposition == ObligationDispositionKind.ABANDONED_WITH_REASON.value
            and not normalized_reason
        ):
            raise ProjectError("ABANDONED_WITH_REASON requires reason")
        previous = (
            require_hash(previous_disposition_hash, "previous_disposition_hash")
            if previous_disposition_hash is not None
            else None
        )
        identity = {
            "obligation_id": require_id(obligation_id, "ObligationDisposition.obligation_id"),
            "obligation_hash": require_hash(
                obligation_hash, "ObligationDisposition.obligation_hash"
            ),
            "disposition": disposition,
            "blocker_refs": list(blockers),
            "evidence_refs": list(evidence),
            "route_failure_refs": list(failures),
            "resolution_basis": basis,
            "superseded_by": list(replacements),
            "reason": normalized_reason,
            "previous_disposition_hash": previous,
        }
        disposition_hash = domain_hash("obligation_disposition", identity)
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="OBLIGATION_DISPOSITION",
            disposition_id=content_id("od", "obligation_disposition_id", identity),
            obligation_id=identity["obligation_id"],
            obligation_hash=identity["obligation_hash"],
            disposition=identity["disposition"],
            blocker_refs=blockers,
            evidence_refs=evidence,
            route_failure_refs=failures,
            resolution_basis=identity["resolution_basis"],
            superseded_by=replacements,
            reason=identity["reason"],
            previous_disposition_hash=identity["previous_disposition_hash"],
            recorded_at=require_text(recorded_at, "ObligationDisposition.recorded_at"),
            recorded_by=require_text(recorded_by, "ObligationDisposition.recorded_by"),
            disposition_hash=disposition_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObligationDisposition":
        fields = {
            "schema_version",
            "object_type",
            "disposition_id",
            "obligation_id",
            "obligation_hash",
            "disposition",
            "blocker_refs",
            "evidence_refs",
            "route_failure_refs",
            "resolution_basis",
            "superseded_by",
            "reason",
            "previous_disposition_hash",
            "recorded_at",
            "recorded_by",
            "disposition_hash",
        }
        strict_fields(value, fields, "ObligationDisposition")
        validate_envelope(value, object_type="OBLIGATION_DISPOSITION", name="ObligationDisposition")
        captured = cls.capture(
            obligation_id=value["obligation_id"],
            obligation_hash=value["obligation_hash"],
            disposition=value["disposition"],
            blocker_refs=value["blocker_refs"],
            evidence_refs=value["evidence_refs"],
            route_failure_refs=value["route_failure_refs"],
            resolution_basis=value["resolution_basis"],
            superseded_by=value["superseded_by"],
            reason=value["reason"],
            previous_disposition_hash=value["previous_disposition_hash"],
            recorded_at=value["recorded_at"],
            recorded_by=value["recorded_by"],
        )
        if captured.disposition_id != value.get("disposition_id"):
            raise ProjectError("ObligationDisposition id mismatch")
        if captured.disposition_hash != value.get("disposition_hash"):
            raise ProjectError("ObligationDisposition hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)
