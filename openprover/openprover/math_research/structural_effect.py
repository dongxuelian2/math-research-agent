"""Evidence-bound activity, tactical-progress, and structural-progress effects."""

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
    require_text,
    stable_value,
    strict_fields,
    string_tuple,
    validate_envelope,
)
from .truth_identity import domain_hash


class StructuralEffectLevel(str, Enum):
    ACTIVITY = "ACTIVITY"
    TACTICAL_PROGRESS = "TACTICAL_PROGRESS"
    STRUCTURAL_PROGRESS = "STRUCTURAL_PROGRESS"


class StructuralEffectValidation(str, Enum):
    VALIDATED = "VALIDATED"
    UNVALIDATED_CLAIM = "UNVALIDATED_CLAIM"


class StructuralEffectKind(str, Enum):
    WORKER_SPAWNED = "WORKER_SPAWNED"
    TOKEN_BUDGET_SPENT = "TOKEN_BUDGET_SPENT"
    ARTIFACT_RETAINED = "ARTIFACT_RETAINED"
    PROVIDER_CALL_COMPLETED = "PROVIDER_CALL_COMPLETED"
    LOCAL_LEMMA_PROVED = "LOCAL_LEMMA_PROVED"
    PARAMETER_RANGE_REDUCED = "PARAMETER_RANGE_REDUCED"
    ONE_OBLIGATION_RESOLVED = "ONE_OBLIGATION_RESOLVED"
    BOUNDED_COMPUTATION_COMPLETED = "BOUNDED_COMPUTATION_COMPLETED"
    DEPENDENCY_RESOLVED = "DEPENDENCY_RESOLVED"
    BRANCH_CLASS_CLOSED = "BRANCH_CLASS_CLOSED"
    GLOBAL_INVARIANT_FOUND = "GLOBAL_INVARIANT_FOUND"
    PARTITION_REMOVED = "PARTITION_REMOVED"
    PARAMETERIZATION_SIMPLIFIED = "PARAMETERIZATION_SIMPLIFIED"
    INFINITE_TO_FINITE_REDUCTION = "INFINITE_TO_FINITE_REDUCTION"
    TERMINATION_MECHANISM_ESTABLISHED = "TERMINATION_MECHANISM_ESTABLISHED"
    ROOT_OBSTRUCTION_CHANGED = "ROOT_OBSTRUCTION_CHANGED"
    MAJOR_DEPENDENCY_ARCHITECTURE_CHANGED = "MAJOR_DEPENDENCY_ARCHITECTURE_CHANGED"
    NEW_MECHANISM_VALIDATED = "NEW_MECHANISM_VALIDATED"


_ACTIVITY_KINDS = {
    StructuralEffectKind.WORKER_SPAWNED,
    StructuralEffectKind.TOKEN_BUDGET_SPENT,
    StructuralEffectKind.ARTIFACT_RETAINED,
    StructuralEffectKind.PROVIDER_CALL_COMPLETED,
}
_TACTICAL_KINDS = {
    StructuralEffectKind.LOCAL_LEMMA_PROVED,
    StructuralEffectKind.PARAMETER_RANGE_REDUCED,
    StructuralEffectKind.ONE_OBLIGATION_RESOLVED,
    StructuralEffectKind.BOUNDED_COMPUTATION_COMPLETED,
    StructuralEffectKind.DEPENDENCY_RESOLVED,
}
_STRUCTURAL_KINDS = {
    StructuralEffectKind.BRANCH_CLASS_CLOSED,
    StructuralEffectKind.GLOBAL_INVARIANT_FOUND,
    StructuralEffectKind.PARTITION_REMOVED,
    StructuralEffectKind.PARAMETERIZATION_SIMPLIFIED,
    StructuralEffectKind.INFINITE_TO_FINITE_REDUCTION,
    StructuralEffectKind.TERMINATION_MECHANISM_ESTABLISHED,
    StructuralEffectKind.ROOT_OBSTRUCTION_CHANGED,
    StructuralEffectKind.MAJOR_DEPENDENCY_ARCHITECTURE_CHANGED,
    StructuralEffectKind.NEW_MECHANISM_VALIDATED,
}


def classify_structural_effect(effect_kind: str) -> str:
    """Return the one deterministic progress level for an exact effect kind."""

    try:
        kind = StructuralEffectKind(effect_kind)
    except ValueError as exc:
        raise ProjectError(f"Unsupported StructuralEffect kind: {effect_kind}") from exc
    if kind in _ACTIVITY_KINDS:
        return StructuralEffectLevel.ACTIVITY.value
    if kind in _TACTICAL_KINDS:
        return StructuralEffectLevel.TACTICAL_PROGRESS.value
    if kind in _STRUCTURAL_KINDS:
        return StructuralEffectLevel.STRUCTURAL_PROGRESS.value
    raise ProjectError(f"StructuralEffect kind has no classification: {effect_kind}")


