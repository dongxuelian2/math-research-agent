"""Typed architecture patches, scope transfers, and authorization receipts."""

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
    stable_value,
    strict_fields,
    string_tuple,
    validate_envelope,
)
from .truth_identity import domain_hash


class PatchClassification(str, Enum):
    LOCAL_ADJUSTMENT = "LOCAL_ADJUSTMENT"
    DESTRUCTIVE_PATCH = "DESTRUCTIVE_PATCH"


class PatchOperationKind(str, Enum):
    CLARIFY_NOTE = "CLARIFY_NOTE"
    ATTACH_EVIDENCE = "ATTACH_EVIDENCE"
    ADD_LOCAL_OBLIGATION = "ADD_LOCAL_OBLIGATION"
    RECORD_ROUTE_FAILURE = "RECORD_ROUTE_FAILURE"
    REPLACE_PARTITION = "REPLACE_PARTITION"
    CHANGE_PARAMETERIZATION = "CHANGE_PARAMETERIZATION"
    MERGE_OBLIGATION_FAMILIES = "MERGE_OBLIGATION_FAMILIES"
    SPLIT_OBLIGATION_FAMILY = "SPLIT_OBLIGATION_FAMILY"
    ABANDON_MAJOR_ROUTE_FAMILY = "ABANDON_MAJOR_ROUTE_FAMILY"
    CHANGE_STRATEGIC_THESIS = "CHANGE_STRATEGIC_THESIS"
    REMOVE_ROOT_RELEVANT_SCOPE = "REMOVE_ROOT_RELEVANT_SCOPE"
    CHANGE_TERMINATION_ARCHITECTURE = "CHANGE_TERMINATION_ARCHITECTURE"


_DESTRUCTIVE_OPERATIONS = {
    PatchOperationKind.REPLACE_PARTITION,
    PatchOperationKind.CHANGE_PARAMETERIZATION,
    PatchOperationKind.MERGE_OBLIGATION_FAMILIES,
    PatchOperationKind.SPLIT_OBLIGATION_FAMILY,
    PatchOperationKind.ABANDON_MAJOR_ROUTE_FAMILY,
    PatchOperationKind.CHANGE_STRATEGIC_THESIS,
    PatchOperationKind.REMOVE_ROOT_RELEVANT_SCOPE,
    PatchOperationKind.CHANGE_TERMINATION_ARCHITECTURE,
}


def classify_patch(operation_kinds: tuple[str, ...] | list[str]) -> str:
    operations = tuple(PatchOperationKind(item) for item in operation_kinds)
    if not operations:
        raise ProjectError("ArchitecturePatch requires operation kinds")
    return (
        PatchClassification.DESTRUCTIVE_PATCH.value
        if any(item in _DESTRUCTIVE_OPERATIONS for item in operations)
        else PatchClassification.LOCAL_ADJUSTMENT.value
    )


class ScopeTransferDisposition(str, Enum):
    TRANSFERRED = "TRANSFERRED"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"
    ABANDONED_WITH_REASON = "ABANDONED_WITH_REASON"


