"""Orthogonal mathematical verdict and execution status for formal audits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import AuditResultSchema, SchemaError, parse_structured_response


DOMAIN_VERDICTS = {"PASS", "FAIL", "INCONCLUSIVE"}
EXECUTION_STATUSES = {"OK", "ERROR"}


@dataclass(slots=True)
class AuditResult:
    role: str
    domain_verdict: str
    execution_status: str
    findings: list[str] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    cross_audit_notes: list[str] = field(default_factory=list)
    computational_evidence: list[str] = field(default_factory=list)
    summary: str = ""
    criteria: dict[str, bool] = field(default_factory=dict)
    authority_uses: list[dict] = field(default_factory=list)
    execution_error: str = ""

    @property
    def passed(self) -> bool:
        return self.execution_status == "OK" and self.domain_verdict == "PASS"

    @property
    def mathematically_failed(self) -> bool:
        return self.execution_status == "OK" and self.domain_verdict == "FAIL"

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": 3,
            "role": self.role,
            "domain_verdict": self.domain_verdict,
            "execution_status": self.execution_status,
            "findings": list(self.findings),
            "failure_reasons": list(self.failure_reasons),
            "cross_audit_notes": list(self.cross_audit_notes),
            "computational_evidence": list(self.computational_evidence),
            "summary": self.summary,
            "criteria": dict(self.criteria),
            "authority_uses": list(self.authority_uses),
            "execution_error": self.execution_error,
        }
        return value

    @classmethod
    def from_exception(cls, role: str, exc: BaseException) -> "AuditResult":
        return cls(
            role=role,
            domain_verdict="INCONCLUSIVE",
            execution_status="ERROR",
            failure_reasons=[],
            execution_error=str(exc),
            summary="Auditor execution did not complete successfully.",
        )


def normalize_audit_result(role: str, raw: dict) -> AuditResult:
    """Validate one typed audit document without inspecting model prose."""
    if not isinstance(raw, dict):
        raise ValueError("Auditor result must be a JSON object")
    try:
        validated = AuditResultSchema.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"Invalid auditor result: {exc}") from exc
    if validated.role != role:
        raise ValueError(f"Auditor role mismatch: expected {role!r}, got {validated.role!r}")
    execution_status = validated.execution_status
    domain_verdict = "INCONCLUSIVE" if execution_status == "ERROR" else validated.domain_verdict
    return AuditResult(
        role=validated.role,
        domain_verdict=domain_verdict,
        execution_status=execution_status,
        findings=list(validated.findings),
        failure_reasons=list(validated.failure_reasons),
        cross_audit_notes=list(validated.cross_audit_notes),
        computational_evidence=list(validated.computational_evidence),
        summary=validated.summary,
        criteria=validated.criteria.model_dump(mode="python"),
        authority_uses=[item.model_dump(mode="json") for item in validated.authority_uses],
        execution_error=validated.execution_error,
    )


def parse_audit_response(role: str, response: dict) -> AuditResult:
    """Validate one provider response against ``AuditResultSchema``."""

    try:
        validated = parse_structured_response(response, AuditResultSchema)
    except SchemaError as exc:
        raise ValueError(f"{role} returned invalid structured audit output: {exc}") from exc
    return normalize_audit_result(role, validated.model_dump(mode="python"))


def audit_suite_outcome(results: dict[str, AuditResult]) -> str:
    if any(result.execution_status == "ERROR" for result in results.values()):
        return "INFRASTRUCTURE_ERROR"
    if any(result.domain_verdict == "FAIL" for result in results.values()):
        return "MATHEMATICAL_FAIL"
    if any(result.domain_verdict == "INCONCLUSIVE" for result in results.values()):
        return "INCONCLUSIVE"
    return "PASS"
