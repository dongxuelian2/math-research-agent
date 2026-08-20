"""Dependency-aware Research Plane failed-route memory and legacy adapters."""

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


class FailureDomain(str, Enum):
    MATHEMATICAL = "MATHEMATICAL"
    DEPENDENCY = "DEPENDENCY"
    ASSUMPTION = "ASSUMPTION"
    AUTHORITY = "AUTHORITY"
    SCOPE = "SCOPE"
    EXECUTION = "EXECUTION"
    PROVIDER = "PROVIDER"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    UNKNOWN = "UNKNOWN"


class ReopenCondition(str, Enum):
    DEPENDENCY_SNAPSHOT_CHANGED = "DEPENDENCY_SNAPSHOT_CHANGED"
    ASSUMPTION_SNAPSHOT_CHANGED = "ASSUMPTION_SNAPSHOT_CHANGED"
    AUTHORITY_CONTEXT_CHANGED = "AUTHORITY_CONTEXT_CHANGED"
    NEW_VERIFIED_LEMMA = "NEW_VERIFIED_LEMMA"
    FAILURE_CONDITION_REMOVED = "FAILURE_CONDITION_REMOVED"
    HUMAN_REVALIDATION = "HUMAN_REVALIDATION"


class RouteEligibility(str, Enum):
    FAILURE_STILL_APPLIES = "FAILURE_STILL_APPLIES"
    REOPENABLE = "REOPENABLE"
    REVALIDATE = "REVALIDATE"