@dataclass(frozen=True, slots=True)
class ScopeTransfer:
    source_obligation_ids: tuple[str, ...]
    target_obligation_ids: tuple[str, ...]
    disposition: str
    reason: str
    evidence_refs: tuple[str, ...]
    transfer_hash: str

    @classmethod
    def capture(
        cls,
        *,
        source_obligation_ids: tuple[str, ...] | list[str],
        target_obligation_ids: tuple[str, ...] | list[str],
        disposition: str,
        reason: str,
        evidence_refs: tuple[str, ...] | list[str],
    ) -> "ScopeTransfer":
        try:
            disposition_value = ScopeTransferDisposition(disposition).value
        except ValueError as exc:
            raise ProjectError(f"Unsupported ScopeTransfer disposition: {disposition}") from exc
        sources = string_tuple(
            source_obligation_ids, "ScopeTransfer.source_obligation_ids", allow_empty=False
        )
        targets = string_tuple(target_obligation_ids, "ScopeTransfer.target_obligation_ids")
        if (
            disposition_value
            in {
                ScopeTransferDisposition.TRANSFERRED.value,
                ScopeTransferDisposition.SUPERSEDED.value,
            }
            and not targets
        ):
            raise ProjectError("Transferred/superseded scope requires target obligations")
        if (
            disposition_value
            in {
                ScopeTransferDisposition.RESOLVED.value,
                ScopeTransferDisposition.ABANDONED_WITH_REASON.value,
            }
            and targets
        ):
            raise ProjectError("Resolved/abandoned scope cannot also name transfer targets")
        evidence = string_tuple(evidence_refs, "ScopeTransfer.evidence_refs")
        if disposition_value == ScopeTransferDisposition.RESOLVED.value and not evidence:
            raise ProjectError("Resolved scope requires evidence")
        identity = {
            "source_obligation_ids": list(sources),
            "target_obligation_ids": list(targets),
            "disposition": disposition_value,
            "reason": require_text(reason, "ScopeTransfer.reason"),
            "evidence_refs": list(evidence),
        }
        return cls(
            source_obligation_ids=sources,
            target_obligation_ids=targets,
            disposition=disposition_value,
            reason=identity["reason"],
            evidence_refs=evidence,
            transfer_hash=domain_hash("scope_transfer", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScopeTransfer":
        strict_fields(
            value,
            {
                "source_obligation_ids",
                "target_obligation_ids",
                "disposition",
                "reason",
                "evidence_refs",
                "transfer_hash",
            },
            "ScopeTransfer",
        )
        captured = cls.capture(
            source_obligation_ids=value["source_obligation_ids"],
            target_obligation_ids=value["target_obligation_ids"],
            disposition=value["disposition"],
            reason=value["reason"],
            evidence_refs=value["evidence_refs"],
        )
        if captured.transfer_hash != value.get("transfer_hash"):
            raise ProjectError("ScopeTransfer hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class PatchObligationAddition:
    obligation_id: str
    title: str
    statement: str
    obligation_kind: str
    scope: tuple[str, ...]
    dependencies: tuple[str, ...]

    @classmethod
    def capture(
        cls,
        *,
        obligation_id: str,
        title: str,
        statement: str,
        obligation_kind: str,
        scope: tuple[str, ...] | list[str],
        dependencies: tuple[str, ...] | list[str] = (),
    ) -> "PatchObligationAddition":
        return cls(
            obligation_id=require_id(obligation_id, "PatchObligationAddition.obligation_id"),
            title=require_text(title, "PatchObligationAddition.title"),
            statement=require_text(statement, "PatchObligationAddition.statement"),
            obligation_kind=require_text(
                obligation_kind, "PatchObligationAddition.obligation_kind"
            ),
            scope=string_tuple(scope, "PatchObligationAddition.scope"),
            dependencies=string_tuple(dependencies, "PatchObligationAddition.dependencies"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PatchObligationAddition":
        strict_fields(
            value,
            {"obligation_id", "title", "statement", "obligation_kind", "scope", "dependencies"},
            "PatchObligationAddition",
        )
        return cls.capture(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ArchitecturePatch:
    schema_version: int
    object_type: str
    patch_id: str
    source_map_id: str
    source_map_version: int
    source_map_hash: str
    root_claim_snapshot_hash: str
    operation_kinds: tuple[str, ...]
    classification: str
    affected_obligation_ids: tuple[str, ...]
    additions: tuple[PatchObligationAddition, ...]
    scope_transfers: tuple[ScopeTransfer, ...]
    route_memory_changes: tuple[str, ...]
    structural_thesis_change: str
    removed_or_reframed_scope: tuple[str, ...]
    justification: str
    review_id: str
    review_hash: str
    probe_ids: tuple[str, ...]
    probe_hashes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    expected_structural_gain: str
    proposed_by: str
    created_at: str
    patch_hash: str

    @classmethod
    def capture(
        cls,
        *,
        source_map_id: str,
        source_map_version: int,
        source_map_hash: str,
        root_claim_snapshot_hash: str,
        operation_kinds: tuple[str, ...] | list[str],
        affected_obligation_ids: tuple[str, ...] | list[str],
        additions: tuple[PatchObligationAddition, ...] | list[PatchObligationAddition],
        scope_transfers: tuple[ScopeTransfer, ...] | list[ScopeTransfer],
        route_memory_changes: tuple[str, ...] | list[str],
        structural_thesis_change: str,
        removed_or_reframed_scope: tuple[str, ...] | list[str],
        justification: str,
        review_id: str,
        review_hash: str,
        probe_ids: tuple[str, ...] | list[str],
        probe_hashes: tuple[str, ...] | list[str],
        evidence_refs: tuple[str, ...] | list[str],
        expected_structural_gain: str,
        proposed_by: str,
        created_at: str,
    ) -> "ArchitecturePatch":
        if (
            not isinstance(source_map_version, int)
            or isinstance(source_map_version, bool)
            or source_map_version < 1
        ):
            raise ProjectError("ArchitecturePatch.source_map_version must be positive")
        try:
            operations = tuple(PatchOperationKind(item).value for item in operation_kinds)
        except ValueError as exc:
            raise ProjectError("Unsupported ArchitecturePatch operation") from exc
        if len(set(operations)) != len(operations):
            raise ProjectError("ArchitecturePatch.operation_kinds contains duplicates")
        classification = classify_patch(operations)
        affected = string_tuple(
            affected_obligation_ids, "ArchitecturePatch.affected_obligation_ids"
        )
        additions_value = tuple(additions)
        transfers = tuple(scope_transfers)
        if not all(isinstance(item, PatchObligationAddition) for item in additions_value):
            raise ProjectError("ArchitecturePatch.additions must be typed")
        if not all(isinstance(item, ScopeTransfer) for item in transfers):
            raise ProjectError("ArchitecturePatch.scope_transfers must be typed")
        addition_ids = [item.obligation_id for item in additions_value]
        if len(set(addition_ids)) != len(addition_ids):
            raise ProjectError("ArchitecturePatch has duplicate additions")
        probe_id_values = string_tuple(probe_ids, "ArchitecturePatch.probe_ids")
        probe_hash_values = string_tuple(probe_hashes, "ArchitecturePatch.probe_hashes")
        if len(probe_id_values) != len(probe_hash_values):
            raise ProjectError("ArchitecturePatch probe ids/hashes must have equal length")
        thesis_change = require_optional_text(
            structural_thesis_change, "ArchitecturePatch.structural_thesis_change"
        )
        removed = string_tuple(
            removed_or_reframed_scope,
            "ArchitecturePatch.removed_or_reframed_scope",
        )
        if thesis_change and PatchOperationKind.CHANGE_STRATEGIC_THESIS.value not in operations:
            raise ProjectError("Strategic thesis change requires CHANGE_STRATEGIC_THESIS")
        if classification == PatchClassification.LOCAL_ADJUSTMENT.value and (
            affected or transfers or removed or thesis_change
        ):
            raise ProjectError("Local adjustment cannot carry destructive scope changes")
        if classification == PatchClassification.DESTRUCTIVE_PATCH.value:
            if not affected:
                raise ProjectError("Destructive ArchitecturePatch requires affected obligations")
            covered_sources = [
                source for transfer in transfers for source in transfer.source_obligation_ids
            ]
            if len(set(covered_sources)) != len(covered_sources):
                raise ProjectError("Scope source appears in multiple transfers")
        identity = {
            "source_map_id": require_id(source_map_id, "ArchitecturePatch.source_map_id"),
            "source_map_version": source_map_version,
            "source_map_hash": require_hash(source_map_hash, "ArchitecturePatch.source_map_hash"),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash,
                "ArchitecturePatch.root_claim_snapshot_hash",
            ),
            "operation_kinds": list(operations),
            "classification": classification,
            "affected_obligation_ids": list(affected),
            "additions": [item.to_dict() for item in additions_value],
            "scope_transfers": [item.to_dict() for item in transfers],
            "route_memory_changes": list(
                string_tuple(route_memory_changes, "ArchitecturePatch.route_memory_changes")
            ),
            "structural_thesis_change": thesis_change,
            "removed_or_reframed_scope": list(removed),
            "justification": require_text(justification, "ArchitecturePatch.justification"),
            "review_id": require_id(review_id, "ArchitecturePatch.review_id"),
            "review_hash": require_hash(review_hash, "ArchitecturePatch.review_hash"),
            "probe_ids": list(probe_id_values),
            "probe_hashes": list(probe_hash_values),
            "evidence_refs": list(string_tuple(evidence_refs, "ArchitecturePatch.evidence_refs")),
            "expected_structural_gain": require_text(
                expected_structural_gain,
                "ArchitecturePatch.expected_structural_gain",
            ),
            "proposed_by": require_id(proposed_by, "ArchitecturePatch.proposed_by"),
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="ARCHITECTURE_PATCH",
            patch_id=content_id("patch", "architecture_patch_id", stable_value(identity)),
            source_map_id=identity["source_map_id"],
            source_map_version=source_map_version,
            source_map_hash=identity["source_map_hash"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            operation_kinds=operations,
            classification=classification,
            affected_obligation_ids=affected,
            additions=additions_value,
            scope_transfers=transfers,
            route_memory_changes=tuple(identity["route_memory_changes"]),
            structural_thesis_change=thesis_change,
            removed_or_reframed_scope=removed,
            justification=identity["justification"],
            review_id=identity["review_id"],
            review_hash=identity["review_hash"],
            probe_ids=probe_id_values,
            probe_hashes=probe_hash_values,
            evidence_refs=tuple(identity["evidence_refs"]),
            expected_structural_gain=identity["expected_structural_gain"],
            proposed_by=identity["proposed_by"],
            created_at=require_text(created_at, "ArchitecturePatch.created_at"),
            patch_hash=domain_hash("architecture_patch", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArchitecturePatch":
        fields = {
            "schema_version",
            "object_type",
            "patch_id",
            "source_map_id",
            "source_map_version",
            "source_map_hash",
            "root_claim_snapshot_hash",
            "operation_kinds",
            "classification",
            "affected_obligation_ids",
            "additions",
            "scope_transfers",
            "route_memory_changes",
            "structural_thesis_change",
            "removed_or_reframed_scope",
            "justification",
            "review_id",
            "review_hash",
            "probe_ids",
            "probe_hashes",
            "evidence_refs",
            "expected_structural_gain",
            "proposed_by",
            "created_at",
            "patch_hash",
        }
        strict_fields(value, fields, "ArchitecturePatch")
        validate_envelope(value, object_type="ARCHITECTURE_PATCH", name="ArchitecturePatch")
        captured = cls.capture(
            source_map_id=value["source_map_id"],
            source_map_version=value["source_map_version"],
            source_map_hash=value["source_map_hash"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            operation_kinds=value["operation_kinds"],
            affected_obligation_ids=value["affected_obligation_ids"],
            additions=[PatchObligationAddition.from_dict(item) for item in value["additions"]],
            scope_transfers=[ScopeTransfer.from_dict(item) for item in value["scope_transfers"]],
            route_memory_changes=value["route_memory_changes"],
            structural_thesis_change=value["structural_thesis_change"],
            removed_or_reframed_scope=value["removed_or_reframed_scope"],
            justification=value["justification"],
            review_id=value["review_id"],
            review_hash=value["review_hash"],
            probe_ids=value["probe_ids"],
            probe_hashes=value["probe_hashes"],
            evidence_refs=value["evidence_refs"],
            expected_structural_gain=value["expected_structural_gain"],
            proposed_by=value["proposed_by"],
            created_at=value["created_at"],
        )
        if captured.classification != value.get("classification"):
            raise ProjectError("ArchitecturePatch classification is not deterministic")
        if captured.patch_id != value.get("patch_id") or captured.patch_hash != value.get(
            "patch_hash"
        ):
            raise ProjectError("ArchitecturePatch identity mismatch")
        return captured

    @property
    def probe_required(self) -> bool:
        return self.classification == PatchClassification.DESTRUCTIVE_PATCH.value

    @property
    def scope_transfer_complete(self) -> bool:
        sources = {
            source for transfer in self.scope_transfers for source in transfer.source_obligation_ids
        }
        return sources == set(self.affected_obligation_ids)

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["additions"] = [item.to_dict() for item in self.additions]
        value["scope_transfers"] = [item.to_dict() for item in self.scope_transfers]
        return value


class PatchAuthorizationStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class PatchAuthorization:
    schema_version: int
    object_type: str
    authorization_id: str
    patch_id: str
    patch_hash: str
    review_id: str
    review_hash: str
    critic_id: str
    critic_hash: str
    probe_ids: tuple[str, ...]
    probe_hashes: tuple[str, ...]
    root_claim_snapshot_hash: str
    source_map_hash: str
    status: str
    scope_validation_passed: bool
    truth_boundary_intact: bool
    invalidated_evidence_refs: tuple[str, ...]
    reason: str
    authorized_by: str
    created_at: str
    authorization_hash: str

    @classmethod
    def capture(
        cls,
        *,
        patch_id: str,
        patch_hash: str,
        review_id: str,
        review_hash: str,
        critic_id: str,
        critic_hash: str,
        probe_ids: tuple[str, ...] | list[str],
        probe_hashes: tuple[str, ...] | list[str],
        root_claim_snapshot_hash: str,
        source_map_hash: str,
        status: str,
        scope_validation_passed: bool,
        truth_boundary_intact: bool,
        invalidated_evidence_refs: tuple[str, ...] | list[str],
        reason: str,
        authorized_by: str,
        created_at: str,
    ) -> "PatchAuthorization":
        try:
            status_value = PatchAuthorizationStatus(status).value
        except ValueError as exc:
            raise ProjectError(f"Unsupported PatchAuthorization status: {status}") from exc
        if not isinstance(scope_validation_passed, bool) or not isinstance(
            truth_boundary_intact, bool
        ):
            raise ProjectError("PatchAuthorization gates must be boolean")
        ids = string_tuple(probe_ids, "PatchAuthorization.probe_ids")
        hashes = string_tuple(probe_hashes, "PatchAuthorization.probe_hashes")
        if len(ids) != len(hashes):
            raise ProjectError("PatchAuthorization probe ids/hashes mismatch")
        identity = {
            "patch_id": require_id(patch_id, "PatchAuthorization.patch_id"),
            "patch_hash": require_hash(patch_hash, "PatchAuthorization.patch_hash"),
            "review_id": require_id(review_id, "PatchAuthorization.review_id"),
            "review_hash": require_hash(review_hash, "PatchAuthorization.review_hash"),
            "critic_id": require_id(critic_id, "PatchAuthorization.critic_id"),
            "critic_hash": require_hash(critic_hash, "PatchAuthorization.critic_hash"),
            "probe_ids": list(ids),
            "probe_hashes": list(hashes),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash,
                "PatchAuthorization.root_claim_snapshot_hash",
            ),
            "source_map_hash": require_hash(source_map_hash, "PatchAuthorization.source_map_hash"),
            "status": status_value,
            "scope_validation_passed": scope_validation_passed,
            "truth_boundary_intact": truth_boundary_intact,
            "invalidated_evidence_refs": list(
                string_tuple(
                    invalidated_evidence_refs,
                    "PatchAuthorization.invalidated_evidence_refs",
                )
            ),
            "reason": require_text(reason, "PatchAuthorization.reason"),
            "authorized_by": require_text(authorized_by, "PatchAuthorization.authorized_by"),
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="PATCH_AUTHORIZATION",
            authorization_id=content_id(
                "authorization", "patch_authorization_id", stable_value(identity)
            ),
            patch_id=identity["patch_id"],
            patch_hash=identity["patch_hash"],
            review_id=identity["review_id"],
            review_hash=identity["review_hash"],
            critic_id=identity["critic_id"],
            critic_hash=identity["critic_hash"],
            probe_ids=ids,
            probe_hashes=hashes,
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            source_map_hash=identity["source_map_hash"],
            status=status_value,
            scope_validation_passed=scope_validation_passed,
            truth_boundary_intact=truth_boundary_intact,
            invalidated_evidence_refs=tuple(identity["invalidated_evidence_refs"]),
            reason=identity["reason"],
            authorized_by=identity["authorized_by"],
            created_at=require_text(created_at, "PatchAuthorization.created_at"),
            authorization_hash=domain_hash("patch_authorization", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PatchAuthorization":
        fields = {
            "schema_version",
            "object_type",
            "authorization_id",
            "patch_id",
            "patch_hash",
            "review_id",
            "review_hash",
            "critic_id",
            "critic_hash",
            "probe_ids",
            "probe_hashes",
            "root_claim_snapshot_hash",
            "source_map_hash",
            "status",
            "scope_validation_passed",
            "truth_boundary_intact",
            "invalidated_evidence_refs",
            "reason",
            "authorized_by",
            "created_at",
            "authorization_hash",
        }
        strict_fields(value, fields, "PatchAuthorization")
        validate_envelope(value, object_type="PATCH_AUTHORIZATION", name="PatchAuthorization")
        captured = cls.capture(
            patch_id=value["patch_id"],
            patch_hash=value["patch_hash"],
            review_id=value["review_id"],
            review_hash=value["review_hash"],
            critic_id=value["critic_id"],
            critic_hash=value["critic_hash"],
            probe_ids=value["probe_ids"],
            probe_hashes=value["probe_hashes"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            source_map_hash=value["source_map_hash"],
            status=value["status"],
            scope_validation_passed=value["scope_validation_passed"],
            truth_boundary_intact=value["truth_boundary_intact"],
            invalidated_evidence_refs=value["invalidated_evidence_refs"],
            reason=value["reason"],
            authorized_by=value["authorized_by"],
            created_at=value["created_at"],
        )
        if captured.authorization_id != value.get(
            "authorization_id"
        ) or captured.authorization_hash != value.get("authorization_hash"):
            raise ProjectError("PatchAuthorization identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)
