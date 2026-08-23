"""Immutable architecture-review commits for the Research Plane."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .governance import ArchitectureReviewTrigger
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


class ArchitectureReviewVerdict(str, Enum):
    KEEP_ARCHITECTURE = "KEEP_ARCHITECTURE"
    LOCAL_ADJUSTMENT = "LOCAL_ADJUSTMENT"
    STRUCTURAL_PROBE_REQUIRED = "STRUCTURAL_PROBE_REQUIRED"
    DESTRUCTIVE_PATCH_PROPOSED = "DESTRUCTIVE_PATCH_PROPOSED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class ArchitectureDimension(str, Enum):
    PARTITION_HEALTH = "PARTITION_HEALTH"
    PARAMETERIZATION_HEALTH = "PARAMETERIZATION_HEALTH"
    ROOT_OBSTRUCTION_MOVEMENT = "ROOT_OBSTRUCTION_MOVEMENT"
    OBLIGATION_DISTRIBUTION = "OBLIGATION_DISTRIBUTION"
    ROUTE_FAILURE_CONCENTRATION = "ROUTE_FAILURE_CONCENTRATION"
    STRUCTURAL_EFFECT_DENSITY = "STRUCTURAL_EFFECT_DENSITY"
    TACTICAL_STRUCTURAL_PROGRESS_RATIO = "TACTICAL_STRUCTURAL_PROGRESS_RATIO"
    TERMINATION_MECHANISM_VISIBILITY = "TERMINATION_MECHANISM_VISIBILITY"
    DEPENDENCY_ARCHITECTURE = "DEPENDENCY_ARCHITECTURE"
    SCOPE_COVERAGE = "SCOPE_COVERAGE"
    STALE_ASSUMPTIONS_AUTHORITY = "STALE_ASSUMPTIONS_AUTHORITY"
    CANDIDATE_REPAIR_LOOP_DOMINANCE = "CANDIDATE_REPAIR_LOOP_DOMINANCE"


class DimensionStatus(str, Enum):
    HEALTHY = "HEALTHY"
    CONCERN = "CONCERN"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class GovernanceActor:
    role: str
    actor_id: str
    provider: str
    model: str
    context_hash: str | None
    fresh_context: bool

    @classmethod
    def capture(
        cls,
        *,
        role: str,
        actor_id: str,
        provider: str = "",
        model: str = "",
        context_hash: str | None = None,
        fresh_context: bool = True,
    ) -> "GovernanceActor":
        if not isinstance(fresh_context, bool):
            raise ProjectError("GovernanceActor.fresh_context must be boolean")
        return cls(
            role=require_text(role, "GovernanceActor.role"),
            actor_id=require_id(actor_id, "GovernanceActor.actor_id"),
            provider=require_optional_text(provider, "GovernanceActor.provider"),
            model=require_optional_text(model, "GovernanceActor.model"),
            context_hash=(
                require_hash(context_hash, "GovernanceActor.context_hash")
                if context_hash is not None
                else None
            ),
            fresh_context=fresh_context,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GovernanceActor":
        strict_fields(
            value,
            {"role", "actor_id", "provider", "model", "context_hash", "fresh_context"},
            "GovernanceActor",
        )
        return cls.capture(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ArchitectureDimensionFinding:
    dimension: str
    status: str
    summary: str
    evidence_refs: tuple[str, ...]

    @classmethod
    def capture(
        cls,
        *,
        dimension: str,
        status: str,
        summary: str,
        evidence_refs: tuple[str, ...] | list[str] = (),
    ) -> "ArchitectureDimensionFinding":
        try:
            dimension_value = ArchitectureDimension(dimension).value
            status_value = DimensionStatus(status).value
        except ValueError as exc:
            raise ProjectError("Unsupported ArchitectureReview dimension/status") from exc
        return cls(
            dimension=dimension_value,
            status=status_value,
            summary=require_text(summary, "ArchitectureDimensionFinding.summary"),
            evidence_refs=string_tuple(evidence_refs, "ArchitectureDimensionFinding.evidence_refs"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArchitectureDimensionFinding":
        strict_fields(
            value,
            {"dimension", "status", "summary", "evidence_refs"},
            "ArchitectureDimensionFinding",
        )
        return cls.capture(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ArchitectureReview:
    schema_version: int
    object_type: str
    review_id: str
    root_claim_snapshot_hash: str
    research_map_id: str
    research_map_version: int
    research_map_hash: str
    trigger_reasons: tuple[str, ...]
    reviewed_partition: str
    reviewed_parameterization: str
    reviewed_obstruction_model: str
    reviewed_dependency_architecture: str
    reviewed_termination_mechanisms: str
    open_obligation_ids: tuple[str, ...]
    blocked_obligation_ids: tuple[str, ...]
    route_failure_refs: tuple[str, ...]
    structural_effect_refs: tuple[str, ...]
    dimension_findings: tuple[ArchitectureDimensionFinding, ...]
    proposed_actions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    verdict: str
    author: GovernanceActor
    created_at: str
    committed_at: str
    review_hash: str

    @classmethod
    def capture(
        cls,
        *,
        root_claim_snapshot_hash: str,
        research_map_id: str,
        research_map_version: int,
        research_map_hash: str,
        trigger_reasons: tuple[str, ...] | list[str],
        reviewed_partition: str,
        reviewed_parameterization: str,
        reviewed_obstruction_model: str,
        reviewed_dependency_architecture: str,
        reviewed_termination_mechanisms: str,
        open_obligation_ids: tuple[str, ...] | list[str],
        blocked_obligation_ids: tuple[str, ...] | list[str],
        route_failure_refs: tuple[str, ...] | list[str],
        structural_effect_refs: tuple[str, ...] | list[str],
        dimension_findings: tuple[ArchitectureDimensionFinding, ...]
        | list[ArchitectureDimensionFinding],
        proposed_actions: tuple[str, ...] | list[str],
        evidence_refs: tuple[str, ...] | list[str],
        verdict: str,
        author: GovernanceActor,
        created_at: str,
        committed_at: str,
    ) -> "ArchitectureReview":
        if (
            not isinstance(research_map_version, int)
            or isinstance(research_map_version, bool)
            or research_map_version < 1
        ):
            raise ProjectError("ArchitectureReview.research_map_version must be positive")
        if not isinstance(author, GovernanceActor):
            raise ProjectError("ArchitectureReview.author must be GovernanceActor")
        try:
            verdict_value = ArchitectureReviewVerdict(verdict).value
        except ValueError as exc:
            raise ProjectError(f"Unsupported ArchitectureReview verdict: {verdict}") from exc
        triggers = string_tuple(
            trigger_reasons, "ArchitectureReview.trigger_reasons", allow_empty=False
        )
        unknown = set(triggers) - {item.value for item in ArchitectureReviewTrigger}
        if unknown:
            raise ProjectError(f"Unsupported ArchitectureReview trigger: {sorted(unknown)}")
        findings = tuple(dimension_findings)
        if not all(isinstance(item, ArchitectureDimensionFinding) for item in findings):
            raise ProjectError("ArchitectureReview.dimension_findings must be typed")
        dimensions = [item.dimension for item in findings]
        required_dimensions = {item.value for item in ArchitectureDimension}
        if set(dimensions) != required_dimensions or len(dimensions) != len(required_dimensions):
            raise ProjectError(
                "ArchitectureReview requires exactly one finding for all 12 dimensions"
            )
        findings = tuple(sorted(findings, key=lambda item: item.dimension))
        identity = {
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "ArchitectureReview.root_claim_snapshot_hash"
            ),
            "research_map_id": require_id(research_map_id, "ArchitectureReview.research_map_id"),
            "research_map_version": research_map_version,
            "research_map_hash": require_hash(
                research_map_hash, "ArchitectureReview.research_map_hash"
            ),
            "trigger_reasons": list(triggers),
            "reviewed_partition": require_text(
                reviewed_partition, "ArchitectureReview.reviewed_partition"
            ),
            "reviewed_parameterization": require_text(
                reviewed_parameterization, "ArchitectureReview.reviewed_parameterization"
            ),
            "reviewed_obstruction_model": require_text(
                reviewed_obstruction_model,
                "ArchitectureReview.reviewed_obstruction_model",
            ),
            "reviewed_dependency_architecture": require_text(
                reviewed_dependency_architecture,
                "ArchitectureReview.reviewed_dependency_architecture",
            ),
            "reviewed_termination_mechanisms": require_text(
                reviewed_termination_mechanisms,
                "ArchitectureReview.reviewed_termination_mechanisms",
            ),
            "open_obligation_ids": list(
                string_tuple(open_obligation_ids, "ArchitectureReview.open_obligation_ids")
            ),
            "blocked_obligation_ids": list(
                string_tuple(blocked_obligation_ids, "ArchitectureReview.blocked_obligation_ids")
            ),
            "route_failure_refs": list(
                string_tuple(route_failure_refs, "ArchitectureReview.route_failure_refs")
            ),
            "structural_effect_refs": list(
                string_tuple(structural_effect_refs, "ArchitectureReview.structural_effect_refs")
            ),
            "dimension_findings": [item.to_dict() for item in findings],
            "proposed_actions": list(
                string_tuple(
                    proposed_actions, "ArchitectureReview.proposed_actions", allow_empty=False
                )
            ),
            "evidence_refs": list(string_tuple(evidence_refs, "ArchitectureReview.evidence_refs")),
            "verdict": verdict_value,
            "author": author.to_dict(),
        }
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="ARCHITECTURE_REVIEW",
            review_id=content_id("review", "architecture_review_id", stable_value(identity)),
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            research_map_id=identity["research_map_id"],
            research_map_version=research_map_version,
            research_map_hash=identity["research_map_hash"],
            trigger_reasons=triggers,
            reviewed_partition=identity["reviewed_partition"],
            reviewed_parameterization=identity["reviewed_parameterization"],
            reviewed_obstruction_model=identity["reviewed_obstruction_model"],
            reviewed_dependency_architecture=identity["reviewed_dependency_architecture"],
            reviewed_termination_mechanisms=identity["reviewed_termination_mechanisms"],
            open_obligation_ids=tuple(identity["open_obligation_ids"]),
            blocked_obligation_ids=tuple(identity["blocked_obligation_ids"]),
            route_failure_refs=tuple(identity["route_failure_refs"]),
            structural_effect_refs=tuple(identity["structural_effect_refs"]),
            dimension_findings=findings,
            proposed_actions=tuple(identity["proposed_actions"]),
            evidence_refs=tuple(identity["evidence_refs"]),
            verdict=verdict_value,
            author=author,
            created_at=require_text(created_at, "ArchitectureReview.created_at"),
            committed_at=require_text(committed_at, "ArchitectureReview.committed_at"),
            review_hash=domain_hash("architecture_review", stable_value(identity)),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArchitectureReview":
        fields = {
            "schema_version",
            "object_type",
            "review_id",
            "root_claim_snapshot_hash",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "trigger_reasons",
            "reviewed_partition",
            "reviewed_parameterization",
            "reviewed_obstruction_model",
            "reviewed_dependency_architecture",
            "reviewed_termination_mechanisms",
            "open_obligation_ids",
            "blocked_obligation_ids",
            "route_failure_refs",
            "structural_effect_refs",
            "dimension_findings",
            "proposed_actions",
            "evidence_refs",
            "verdict",
            "author",
            "created_at",
            "committed_at",
            "review_hash",
        }
        strict_fields(value, fields, "ArchitectureReview")
        validate_envelope(value, object_type="ARCHITECTURE_REVIEW", name="ArchitectureReview")
        captured = cls.capture(
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            research_map_id=value["research_map_id"],
            research_map_version=value["research_map_version"],
            research_map_hash=value["research_map_hash"],
            trigger_reasons=value["trigger_reasons"],
            reviewed_partition=value["reviewed_partition"],
            reviewed_parameterization=value["reviewed_parameterization"],
            reviewed_obstruction_model=value["reviewed_obstruction_model"],
            reviewed_dependency_architecture=value["reviewed_dependency_architecture"],
            reviewed_termination_mechanisms=value["reviewed_termination_mechanisms"],
            open_obligation_ids=value["open_obligation_ids"],
            blocked_obligation_ids=value["blocked_obligation_ids"],
            route_failure_refs=value["route_failure_refs"],
            structural_effect_refs=value["structural_effect_refs"],
            dimension_findings=[
                ArchitectureDimensionFinding.from_dict(item) for item in value["dimension_findings"]
            ],
            proposed_actions=value["proposed_actions"],
            evidence_refs=value["evidence_refs"],
            verdict=value["verdict"],
            author=GovernanceActor.from_dict(value["author"]),
            created_at=value["created_at"],
            committed_at=value["committed_at"],
        )
        if captured.review_id != value.get("review_id") or captured.review_hash != value.get(
            "review_hash"
        ):
            raise ProjectError("ArchitectureReview identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["dimension_findings"] = [item.to_dict() for item in self.dimension_findings]
        value["author"] = self.author.to_dict()
        return value
