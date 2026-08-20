"""SessionClosure, evidence projection, and deterministic obligation gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .directive import TacticalExecutionStatus, TacticalSession
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
from .research_obligation import ResearchObligation
from .truth_identity import domain_hash


class EvidenceKind(str, Enum):
    CANDIDATE = "CANDIDATE"
    VERIFIER = "VERIFIER"
    AUDIT = "AUDIT"
    AUTHORITY = "AUTHORITY"
    TRUST_RECEIPT = "TRUST_RECEIPT"
    COMPUTATIONAL = "COMPUTATIONAL"
    ROUTE_DIAGNOSTIC = "ROUTE_DIAGNOSTIC"
    OTHER = "OTHER"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AuthorityStatus(str, Enum):
    TRUSTED = "TRUSTED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ResolutionStatus(str, Enum):
    RESOLUTION_ACCEPTED = "RESOLUTION_ACCEPTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    AUTHORITY_BLOCKED = "AUTHORITY_BLOCKED"
    AUDIT_FAILED = "AUDIT_FAILED"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"


@dataclass(frozen=True, slots=True)
class RawArtifactRef:
    artifact_kind: str
    retained_path: str
    artifact_sha256: str
    original_path: str
    producer: str

    @classmethod
    def capture(
        cls,
        *,
        artifact_kind: str,
        retained_path: str,
        artifact_sha256: str,
        original_path: str,
        producer: str,
    ) -> "RawArtifactRef":
        return cls(
            artifact_kind=require_text(artifact_kind, "RawArtifactRef.artifact_kind"),
            retained_path=require_text(retained_path, "RawArtifactRef.retained_path"),
            artifact_sha256=require_hash(artifact_sha256, "RawArtifactRef.artifact_sha256"),
            original_path=require_text(original_path, "RawArtifactRef.original_path"),
            producer=require_text(producer, "RawArtifactRef.producer"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RawArtifactRef":
        strict_fields(
            value,
            {"artifact_kind", "retained_path", "artifact_sha256", "original_path", "producer"},
            "RawArtifactRef",
        )
        return cls.capture(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    schema_version: int
    object_type: str
    evidence_id: str
    evidence_kind: str
    obligation_id: str
    obligation_hash: str
    root_claim_snapshot_hash: str
    artifact_sha256: str
    retained_artifact_path: str
    scope_obligation_ids: tuple[str, ...]
    verifier_status: str
    audit_status: str
    authority_status: str
    authority_refs: tuple[str, ...]
    summary: str
    evidence_hash: str

    @classmethod
    def capture(
        cls,
        *,
        evidence_kind: str,
        obligation_id: str,
        obligation_hash: str,
        root_claim_snapshot_hash: str,
        artifact_sha256: str,
        retained_artifact_path: str,
        scope_obligation_ids: tuple[str, ...] | list[str],
        verifier_status: str,
        audit_status: str,
        authority_status: str,
        authority_refs: tuple[str, ...] | list[str] = (),
        summary: str = "",
    ) -> "EvidenceProjection":
        if evidence_kind not in {item.value for item in EvidenceKind}:
            raise ProjectError(f"Unsupported evidence kind: {evidence_kind}")
        if verifier_status not in {item.value for item in ValidationStatus}:
            raise ProjectError(f"Unsupported verifier status: {verifier_status}")
        if audit_status not in {item.value for item in ValidationStatus}:
            raise ProjectError(f"Unsupported audit status: {audit_status}")
        if authority_status not in {item.value for item in AuthorityStatus}:
            raise ProjectError(f"Unsupported authority status: {authority_status}")
        scopes = string_tuple(
            scope_obligation_ids, "EvidenceProjection.scope_obligation_ids", allow_empty=False
        )
        if not scopes:
            raise ProjectError("EvidenceProjection requires explicit obligation scope")
        authorities = string_tuple(authority_refs, "EvidenceProjection.authority_refs")
        identity = {
            "evidence_kind": evidence_kind,
            "obligation_id": require_id(obligation_id, "EvidenceProjection.obligation_id"),
            "obligation_hash": require_hash(obligation_hash, "EvidenceProjection.obligation_hash"),
            "root_claim_snapshot_hash": require_hash(
                root_claim_snapshot_hash, "EvidenceProjection.root_claim_snapshot_hash"
            ),
            "artifact_sha256": require_hash(artifact_sha256, "EvidenceProjection.artifact_sha256"),
            "retained_artifact_path": require_text(
                retained_artifact_path, "EvidenceProjection.retained_artifact_path"
            ),
            "scope_obligation_ids": list(scopes),
            "verifier_status": verifier_status,
            "audit_status": audit_status,
            "authority_status": authority_status,
            "authority_refs": list(authorities),
            "summary": require_optional_text(summary, "EvidenceProjection.summary"),
        }
        evidence_hash = domain_hash("research_evidence_projection", stable_value(identity))
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="EVIDENCE_PROJECTION",
            evidence_id=content_id("evidence", "research_evidence_id", identity),
            evidence_kind=evidence_kind,
            obligation_id=identity["obligation_id"],
            obligation_hash=identity["obligation_hash"],
            root_claim_snapshot_hash=identity["root_claim_snapshot_hash"],
            artifact_sha256=identity["artifact_sha256"],
            retained_artifact_path=identity["retained_artifact_path"],
            scope_obligation_ids=scopes,
            verifier_status=verifier_status,
            audit_status=audit_status,
            authority_status=authority_status,
            authority_refs=authorities,
            summary=identity["summary"],
            evidence_hash=evidence_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceProjection":
        fields = {
            "schema_version",
            "object_type",
            "evidence_id",
            "evidence_kind",
            "obligation_id",
            "obligation_hash",
            "root_claim_snapshot_hash",
            "artifact_sha256",
            "retained_artifact_path",
            "scope_obligation_ids",
            "verifier_status",
            "audit_status",
            "authority_status",
            "authority_refs",
            "summary",
            "evidence_hash",
        }
        strict_fields(value, fields, "EvidenceProjection")
        validate_envelope(value, object_type="EVIDENCE_PROJECTION", name="EvidenceProjection")
        captured = cls.capture(
            evidence_kind=value["evidence_kind"],
            obligation_id=value["obligation_id"],
            obligation_hash=value["obligation_hash"],
            root_claim_snapshot_hash=value["root_claim_snapshot_hash"],
            artifact_sha256=value["artifact_sha256"],
            retained_artifact_path=value["retained_artifact_path"],
            scope_obligation_ids=value["scope_obligation_ids"],
            verifier_status=value["verifier_status"],
            audit_status=value["audit_status"],
            authority_status=value["authority_status"],
            authority_refs=value["authority_refs"],
            summary=value["summary"],
        )
        if captured.evidence_id != value.get("evidence_id") or captured.evidence_hash != value.get(
            "evidence_hash"
        ):
            raise ProjectError("EvidenceProjection identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class ProviderProvenance:
    provider: str
    model: str
    call_refs: tuple[str, ...]

    @classmethod
    def capture(cls, *, provider: str, model: str, call_refs=()):
        return cls(
            provider=require_text(provider, "ProviderProvenance.provider"),
            model=require_optional_text(model, "ProviderProvenance.model"),
            call_refs=string_tuple(call_refs, "ProviderProvenance.call_refs"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        strict_fields(value, {"provider", "model", "call_refs"}, "ProviderProvenance")
        return cls.capture(**value)

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


@dataclass(frozen=True, slots=True)
class SessionClosure:
    schema_version: int
    object_type: str
    session_closure_id: str
    tactical_session_id: str
    session_hash: str
    directive_id: str
    obligation_id: str
    obligation_hash: str
    research_map_id: str
    research_map_version: int
    research_map_hash: str
    root_claim_snapshot_hash: str
    execution_status: str
    raw_artifacts: tuple[RawArtifactRef, ...]
    validated_evidence: tuple[EvidenceProjection, ...]
    unresolved_findings: tuple[str, ...]
    provider_provenance: tuple[ProviderProvenance, ...]
    closed_at: str
    closed_by: str
    closure_hash: str

    @classmethod
    def capture(
        cls,
        *,
        session: TacticalSession,
        execution_status: str,
        raw_artifacts: tuple[RawArtifactRef, ...] | list[RawArtifactRef],
        validated_evidence: tuple[EvidenceProjection, ...] | list[EvidenceProjection],
        unresolved_findings: tuple[str, ...] | list[str],
        provider_provenance: tuple[ProviderProvenance, ...] | list[ProviderProvenance],
        closed_at: str,
        closed_by: str,
    ) -> "SessionClosure":
        if execution_status not in {item.value for item in TacticalExecutionStatus} - {
            TacticalExecutionStatus.CREATED.value,
            TacticalExecutionStatus.RUNNING.value,
        }:
            raise ProjectError("SessionClosure requires a terminal execution status")
        raw = tuple(raw_artifacts)
        evidence = tuple(validated_evidence)
        provenance = tuple(provider_provenance)
        if not all(isinstance(item, RawArtifactRef) for item in raw):
            raise ProjectError("SessionClosure.raw_artifacts must be typed")
        if not all(isinstance(item, EvidenceProjection) for item in evidence):
            raise ProjectError("SessionClosure.validated_evidence must be typed")
        if not all(isinstance(item, ProviderProvenance) for item in provenance):
            raise ProjectError("SessionClosure.provider_provenance must be typed")
        raw_hashes = {item.artifact_sha256 for item in raw}
        if any(item.artifact_sha256 not in raw_hashes for item in evidence):
            raise ProjectError("Validated evidence must reference a retained raw artifact")
        identity = {
            "tactical_session_id": session.tactical_session_id,
            "session_hash": session.session_hash,
            "directive_id": session.directive_id,
            "obligation_id": session.obligation_id,
            "obligation_hash": session.obligation_hash,
            "research_map_id": session.research_map_id,
            "research_map_version": session.research_map_version,
            "research_map_hash": session.research_map_hash,
            "root_claim_snapshot_hash": session.root_claim_snapshot_hash,
            "execution_status": execution_status,
            "raw_artifacts": [item.to_dict() for item in raw],
            "validated_evidence": [item.to_dict() for item in evidence],
            "unresolved_findings": list(
                string_tuple(unresolved_findings, "SessionClosure.unresolved_findings")
            ),
            "provider_provenance": [item.to_dict() for item in provenance],
        }
        closure_hash = domain_hash("session_closure", stable_value(identity))
        return cls(
            schema_version=RESEARCH_SCHEMA_VERSION,
            object_type="SESSION_CLOSURE",
            session_closure_id=content_id("closure", "session_closure_id", identity),
            tactical_session_id=session.tactical_session_id,
            session_hash=session.session_hash,
            directive_id=session.directive_id,
            obligation_id=session.obligation_id,
            obligation_hash=session.obligation_hash,
            research_map_id=session.research_map_id,
            research_map_version=session.research_map_version,
            research_map_hash=session.research_map_hash,
            root_claim_snapshot_hash=session.root_claim_snapshot_hash,
            execution_status=execution_status,
            raw_artifacts=raw,
            validated_evidence=evidence,
            unresolved_findings=tuple(identity["unresolved_findings"]),
            provider_provenance=provenance,
            closed_at=require_text(closed_at, "SessionClosure.closed_at"),
            closed_by=require_text(closed_by, "SessionClosure.closed_by"),
            closure_hash=closure_hash,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], session: TacticalSession) -> "SessionClosure":
        fields = {
            "schema_version",
            "object_type",
            "session_closure_id",
            "tactical_session_id",
            "session_hash",
            "directive_id",
            "obligation_id",
            "obligation_hash",
            "research_map_id",
            "research_map_version",
            "research_map_hash",
            "root_claim_snapshot_hash",
            "execution_status",
            "raw_artifacts",
            "validated_evidence",
            "unresolved_findings",
            "provider_provenance",
            "closed_at",
            "closed_by",
            "closure_hash",
        }
        strict_fields(value, fields, "SessionClosure")
        validate_envelope(value, object_type="SESSION_CLOSURE", name="SessionClosure")
        captured = cls.capture(
            session=session,
            execution_status=value["execution_status"],
            raw_artifacts=[RawArtifactRef.from_dict(item) for item in value["raw_artifacts"]],
            validated_evidence=[
                EvidenceProjection.from_dict(item) for item in value["validated_evidence"]
            ],
            unresolved_findings=value["unresolved_findings"],
            provider_provenance=[
                ProviderProvenance.from_dict(item) for item in value["provider_provenance"]
            ],
            closed_at=value["closed_at"],
            closed_by=value["closed_by"],
        )
        if captured.session_closure_id != value.get(
            "session_closure_id"
        ) or captured.closure_hash != value.get("closure_hash"):
            raise ProjectError("SessionClosure identity mismatch")
        return captured

    def to_dict(self) -> dict[str, Any]:
        value = artifact_dict(self)
        value["raw_artifacts"] = [item.to_dict() for item in self.raw_artifacts]
        value["validated_evidence"] = [item.to_dict() for item in self.validated_evidence]
        value["provider_provenance"] = [item.to_dict() for item in self.provider_provenance]
        return value


@dataclass(frozen=True, slots=True)
class ObligationResolutionDecision:
    schema_version: int
    object_type: str
    status: str
    obligation_id: str
    session_closure_id: str
    accepted_evidence_ids: tuple[str, ...]
    reason: str
    decision_hash: str

    def to_dict(self) -> dict[str, Any]:
        return artifact_dict(self)


def can_resolve_obligation(
    obligation: ResearchObligation,
    closure: SessionClosure,
    *,
    current_claim_snapshot_hash: str,
) -> ObligationResolutionDecision:
    status: str
    reason: str
    evidence = closure.validated_evidence
    if closure.obligation_id != obligation.obligation_id or closure.obligation_hash != (
        obligation.obligation_hash
    ):
        status = ResolutionStatus.SCOPE_MISMATCH.value
        reason = "SessionClosure targets a different obligation semantic revision"
    elif any(
        digest != current_claim_snapshot_hash
        for digest in (
            obligation.root_claim_snapshot_hash,
            closure.root_claim_snapshot_hash,
            *(item.root_claim_snapshot_hash for item in evidence),
        )
    ):
        status = ResolutionStatus.STALE_EVIDENCE.value
        reason = "obligation or evidence is not bound to the current root ClaimSnapshot"
    elif any(
        obligation.obligation_id not in item.scope_obligation_ids
        or item.obligation_id != obligation.obligation_id
        or item.obligation_hash != obligation.obligation_hash
        for item in evidence
    ):
        status = ResolutionStatus.SCOPE_MISMATCH.value
        reason = "validated evidence scope does not cover the exact obligation"
    elif any(item.authority_status == AuthorityStatus.BLOCKED.value for item in evidence):
        status = ResolutionStatus.AUTHORITY_BLOCKED.value
        reason = "an evidence authority is blocked"
    elif any(item.audit_status == ValidationStatus.FAIL.value for item in evidence):
        status = ResolutionStatus.AUDIT_FAILED.value
        reason = "an audit projection failed"
    else:
        kinds = {item.evidence_kind for item in evidence}
        has_verifier_pass = any(
            item.evidence_kind == EvidenceKind.VERIFIER.value
            and item.verifier_status == ValidationStatus.PASS.value
            for item in evidence
        )
        has_audit_pass = any(
            item.evidence_kind == EvidenceKind.AUDIT.value
            and item.audit_status == ValidationStatus.PASS.value
            for item in evidence
        )
        has_trusted_authority = any(
            item.authority_status == AuthorityStatus.TRUSTED.value for item in evidence
        )
        if not (
            EvidenceKind.CANDIDATE.value in kinds
            and has_verifier_pass
            and has_audit_pass
            and has_trusted_authority
        ):
            status = ResolutionStatus.INSUFFICIENT_EVIDENCE.value
            reason = "candidate, verifier PASS, audit PASS, and trusted authority are required"
        else:
            status = ResolutionStatus.RESOLUTION_ACCEPTED.value
            reason = "validated evidence satisfies the deterministic research resolution gate"
    accepted = (
        tuple(item.evidence_id for item in evidence)
        if status == ResolutionStatus.RESOLUTION_ACCEPTED.value
        else ()
    )
    identity = {
        "status": status,
        "obligation_id": obligation.obligation_id,
        "session_closure_id": closure.session_closure_id,
        "accepted_evidence_ids": list(accepted),
        "reason": reason,
    }
    return ObligationResolutionDecision(
        schema_version=RESEARCH_SCHEMA_VERSION,
        object_type="OBLIGATION_RESOLUTION_DECISION",
        status=status,
        obligation_id=obligation.obligation_id,
        session_closure_id=closure.session_closure_id,
        accepted_evidence_ids=accepted,
        reason=reason,
        decision_hash=domain_hash("obligation_resolution_decision", identity),
    )