@dataclass(frozen=True, slots=True)
class RouteContext:
    dependency_snapshot_hash: str
    assumption_snapshot_hash: str
    authority_context_hash: str
    verified_lemma_refs: tuple[str, ...] = ()

    @classmethod
    def capture(
        cls,
        *,
        dependency_snapshot_hash: str,
        assumption_snapshot_hash: str,
        authority_context_hash: str,
        verified_lemma_refs: tuple[str, ...] | list[str] = (),
    ) -> "RouteContext":
        return cls(
            dependency_snapshot_hash=require_hash(
                dependency_snapshot_hash, "RouteContext.dependency_snapshot_hash"
            ),
            assumption_snapshot_hash=require_hash(
                assumption_snapshot_hash, "RouteContext.assumption_snapshot_hash"
            ),
            authority_context_hash=require_hash(
                authority_context_hash, "RouteContext.authority_context_hash"
            ),
            verified_lemma_refs=string_tuple(
                verified_lemma_refs, "RouteContext.verified_lemma_refs"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class RouteEligibilityDecision:
    schema_version: int
    object_type: str
    status: str
    reason: str
    changed_contexts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class RouteFailureRecord:
    schema_version: int
    object_type: str
    route_failure_id: str
    root_claim_snapshot_hash: str
    research_map_id: str
    research_map_version: int
    research_map_hash: str
    obligation_id: str
    obligation_hash: str
    route_description: str
    method_family: str
    dependency_snapshot_hash: str
    assumption_snapshot_hash: str
    authority_context_hash: str
    exact_failure_condition: str
    failure_domain: str
    evidence_refs: tuple[str, ...]
    reopen_conditions: tuple[str, ...]
    verified_lemma_refs: tuple[str, ...]
    provenance: str
    legacy_source_ref: str
    created_at: str
    created_by: str
    route_failure_hash: str

    @classmethod
    def capture(
        cls,
        *,
        root_claim_snapshot_hash: str,
        research_map_id: str,
        research_map_version: int,
        research_map_hash: str,
        obligation_id: str,
        obligation_hash: str,
        route_description: str,
        method_family: str,
        context: RouteContext,
        exact_failure_condition: str,
        failure_domain: str,
        evidence_refs: tuple[str, ...] | list[str],
        reopen_conditions: tuple[str, ...] | list[str],
        provenance: str,
        created_at: str,
        created_by: str,
        legacy_source_ref: str = "",
    ) -> "RouteFailureRecord":
        if failure_domain not in {item.value for item in FailureDomain}:
            raise ProjectError(f"Unsupported route failure domain: {failure_domain}")
        if provenance not in {"NATIVE", "LEGACY_DERIVED"}:
            raise ProjectError(f"Unsupported RouteFailureRecord provenance: {provenance}")
        if not isinstance(research_map_version, int) or isinstance(
            research_map_version, bool
        ) or research_map_version < 1:
            raise ProjectError("RouteFailureRecord.research_map_version must be positive")
        conditions = string_tuple(
            reopen_conditions, "RouteFailureRecord.reopen_conditions", allow_empty=False
        )
        unknown_conditions = set(conditions) - {item.value for item in ReopenCondition}
        if unknown_conditions:
            raise ProjectError(f"Unsupported reopen conditions: {sorted(unknown_conditions)}")
        if not conditions:
            raise ProjectError("RouteFailureRecord requires explicit reopen_conditions")
        evidence = string_tuple(evidence_refs, "RouteFailureRecord.evidence_refs")
        source = require_optional_text(legacy_source_ref, "RouteFailureRecord.legacy_source_ref")
        if provenance == "LEGACY_DERIVED" and not source:
            raise ProjectError("LEGACY_DERIVED RouteFailureRecord requires legacy_source_ref")
        identity = {
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "RouteFailureRecord.root_claim_snapshot_hash"
            ),
            "research_map_id": require_id(
                research_map_id, "RouteFailureRecord.research_map_id"
            ),
            "research_map_version": research_map_version,
            "research_map_hash": require_hash(
                research_map_hash, "RouteFailureRecord.research_map_hash"
            ),
            "obligation_id": require_id(obligation_id, "RouteFailureRecord.obligation_id"),
            "obligation_hash": require_hash(
                obligation_hash, "RouteFailureRecord.obligation_hash"
            ),
            "route_description": require_text(
                route_description, "RouteFailureRecord.route_description"
            ),
            "method_family": require_text(method_family, "RouteFailureRecord.method_family"),
            "dependency_snapshot_hash": context.dependency_snapshot_hash,
            "assumption_snapshot_hash": context.assumption_snapshot_hash,
            "authority_context_hash": context.authority_context_hash,
            "exact_failure_condition": require_text(
                exact_failure_condition, "RouteFailureRecord.exact_failure_condition"
            ),
            "failure_domain": failure_domain,
            "evidence_refs": list(evidence),
            "reopen_conditions": list(conditions),
            "verified_lemma_refs": list(context.verified_lemma_refs),
            "provenance": provenance,
            "legacy_source_ref": source,
        }
        failure_hash = domain_hash("route_failure", stable_value(identity))
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="ROUTE_FAILURE_RECORD",
            route_failure_id=content_id("route-failure", "route_failure_id", identity),
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            research_map_id=identity["research_map_id"],
            research_map_version=research_map_version,
            research_map_hash=identity["research_map_hash"],
            obligation_id=identity["obligation_id"],
            obligation_hash=identity["obligation_hash"],
            route_description=identity["route_description"],
            method_family=identity["method_family"],
            dependency_snapshot_hash=context.dependency_snapshot_hash,
            assumption_snapshot_hash=context.assumption_snapshot_hash,
            authority_context_hash=context.authority_context_hash,
            exact_failure_condition=identity["exact_failure_condition"],
            failure_domain=failure_domain,
            evidence_refs=evidence,
            reopen_conditions=conditions,
            verified_lemma_refs=context.verified_lemma_refs,
            provenance=provenance,
            legacy_source_ref=source,
            created_at=require_text(created_at, "RouteFailureRecord.created_at"),
            created_by=require_text(created_by, "RouteFailureRecord.created_by"),
            route_failure_hash=failure_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RouteFailureRecord":
        fields = {
            "schema_version", "object_type", "route_failure_id",
            "root_claim_snapshot_hash", "research_map_id", "research_map_version",
            "research_map_hash", "obligation_id", "obligation_hash", "route_description",
            "method_family", "dependency_snapshot_hash", "assumption_snapshot_hash",
            "authority_context_hash", "exact_failure_condition", "failure_domain",
            "evidence_refs", "reopen_conditions", "verified_lemma_refs", "provenance",
            "legacy_source_ref", "created_at", "created_by", "route_failure_hash",
        }
        strict_fields(value, fields, "RouteFailureRecord")
        validate_envelope(
            value, object_type="ROUTE_FAILURE_RECORD", name="RouteFailureRecord"
        )
        context = RouteContext.capture(
            dependency_snapshot_hash=value["dependency_snapshot_hash"],
            assumption_snapshot_hash=value["assumption_snapshot_hash"],
            authority_context_hash=value["authority_context_hash"],
            verified_lemma_refs=value["verified_lemma_refs"],
        )
        captured = cls.capture(
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            research_map_id=value["research_map_id"],
            research_map_version=value["research_map_version"],
            research_map_hash=value["research_map_hash"], obligation_id=value["obligation_id"],
            obligation_hash=value["obligation_hash"],
            route_description=value["route_description"], method_family=value["method_family"],
            context=context, exact_failure_condition=value["exact_failure_condition"],
            failure_domain=value["failure_domain"], evidence_refs=value["evidence_refs"],
            reopen_conditions=value["reopen_conditions"], provenance=value["provenance"],
            legacy_source_ref=value["legacy_source_ref"], created_at=value["created_at"],
            created_by=value["created_by"],
        )
        if captured.route_failure_id != value.get(
            "route_failure_id"
        ) or captured.route_failure_hash != value.get("route_failure_hash"):
            raise ProjectError("RouteFailureRecord identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)

    def eligibility(self, current: RouteContext) -> RouteEligibilityDecision:
        changed: list[str] = []
        condition_map = {
            "DEPENDENCY_SNAPSHOT_CHANGED": (
                self.dependency_snapshot_hash != current.dependency_snapshot_hash
            ),
            "ASSUMPTION_SNAPSHOT_CHANGED": (
                self.assumption_snapshot_hash != current.assumption_snapshot_hash
            ),
            "AUTHORITY_CONTEXT_CHANGED": (
                self.authority_context_hash != current.authority_context_hash
            ),
            "NEW_VERIFIED_LEMMA": bool(
                set(current.verified_lemma_refs) - set(self.verified_lemma_refs)
            ),
        }
        for condition in self.reopen_conditions:
            if condition_map.get(condition, False):
                changed.append(condition)
        if changed:
            status = RouteEligibility.REOPENABLE.value
            reason = "an explicit reopen condition is satisfied"
        elif any(
            (
                self.dependency_snapshot_hash != current.dependency_snapshot_hash,
                self.assumption_snapshot_hash != current.assumption_snapshot_hash,
                self.authority_context_hash != current.authority_context_hash,
            )
        ):
            status = RouteEligibility.REVALIDATE.value
            reason = "route context changed outside the declared automatic reopen conditions"
        else:
            status = RouteEligibility.FAILURE_STILL_APPLIES.value
            reason = "exact failure context remains unchanged"
        return RouteEligibilityDecision(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="ROUTE_ELIGIBILITY_DECISION",
            status=status,
            reason=reason,
            changed_contexts=tuple(changed),
        )


def route_failure_from_legacy_fingerprint(
    legacy: Mapping[str, Any],
    *,
    root_claim_snapshot_hash: str,
    research_map_id: str,
    research_map_version: int,
    research_map_hash: str,
    obligation_id: str,
    obligation_hash: str,
    context: RouteContext,
    created_at: str,
) -> RouteFailureRecord:
    """Project one legacy heuristic without pretending it has native precision."""

    fingerprint = require_hash(legacy.get("fingerprint"), "legacy StrategyFingerprint")
    route = " / ".join(
        str(legacy.get(key) or "").strip()
        for key in ("branch", "target_lemma", "method", "key_dependency")
        if str(legacy.get(key) or "").strip()
    )
    return RouteFailureRecord.capture(
        root_claim_snapshot_hash=root_claim_snapshot_hash,
        research_map_id=research_map_id,
        research_map_version=research_map_version,
        research_map_hash=research_map_hash,
        obligation_id=obligation_id,
        obligation_hash=obligation_hash,
        route_description=route or "legacy strategy fingerprint",
        method_family=str(legacy.get("method") or "LEGACY_UNKNOWN"),
        context=context,
        exact_failure_condition=str(legacy.get("failure_point") or "legacy failure point unknown"),
        failure_domain=FailureDomain.UNKNOWN.value,
        evidence_refs=(),
        reopen_conditions=(
            ReopenCondition.DEPENDENCY_SNAPSHOT_CHANGED.value,
            ReopenCondition.NEW_VERIFIED_LEMMA.value,
            ReopenCondition.FAILURE_CONDITION_REMOVED.value,
            ReopenCondition.HUMAN_REVALIDATION.value,
        ),
        provenance="LEGACY_DERIVED",
        legacy_source_ref=f"strategy_fingerprints.json#{fingerprint}",
        created_at=created_at,
        created_by="StrategyFingerprintCompatibilityAdapter",
    )