@dataclass(frozen=True, slots=True)
class StructuralEffect:
    schema_version: int
    object_type: str
    structural_effect_id: str
    root_claim_snapshot_hash: str
    research_map_id: str
    research_map_version: int
    research_map_hash: str
    obligation_refs: tuple[str, ...]
    effect_kind: str
    level: str
    evidence_refs: tuple[str, ...]
    validation_basis: str
    validation_status: str
    source_type: str
    created_at: str
    created_by: str
    structural_effect_hash: str

    @classmethod
    def capture(
        cls,
        *,
        root_claim_snapshot_hash: str,
        research_map_id: str,
        research_map_version: int,
        research_map_hash: str,
        obligation_refs: tuple[str, ...] | list[str],
        effect_kind: str,
        evidence_refs: tuple[str, ...] | list[str],
        validation_basis: str,
        validation_status: str,
        source_type: str,
        created_at: str,
        created_by: str,
    ) -> "StructuralEffect":
        if (
            not isinstance(research_map_version, int)
            or isinstance(research_map_version, bool)
            or research_map_version < 1
        ):
            raise ProjectError("StructuralEffect.research_map_version must be positive")
        try:
            status = StructuralEffectValidation(validation_status)
        except ValueError as exc:
            raise ProjectError(
                f"Unsupported StructuralEffect validation status: {validation_status}"
            ) from exc
        level = classify_structural_effect(effect_kind)
        obligations = string_tuple(
            obligation_refs, "StructuralEffect.obligation_refs", allow_empty=False
        )
        evidence = string_tuple(evidence_refs, "StructuralEffect.evidence_refs")
        basis = require_text(validation_basis, "StructuralEffect.validation_basis")
        if status is StructuralEffectValidation.VALIDATED and not evidence:
            raise ProjectError("Validated StructuralEffect requires evidence_refs")
        identity = {
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "StructuralEffect.root_claim_snapshot_hash"
            ),
            "research_map_id": require_id(research_map_id, "StructuralEffect.research_map_id"),
            "research_map_version": research_map_version,
            "research_map_hash": require_hash(
                research_map_hash, "StructuralEffect.research_map_hash"
            ),
            "obligation_refs": list(obligations),
            "effect_kind": StructuralEffectKind(effect_kind).value,
            "level": level,
            "evidence_refs": list(evidence),
            "validation_basis": basis,
            "validation_status": status.value,
            "source_type": require_text(source_type, "StructuralEffect.source_type"),
            "created_by": require_text(created_by, "StructuralEffect.created_by"),
        }
        digest = domain_hash("structural_effect", stable_value(identity))
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="STRUCTURAL_EFFECT",
            structural_effect_id=content_id(
                "effect", "structural_effect_id", stable_value(identity)
            ),
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            research_map_id=identity["research_map_id"],
            research_map_version=research_map_version,
            research_map_hash=identity["research_map_hash"],
            obligation_refs=obligations,
            effect_kind=identity["effect_kind"],
            level=level,
            evidence_refs=evidence,
            validation_basis=basis,
            validation_status=status.value,
            source_type=identity["source_type"],
            created_at=require_text(created_at, "StructuralEffect.created_at"),
            created_by=identity["created_by"],
            structural_effect_hash=digest,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuralEffect":
        fields = {
            "schema_version",
            "object_type",
            "structural_effect_id",
            "root_claim_snapshot_hash",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "obligation_refs",
            "effect_kind",
            "level",
            "evidence_refs",
            "validation_basis",
            "validation_status",
            "source_type",
            "created_at",
            "created_by",
            "structural_effect_hash",
        }
        strict_fields(value, fields, "StructuralEffect")
        validate_envelope(value, object_type="STRUCTURAL_EFFECT", name="StructuralEffect")
        captured = cls.capture(
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            research_map_id=value["research_map_id"],
            research_map_version=value["research_map_version"],
            research_map_hash=value["research_map_hash"],
            obligation_refs=value["obligation_refs"],
            effect_kind=value["effect_kind"],
            evidence_refs=value["evidence_refs"],
            validation_basis=value["validation_basis"],
            validation_status=value["validation_status"],
            source_type=value["source_type"],
            created_at=value["created_at"],
            created_by=value["created_by"],
        )
        if captured.level != value.get("level"):
            raise ProjectError("StructuralEffect level does not match deterministic kind")
        if captured.structural_effect_id != value.get(
            "structural_effect_id"
        ) or captured.structural_effect_hash != value.get("structural_effect_hash"):
            raise ProjectError("StructuralEffect identity mismatch")
        return captured

    @property
    def governance_validated(self) -> bool:
        return self.validation_status == StructuralEffectValidation.VALIDATED.value

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)
