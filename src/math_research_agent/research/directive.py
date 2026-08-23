"""Immutable Directive projection and TacticalSession execution binding."""

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
    strict_fields,
    string_tuple,
    validate_envelope,
)
from .truth_identity import domain_hash


class TacticalExecutionStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CRASHED = "CRASHED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class BudgetProfile:
    wall_clock_seconds: int
    max_workers: int
    max_provider_calls: int
    reasoning_tier: str

    @classmethod
    def capture(
        cls,
        *,
        wall_clock_seconds: int,
        max_workers: int,
        max_provider_calls: int,
        reasoning_tier: str,
    ) -> "BudgetProfile":
        for field, value in (
            ("wall_clock_seconds", wall_clock_seconds),
            ("max_workers", max_workers),
            ("max_provider_calls", max_provider_calls),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ProjectError(f"BudgetProfile.{field} must be a positive integer")
        return cls(
            wall_clock_seconds=wall_clock_seconds,
            max_workers=max_workers,
            max_provider_calls=max_provider_calls,
            reasoning_tier=require_text(reasoning_tier, "BudgetProfile.reasoning_tier"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BudgetProfile":
        strict_fields(
            value,
            {"wall_clock_seconds", "max_workers", "max_provider_calls", "reasoning_tier"},
            "BudgetProfile",
        )
        return cls.capture(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class Directive:
    schema_version: int
    object_type: str
    directive_id: str
    research_map_id: str
    research_map_version: int
    research_map_hash: str
    obligation_id: str
    obligation_hash: str
    root_claim_snapshot_hash: str
    tactical_goal: str
    allowed_scope: tuple[str, ...]
    prohibited_routes: tuple[str, ...]
    relevant_evidence_refs: tuple[str, ...]
    failed_route_refs: tuple[str, ...]
    requested_worker_roles: tuple[str, ...]
    budget_profile: BudgetProfile
    created_at: str
    created_by: str
    directive_hash: str

    @classmethod
    def capture(
        cls,
        *,
        research_map_id: str,
        research_map_version: int,
        research_map_hash: str,
        obligation_id: str,
        obligation_hash: str,
        root_claim_snapshot_hash: str,
        tactical_goal: str,
        allowed_scope: tuple[str, ...] | list[str],
        prohibited_routes: tuple[str, ...] | list[str] = (),
        relevant_evidence_refs: tuple[str, ...] | list[str] = (),
        failed_route_refs: tuple[str, ...] | list[str] = (),
        requested_worker_roles: tuple[str, ...] | list[str] = ("constructive",),
        budget_profile: BudgetProfile,
        created_at: str,
        created_by: str,
    ) -> "Directive":
        if (
            not isinstance(research_map_version, int)
            or isinstance(research_map_version, bool)
            or research_map_version < 1
        ):
            raise ProjectError("Directive.research_map_version must be positive")
        if not isinstance(budget_profile, BudgetProfile):
            raise ProjectError("Directive.budget_profile must be typed")
        allowed = string_tuple(allowed_scope, "Directive.allowed_scope", allow_empty=False)
        if not allowed:
            raise ProjectError("Directive requires non-empty allowed_scope")
        prohibited = string_tuple(prohibited_routes, "Directive.prohibited_routes")
        evidence = string_tuple(relevant_evidence_refs, "Directive.relevant_evidence_refs")
        failures = string_tuple(failed_route_refs, "Directive.failed_route_refs")
        roles = string_tuple(
            requested_worker_roles, "Directive.requested_worker_roles", allow_empty=False
        )
        if not roles:
            raise ProjectError("Directive requires at least one requested worker role")
        identity = {
            "research_map_id": require_id(research_map_id, "Directive.research_map_id"),
            "research_map_version": research_map_version,
            "research_map_hash": require_hash(research_map_hash, "Directive.research_map_hash"),
            "obligation_id": require_id(obligation_id, "Directive.obligation_id"),
            "obligation_hash": require_hash(obligation_hash, "Directive.obligation_hash"),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "Directive.root_claim_snapshot_hash"
            ),
            "tactical_goal": require_text(tactical_goal, "Directive.tactical_goal"),
            "allowed_scope": list(allowed),
            "prohibited_routes": list(prohibited),
            "relevant_evidence_refs": list(evidence),
            "failed_route_refs": list(failures),
            "requested_worker_roles": list(roles),
            "budget_profile": budget_profile.to_dict(),
        }
        directive_hash = domain_hash("directive", identity)
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="DIRECTIVE",
            directive_id=content_id("directive", "directive_id", identity),
            research_map_id=identity["research_map_id"],
            research_map_version=research_map_version,
            research_map_hash=identity["research_map_hash"],
            obligation_id=identity["obligation_id"],
            obligation_hash=identity["obligation_hash"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            tactical_goal=identity["tactical_goal"],
            allowed_scope=allowed,
            prohibited_routes=prohibited,
            relevant_evidence_refs=evidence,
            failed_route_refs=failures,
            requested_worker_roles=roles,
            budget_profile=budget_profile,
            created_at=require_text(created_at, "Directive.created_at"),
            created_by=require_text(created_by, "Directive.created_by"),
            directive_hash=directive_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Directive":
        fields = {
            "schema_version",
            "object_type",
            "directive_id",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "obligation_id",
            "obligation_hash",
            "root_claim_snapshot_hash",
            "tactical_goal",
            "allowed_scope",
            "prohibited_routes",
            "relevant_evidence_refs",
            "failed_route_refs",
            "requested_worker_roles",
            "budget_profile",
            "created_at",
            "created_by",
            "directive_hash",
        }
        strict_fields(value, fields, "Directive")
        validate_envelope(value, object_type="DIRECTIVE", name="Directive")
        if not isinstance(value.get("budget_profile"), Mapping):
            raise ProjectError("Directive.budget_profile must be an object")
        captured = cls.capture(
            research_map_id=value["research_map_id"],
            research_map_version=value["research_map_version"],
            research_map_hash=value["research_map_hash"],
            obligation_id=value["obligation_id"],
            obligation_hash=value["obligation_hash"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            tactical_goal=value["tactical_goal"],
            allowed_scope=value["allowed_scope"],
            prohibited_routes=value["prohibited_routes"],
            relevant_evidence_refs=value["relevant_evidence_refs"],
            failed_route_refs=value["failed_route_refs"],
            requested_worker_roles=value["requested_worker_roles"],
            budget_profile=BudgetProfile.from_dict(value["budget_profile"]),
            created_at=value["created_at"],
            created_by=value["created_by"],
        )
        if captured.directive_id != value.get(
            "directive_id"
        ) or captured.directive_hash != value.get("directive_hash"):
            raise ProjectError("Directive identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["budget_profile"] = self.budget_profile.to_dict()
        return value

    def tactical_context(self) -> dict[str, Any]:
        """Planner-visible projection; deliberately excludes the full ResearchMap."""

        return {
            "schema_version": 1,
            "object_type": "TACTICAL_CONTEXT",
            "source_directive_id": self.directive_id,
            "source_directive_hash": self.directive_hash,
            "obligation_id": self.obligation_id,
            "obligation_hash": self.obligation_hash,
            "root_claim_snapshot_hash": self.root_claim_snapshot_hash,
            "tactical_goal": self.tactical_goal,
            "allowed_scope": list(self.allowed_scope),
            "prohibited_routes": list(self.prohibited_routes),
            "relevant_evidence_refs": list(self.relevant_evidence_refs),
            "failed_route_refs": list(self.failed_route_refs),
            "requested_worker_roles": list(self.requested_worker_roles),
            "budget_profile": self.budget_profile.to_dict(),
        }

    def worker_context(self, *, task_id: str, task_goal: str, evidence_refs=()) -> dict[str, Any]:
        requested = string_tuple(evidence_refs, "WorkerContext.evidence_refs")
        outside = set(requested) - set(self.relevant_evidence_refs)
        if outside:
            raise ProjectError(f"WorkerContext evidence exceeds Directive scope: {sorted(outside)}")
        return {
            "schema_version": 1,
            "object_type": "WORKER_CONTEXT",
            "source_directive_id": self.directive_id,
            "obligation_id": self.obligation_id,
            "task_id": require_id(task_id, "WorkerContext.task_id"),
            "task_goal": require_text(task_goal, "WorkerContext.task_goal"),
            "allowed_scope": list(self.allowed_scope),
            "prohibited_routes": list(self.prohibited_routes),
            "evidence_refs": list(requested),
        }


@dataclass(frozen=True, slots=True)
class TacticalSession:
    schema_version: int
    object_type: str
    tactical_session_id: str
    directive_id: str
    directive_hash: str
    obligation_id: str
    obligation_hash: str
    research_map_id: str
    research_map_version: int
    research_map_hash: str
    root_claim_snapshot_hash: str
    execution_run_id: str
    parent_execution_run_id: str | None
    execution_status: str
    created_at: str
    session_hash: str

    @classmethod
    def capture(
        cls,
        *,
        directive: Directive,
        execution_run_id: str,
        created_at: str,
        execution_status: str = TacticalExecutionStatus.CREATED.value,
        parent_execution_run_id: str | None = None,
    ) -> "TacticalSession":
        if execution_status not in {item.value for item in TacticalExecutionStatus}:
            raise ProjectError(f"Unsupported TacticalSession execution status: {execution_status}")
        parent = (
            require_id(parent_execution_run_id, "TacticalSession.parent_execution_run_id")
            if parent_execution_run_id is not None
            else None
        )
        identity = {
            "directive_id": directive.directive_id,
            "directive_hash": directive.directive_hash,
            "obligation_id": directive.obligation_id,
            "obligation_hash": directive.obligation_hash,
            "research_map_id": directive.research_map_id,
            "research_map_version": directive.research_map_version,
            "research_map_hash": directive.research_map_hash,
            "root_claim_snapshot_hash": directive.root_claim_snapshot_hash,
            "execution_run_id": require_id(execution_run_id, "TacticalSession.execution_run_id"),
            "parent_execution_run_id": parent,
            "execution_status": execution_status,
        }
        session_hash = domain_hash("tactical_session_binding", identity)
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="TACTICAL_SESSION",
            tactical_session_id=content_id("session", "tactical_session_id", identity),
            **identity,
            created_at=require_text(created_at, "TacticalSession.created_at"),
            session_hash=session_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], directive: Directive) -> "TacticalSession":
        fields = {
            "schema_version",
            "object_type",
            "tactical_session_id",
            "directive_id",
            "directive_hash",
            "obligation_id",
            "obligation_hash",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "root_claim_snapshot_hash",
            "execution_run_id",
            "parent_execution_run_id",
            "execution_status",
            "created_at",
            "session_hash",
        }
        strict_fields(value, fields, "TacticalSession")
        validate_envelope(value, object_type="TACTICAL_SESSION", name="TacticalSession")
        captured = cls.capture(
            directive=directive,
            execution_run_id=value["execution_run_id"],
            parent_execution_run_id=value["parent_execution_run_id"],
            execution_status=value["execution_status"],
            created_at=value["created_at"],
        )
        for field in (
            "directive_id",
            "directive_hash",
            "obligation_id",
            "obligation_hash",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "root_claim_snapshot_hash",
            "tactical_session_id",
            "session_hash",
        ):
            if getattr(captured, field) != value.get(field):
                raise ProjectError(f"TacticalSession {field} mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)
