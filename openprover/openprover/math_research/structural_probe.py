"""Bounded, non-authoritative tests of proposed architecture mechanisms."""

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


class StructuralProbeResult(str, Enum):
    SUPPORTS_PATCH = "SUPPORTS_PATCH"
    REJECTS_PATCH = "REJECTS_PATCH"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class ProbeBudget:
    max_sessions: int
    max_workers: int
    max_provider_calls: int
    wall_clock_seconds: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ProjectError(f"ProbeBudget.{name} must be a positive integer")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProbeBudget":
        strict_fields(value, set(cls.__dataclass_fields__), "ProbeBudget")
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class StructuralProbePlan:
    schema_version: int
    object_type: str
    probe_id: str
    review_id: str
    review_hash: str
    root_claim_snapshot_hash: str
    research_map_id: str
    source_map_version: int
    source_map_hash: str
    proposed_mechanism: str
    proposed_partition_change: str
    proposed_parameterization: str
    target_obstruction: str
    bounded_scope: tuple[str, ...]
    budget: ProbeBudget
    success_criteria: tuple[str, ...]
    failure_criteria: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    created_at: str
    created_by: str
    plan_hash: str

    @classmethod
    def capture(
        cls,
        *,
        review_id: str,
        review_hash: str,
        root_claim_snapshot_hash: str,
        research_map_id: str,
        source_map_version: int,
        source_map_hash: str,
        proposed_mechanism: str,
        proposed_partition_change: str = "",
        proposed_parameterization: str = "",
        target_obstruction: str,
        bounded_scope: tuple[str, ...] | list[str],
        budget: ProbeBudget,
        success_criteria: tuple[str, ...] | list[str],
        failure_criteria: tuple[str, ...] | list[str],
        evidence_refs: tuple[str, ...] | list[str] = (),
        created_at: str,
        created_by: str,
    ) -> "StructuralProbePlan":
        if (
            not isinstance(source_map_version, int)
            or isinstance(source_map_version, bool)
            or source_map_version < 1
        ):
            raise ProjectError("StructuralProbePlan.source_map_version must be positive")
        if not isinstance(budget, ProbeBudget):
            raise ProjectError("StructuralProbePlan.budget must be ProbeBudget")
        identity = {
            "review_id": require_id(review_id, "StructuralProbePlan.review_id"),
            "review_hash": require_hash(review_hash, "StructuralProbePlan.review_hash"),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash,
                "StructuralProbePlan.root_claim_snapshot_hash",
            ),
            "research_map_id": require_id(research_map_id, "StructuralProbePlan.research_map_id"),
            "source_map_version": source_map_version,
            "source_map_hash": require_hash(source_map_hash, "StructuralProbePlan.source_map_hash"),
            "proposed_mechanism": require_text(
                proposed_mechanism, "StructuralProbePlan.proposed_mechanism"
            ),
            "proposed_partition_change": require_optional_text(
                proposed_partition_change,
                "StructuralProbePlan.proposed_partition_change",
            ),
            "proposed_parameterization": require_optional_text(
                proposed_parameterization,
                "StructuralProbePlan.proposed_parameterization",
            ),
            "target_obstruction": require_text(
                target_obstruction, "StructuralProbePlan.target_obstruction"
            ),
            "bounded_scope": list(
                string_tuple(bounded_scope, "StructuralProbePlan.bounded_scope", allow_empty=False)
            ),
            "budget": budget.to_dict(),
            "success_criteria": list(
                string_tuple(
                    success_criteria,
                    "StructuralProbePlan.success_criteria",
                    allow_empty=False,
                )
            ),
            "failure_criteria": list(
                string_tuple(
                    failure_criteria,
                    "StructuralProbePlan.failure_criteria",
                    allow_empty=False,
                )
            ),
            "evidence_refs": list(string_tuple(evidence_refs, "StructuralProbePlan.evidence_refs")),
            "created_by": require_text(created_by, "StructuralProbePlan.created_by"),
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="STRUCTURAL_PROBE_PLAN",
            probe_id=content_id("probe", "structural_probe_id", stable_value(identity)),
            review_id=identity["review_id"],
            review_hash=identity["review_hash"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            research_map_id=identity["research_map_id"],
            source_map_version=source_map_version,
            source_map_hash=identity["source_map_hash"],
            proposed_mechanism=identity["proposed_mechanism"],
            proposed_partition_change=identity["proposed_partition_change"],
            proposed_parameterization=identity["proposed_parameterization"],
            target_obstruction=identity["target_obstruction"],
            bounded_scope=tuple(identity["bounded_scope"]),
            budget=budget,
            success_criteria=tuple(identity["success_criteria"]),
            failure_criteria=tuple(identity["failure_criteria"]),
            evidence_refs=tuple(identity["evidence_refs"]),
            created_at=require_text(created_at, "StructuralProbePlan.created_at"),
            created_by=identity["created_by"],
            plan_hash=domain_hash("structural_probe_plan", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuralProbePlan":
        fields = {
            "schema_version",
            "object_type",
            "probe_id",
            "review_id",
            "review_hash",
            "root_claim_snapshot_hash",
            "research_map_id",
            "source_map_version",
            "source_map_hash",
            "proposed_mechanism",
            "proposed_partition_change",
            "proposed_parameterization",
            "target_obstruction",
            "bounded_scope",
            "budget",
            "success_criteria",
            "failure_criteria",
            "evidence_refs",
            "created_at",
            "created_by",
            "plan_hash",
        }
        strict_fields(value, fields, "StructuralProbePlan")
        validate_envelope(value, object_type="STRUCTURAL_PROBE_PLAN", name="StructuralProbePlan")
        captured = cls.capture(
            review_id=value["review_id"],
            review_hash=value["review_hash"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            research_map_id=value["research_map_id"],
            source_map_version=value["source_map_version"],
            source_map_hash=value["source_map_hash"],
            proposed_mechanism=value["proposed_mechanism"],
            proposed_partition_change=value["proposed_partition_change"],
            proposed_parameterization=value["proposed_parameterization"],
            target_obstruction=value["target_obstruction"],
            bounded_scope=value["bounded_scope"],
            budget=ProbeBudget.from_dict(value["budget"]),
            success_criteria=value["success_criteria"],
            failure_criteria=value["failure_criteria"],
            evidence_refs=value["evidence_refs"],
            created_at=value["created_at"],
            created_by=value["created_by"],
        )
        if captured.probe_id != value.get("probe_id") or captured.plan_hash != value.get(
            "plan_hash"
        ):
            raise ProjectError("StructuralProbePlan identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["budget"] = self.budget.to_dict()
        return value


@dataclass(frozen=True, slots=True)
class StructuralProbe:
    schema_version: int
    object_type: str
    probe_id: str
    plan_hash: str
    root_claim_snapshot_hash: str
    source_map_hash: str
    result: str
    evidence_refs: tuple[str, ...]
    result_basis: str
    closed_at: str
    closed_by: str
    probe_hash: str

    @classmethod
    def capture(
        cls,
        *,
        plan: StructuralProbePlan,
        result: str,
        evidence_refs: tuple[str, ...] | list[str],
        result_basis: str,
        closed_at: str,
        closed_by: str,
    ) -> "StructuralProbe":
        if not isinstance(plan, StructuralProbePlan):
            raise ProjectError("StructuralProbe requires StructuralProbePlan")
        try:
            result_value = StructuralProbeResult(result).value
        except ValueError as exc:
            raise ProjectError(f"Unsupported StructuralProbe result: {result}") from exc
        evidence = string_tuple(evidence_refs, "StructuralProbe.evidence_refs", allow_empty=False)
        identity = {
            "probe_id": plan.probe_id,
            "plan_hash": plan.plan_hash,
            "root_claim_snapshot_hash": plan.root_claim_snapshot_hash,
            "source_map_hash": plan.source_map_hash,
            "result": result_value,
            "evidence_refs": list(evidence),
            "result_basis": require_text(result_basis, "StructuralProbe.result_basis"),
            "closed_by": require_text(closed_by, "StructuralProbe.closed_by"),
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="STRUCTURAL_PROBE",
            probe_id=plan.probe_id,
            plan_hash=plan.plan_hash,
            root_claim_snapshot_hash=plan.root_claim_snapshot_hash,
            source_map_hash=plan.source_map_hash,
            result=result_value,
            evidence_refs=evidence,
            result_basis=identity["result_basis"],
            closed_at=require_text(closed_at, "StructuralProbe.closed_at"),
            closed_by=identity["closed_by"],
            probe_hash=domain_hash("structural_probe", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], plan: StructuralProbePlan) -> "StructuralProbe":
        fields = {
            "schema_version",
            "object_type",
            "probe_id",
            "plan_hash",
            "root_claim_snapshot_hash",
            "source_map_hash",
            "result",
            "evidence_refs",
            "result_basis",
            "closed_at",
            "closed_by",
            "probe_hash",
        }
        strict_fields(value, fields, "StructuralProbe")
        validate_envelope(value, object_type="STRUCTURAL_PROBE", name="StructuralProbe")
        captured = cls.capture(
            plan=plan,
            result=value["result"],
            evidence_refs=value["evidence_refs"],
            result_basis=value["result_basis"],
            closed_at=value["closed_at"],
            closed_by=value["closed_by"],
        )
        if captured.probe_hash != value.get("probe_hash"):
            raise ProjectError("StructuralProbe hash mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)
