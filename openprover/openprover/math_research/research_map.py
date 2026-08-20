"""Immutable, versioned and explicitly non-authoritative ResearchMaps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .project import ProjectError
from .research_common import (
    RESEARCH_SCHEMA_VERSION,
    artifact_dict,
    require_hash,
    require_id,
    require_optional_text,
    require_text,
    stable_value,
    strict_fields,
    string_tuple,
    validate_envelope,
)
from .research_obligation import ObligationDispositionKind
from .truth_identity import domain_hash


class MapRevisionReason(str, Enum):
    INITIAL = "INITIAL"
    EVIDENCE_INTEGRATION = "EVIDENCE_INTEGRATION"
    OBLIGATION_RESOLVED = "OBLIGATION_RESOLVED"
    OBLIGATION_BLOCKED = "OBLIGATION_BLOCKED"
    NEW_OBLIGATION = "NEW_OBLIGATION"
    SCOPE_SUPERSESSION = "SCOPE_SUPERSESSION"
    ROUTE_FAILURE = "ROUTE_FAILURE"
    ROOT_REBASE = "ROOT_REBASE"
    HUMAN_STEERING = "HUMAN_STEERING"


@dataclass(frozen=True, slots=True)
class ResearchRelation:
    source_node: str
    relation_kind: str
    target_node: str

    @classmethod
    def capture(cls, source_node: str, relation_kind: str, target_node: str):
        return cls(
            source_node=require_text(source_node, "ResearchRelation.source_node"),
            relation_kind=require_text(relation_kind, "ResearchRelation.relation_kind"),
            target_node=require_text(target_node, "ResearchRelation.target_node"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        strict_fields(value, {"source_node", "relation_kind", "target_node"}, "ResearchRelation")
        return cls.capture(value["source_node"], value["relation_kind"], value["target_node"])

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ObligationRef:
    obligation_id: str
    obligation_hash: str
    disposition: str
    disposition_hash: str

    @classmethod
    def capture(
        cls, obligation_id: str, obligation_hash: str, disposition: str, disposition_hash: str
    ) -> "ObligationRef":
        if disposition not in {item.value for item in ObligationDispositionKind}:
            raise ProjectError(f"Unsupported obligation disposition projection: {disposition}")
        return cls(
            obligation_id=require_id(obligation_id, "ObligationRef.obligation_id"),
            obligation_hash=require_hash(obligation_hash, "ObligationRef.obligation_hash"),
            disposition=disposition,
            disposition_hash=require_hash(disposition_hash, "ObligationRef.disposition_hash"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        strict_fields(
            value,
            {"obligation_id", "obligation_hash", "disposition", "disposition_hash"},
            "ObligationRef",
        )
        return cls.capture(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ResearchMap:
    schema_version: int
    object_type: str
    research_map_id: str
    version: int
    root_theorem_id: str
    root_claim_snapshot_hash: str
    parent_version_ref: str | None
    structural_nodes: tuple[str, ...]
    relations: tuple[ResearchRelation, ...]
    known_invariants: tuple[str, ...]
    open_obstructions: tuple[str, ...]
    unbounded_parameters: tuple[str, ...]
    termination_mechanisms: tuple[str, ...]
    obligation_refs: tuple[ObligationRef, ...]
    route_failure_refs: tuple[str, ...]
    strategic_thesis: str
    added_scope: tuple[str, ...]
    removed_or_reframed_scope: tuple[str, ...]
    obligation_changes: tuple[str, ...]
    route_memory_changes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    created_at: str
    created_by: str
    revision_reason: str
    research_map_hash: str

    @classmethod
    def capture(
        cls,
        *,
        research_map_id: str,
        version: int,
        root_theorem_id: str,
        root_claim_snapshot_hash: str,
        obligation_refs: tuple[ObligationRef, ...] | list[ObligationRef],
        created_at: str,
        created_by: str,
        revision_reason: str,
        parent_version_ref: str | None = None,
        structural_nodes: tuple[str, ...] | list[str] = (),
        relations: tuple[ResearchRelation, ...] | list[ResearchRelation] = (),
        known_invariants: tuple[str, ...] | list[str] = (),
        open_obstructions: tuple[str, ...] | list[str] = (),
        unbounded_parameters: tuple[str, ...] | list[str] = (),
        termination_mechanisms: tuple[str, ...] | list[str] = (),
        route_failure_refs: tuple[str, ...] | list[str] = (),
        strategic_thesis: str = "",
        added_scope: tuple[str, ...] | list[str] = (),
        removed_or_reframed_scope: tuple[str, ...] | list[str] = (),
        obligation_changes: tuple[str, ...] | list[str] = (),
        route_memory_changes: tuple[str, ...] | list[str] = (),
        evidence_refs: tuple[str, ...] | list[str] = (),
    ) -> "ResearchMap":
        if version < 1:
            raise ProjectError("ResearchMap.version starts at 1")
        if revision_reason not in {item.value for item in MapRevisionReason}:
            raise ProjectError(f"Unsupported ResearchMap revision reason: {revision_reason}")
        if version == 1:
            if parent_version_ref is not None or revision_reason != MapRevisionReason.INITIAL.value:
                raise ProjectError("Initial ResearchMap requires INITIAL and no parent")
            parent = None
        else:
            parent = require_hash(parent_version_ref, "ResearchMap.parent_version_ref")
            if revision_reason == MapRevisionReason.INITIAL.value:
                raise ProjectError("Only ResearchMap version 1 can use INITIAL")
        refs = tuple(sorted(obligation_refs, key=lambda item: item.obligation_id))
        if not refs:
            raise ProjectError("ResearchMap requires at least one obligation")
        if len({item.obligation_id for item in refs}) != len(refs):
            raise ProjectError("ResearchMap has duplicate obligation ids")
        relation_values = tuple(relations)
        if not all(isinstance(item, ResearchRelation) for item in relation_values):
            raise ProjectError("ResearchMap.relations must contain ResearchRelation")
        identity = {
            "research_map_id": require_id(research_map_id, "ResearchMap.research_map_id"),
            "version": version,
            "root_theorem_id": require_id(root_theorem_id, "ResearchMap.root_theorem_id"),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "ResearchMap.root_claim_snapshot_hash"
            ),
            "parent_version_ref": parent,
            "structural_nodes": list(
                string_tuple(structural_nodes, "ResearchMap.structural_nodes")
            ),
            "relations": [item.to_dict() for item in relation_values],
            "known_invariants": list(
                string_tuple(known_invariants, "ResearchMap.known_invariants")
            ),
            "open_obstructions": list(
                string_tuple(open_obstructions, "ResearchMap.open_obstructions")
            ),
            "unbounded_parameters": list(
                string_tuple(unbounded_parameters, "ResearchMap.unbounded_parameters")
            ),
            "termination_mechanisms": list(
                string_tuple(termination_mechanisms, "ResearchMap.termination_mechanisms")
            ),
            "obligation_refs": [item.to_dict() for item in refs],
            "route_failure_refs": list(
                string_tuple(route_failure_refs, "ResearchMap.route_failure_refs")
            ),
            "strategic_thesis": require_optional_text(
                strategic_thesis, "ResearchMap.strategic_thesis"
            ),
            "added_scope": list(string_tuple(added_scope, "ResearchMap.added_scope")),
            "removed_or_reframed_scope": list(
                string_tuple(removed_or_reframed_scope, "ResearchMap.removed_or_reframed_scope")
            ),
            "obligation_changes": list(
                string_tuple(obligation_changes, "ResearchMap.obligation_changes")
            ),
            "route_memory_changes": list(
                string_tuple(route_memory_changes, "ResearchMap.route_memory_changes")
            ),
            "evidence_refs": list(string_tuple(evidence_refs, "ResearchMap.evidence_refs")),
            "revision_reason": revision_reason,
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="RESEARCH_MAP",
            research_map_id=identity["research_map_id"],
            version=identity["version"],
            root_theorem_id=identity["root_theorem_id"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            parent_version_ref=identity["parent_version_ref"],
            structural_nodes=tuple(identity["structural_nodes"]),
            relations=relation_values,
            known_invariants=tuple(identity["known_invariants"]),
            open_obstructions=tuple(identity["open_obstructions"]),
            unbounded_parameters=tuple(identity["unbounded_parameters"]),
            termination_mechanisms=tuple(identity["termination_mechanisms"]),
            obligation_refs=refs,
            route_failure_refs=tuple(identity["route_failure_refs"]),
            strategic_thesis=identity["strategic_thesis"],
            added_scope=tuple(identity["added_scope"]),
            removed_or_reframed_scope=tuple(identity["removed_or_reframed_scope"]),
            obligation_changes=tuple(identity["obligation_changes"]),
            route_memory_changes=tuple(identity["route_memory_changes"]),
            evidence_refs=tuple(identity["evidence_refs"]),
            created_at=require_text(created_at, "ResearchMap.created_at"),
            created_by=require_text(created_by, "ResearchMap.created_by"),
            revision_reason=identity["revision_reason"],
            research_map_hash=domain_hash("research_map", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchMap":
        fields = {
            "schema_version",
            "object_type",
            "research_map_id",
            "version",
            "root_theorem_id",
            "root_claim_snapshot_hash",
            "parent_version_ref",
            "structural_nodes",
            "relations",
            "known_invariants",
            "open_obstructions",
            "unbounded_parameters",
            "termination_mechanisms",
            "obligation_refs",
            "route_failure_refs",
            "strategic_thesis",
            "added_scope",
            "removed_or_reframed_scope",
            "obligation_changes",
            "route_memory_changes",
            "evidence_refs",
            "created_at",
            "created_by",
            "revision_reason",
            "research_map_hash",
        }
        strict_fields(value, fields, "ResearchMap")
        validate_envelope(value, object_type="RESEARCH_MAP", name="ResearchMap")
        if not isinstance(value.get("relations"), list) or not isinstance(
            value.get("obligation_refs"), list
        ):
            raise ProjectError("ResearchMap relations and obligation_refs must be lists")
        captured = cls.capture(
            research_map_id=value["research_map_id"],
            version=value["version"],
            root_theorem_id=value["root_theorem_id"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            parent_version_ref=value["parent_version_ref"],
            structural_nodes=value["structural_nodes"],
            relations=[ResearchRelation.from_dict(item) for item in value["relations"]],
            known_invariants=value["known_invariants"],
            open_obstructions=value["open_obstructions"],
            unbounded_parameters=value["unbounded_parameters"],
            termination_mechanisms=value["termination_mechanisms"],
            obligation_refs=[ObligationRef.from_dict(item) for item in value["obligation_refs"]],
            route_failure_refs=value["route_failure_refs"],
            strategic_thesis=value["strategic_thesis"],
            added_scope=value["added_scope"],
            removed_or_reframed_scope=value["removed_or_reframed_scope"],
            obligation_changes=value["obligation_changes"],
            route_memory_changes=value["route_memory_changes"],
            evidence_refs=value["evidence_refs"],
            created_at=value["created_at"],
            created_by=value["created_by"],
            revision_reason=value["revision_reason"],
        )
        if captured.research_map_hash != value.get("research_map_hash"):
            raise ProjectError("ResearchMap hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["relations"] = [item.to_dict() for item in self.relations]
        value["obligation_refs"] = [item.to_dict() for item in self.obligation_refs]
        return value

    def obligation_ref(self, obligation_id: str) -> ObligationRef:
        for item in self.obligation_refs:
            if item.obligation_id == obligation_id:
                return item
        raise ProjectError(f"ResearchMap does not contain obligation: {obligation_id}")

    @property
    def open_obligation_ids(self) -> tuple[str, ...]:
        return tuple(
            item.obligation_id
            for item in self.obligation_refs
            if item.disposition
            in {
                ObligationDispositionKind.OPEN.value,
                ObligationDispositionKind.BLOCKED.value,
            }
        )


@dataclass(frozen=True, slots=True)
class ResearchMapRebase:
    schema_version: int
    object_type: str
    rebase_id: str
    research_map_id: str
    source_map_hash: str
    resulting_map_hash: str
    old_claim_snapshot_hash: str
    new_claim_snapshot_hash: str
    compatibility_status: str
    compatibility_disposition: str
    carried_obligation_ids: tuple[str, ...]
    revalidation_required_obligation_ids: tuple[str, ...]
    invalid_obligation_ids: tuple[str, ...]
    reason: str
    created_at: str
    created_by: str
    rebase_hash: str

    @classmethod
    def capture(
        cls,
        *,
        research_map_id: str,
        source_map_hash: str,
        resulting_map_hash: str,
        old_claim_snapshot_hash: str,
        new_claim_snapshot_hash: str,
        compatibility_status: str,
        compatibility_disposition: str,
        carried_obligation_ids: tuple[str, ...] | list[str],
        revalidation_required_obligation_ids: tuple[str, ...] | list[str],
        invalid_obligation_ids: tuple[str, ...] | list[str],
        reason: str,
        created_at: str,
        created_by: str,
    ) -> "ResearchMapRebase":
        carried = string_tuple(
            carried_obligation_ids, "ResearchMapRebase.carried_obligation_ids", allow_empty=False
        )
        revalidate = string_tuple(
            revalidation_required_obligation_ids,
            "ResearchMapRebase.revalidation_required_obligation_ids",
            allow_empty=False,
        )
        invalid = string_tuple(
            invalid_obligation_ids,
            "ResearchMapRebase.invalid_obligation_ids",
            allow_empty=False,
        )
        groups = [set(carried), set(revalidate), set(invalid)]
        if any(groups[i].intersection(groups[j]) for i in range(3) for j in range(i + 1, 3)):
            raise ProjectError("ResearchMapRebase obligation classifications overlap")
        identity = {
            "research_map_id": require_id(research_map_id, "ResearchMapRebase.research_map_id"),
            "source_map_hash": require_hash(source_map_hash, "ResearchMapRebase.source_map_hash"),
            "resulting_map_hash": require_hash(
                resulting_map_hash, "ResearchMapRebase.resulting_map_hash"
            ),
            "old_claim_snapshot_hash": require_hash(
                old_claim_snapshot_hash, "ResearchMapRebase.old_claim_snapshot_hash"
            ),
            "new_claim_snapshot_hash": require_hash(
                new_claim_snapshot_hash, "ResearchMapRebase.new_claim_snapshot_hash"
            ),
            "compatibility_status": require_text(
                compatibility_status, "ResearchMapRebase.compatibility_status"
            ),
            "compatibility_disposition": require_text(
                compatibility_disposition, "ResearchMapRebase.compatibility_disposition"
            ),
            "carried_obligation_ids": list(carried),
            "revalidation_required_obligation_ids": list(revalidate),
            "invalid_obligation_ids": list(invalid),
            "reason": require_text(reason, "ResearchMapRebase.reason"),
        }
        rebase_hash = domain_hash("research_map_rebase", identity)
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="RESEARCH_MAP_REBASE",
            rebase_id="rebase-" + rebase_hash.removeprefix("sha256:")[:24],
            research_map_id=identity["research_map_id"],
            source_map_hash=identity["source_map_hash"],
            resulting_map_hash=identity["resulting_map_hash"],
            old_claim_snapshot_hash=identity["old_claim_snapshot_hash"],
            new_claim_snapshot_hash=identity["new_claim_snapshot_hash"],
            compatibility_status=identity["compatibility_status"],
            compatibility_disposition=identity["compatibility_disposition"],
            carried_obligation_ids=carried,
            revalidation_required_obligation_ids=revalidate,
            invalid_obligation_ids=invalid,
            reason=identity["reason"],
            created_at=require_text(created_at, "ResearchMapRebase.created_at"),
            created_by=require_text(created_by, "ResearchMapRebase.created_by"),
            rebase_hash=rebase_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchMapRebase":
        fields = {
            "schema_version",
            "object_type",
            "rebase_id",
            "research_map_id",
            "source_map_hash",
            "resulting_map_hash",
            "old_claim_snapshot_hash",
            "new_claim_snapshot_hash",
            "compatibility_status",
            "compatibility_disposition",
            "carried_obligation_ids",
            "revalidation_required_obligation_ids",
            "invalid_obligation_ids",
            "reason",
            "created_at",
            "created_by",
            "rebase_hash",
        }
        strict_fields(value, fields, "ResearchMapRebase")
        validate_envelope(value, object_type="RESEARCH_MAP_REBASE", name="ResearchMapRebase")
        captured = cls.capture(
            research_map_id=value["research_map_id"],
            source_map_hash=value["source_map_hash"],
            resulting_map_hash=value["resulting_map_hash"],
            old_claim_snapshot_hash=value["old_claim_snapshot_hash"],
            new_claim_snapshot_hash=value["new_claim_snapshot_hash"],
            compatibility_status=value["compatibility_status"],
            compatibility_disposition=value["compatibility_disposition"],
            carried_obligation_ids=value["carried_obligation_ids"],
            revalidation_required_obligation_ids=value["revalidation_required_obligation_ids"],
            invalid_obligation_ids=value["invalid_obligation_ids"],
            reason=value["reason"],
            created_at=value["created_at"],
            created_by=value["created_by"],
        )
        if captured.rebase_id != value.get("rebase_id") or captured.rebase_hash != value.get(
            "rebase_hash"
        ):
            raise ProjectError("ResearchMapRebase identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)
