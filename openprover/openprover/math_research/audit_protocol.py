"""Orthogonal mathematical verdict and execution status for formal audits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
            "schema_version": 2,
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
            # Compatibility fields for older report readers.
            "verdict": self.domain_verdict,
            "pass": self.passed,
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


def _string_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Audit field {field_name} must be an array")
    return [str(item) for item in value]


def normalize_audit_result(role: str, raw: dict) -> AuditResult:
    """Normalize v2 or legacy audit JSON without conflating errors and FAIL."""
    if not isinstance(raw, dict):
        raise ValueError("Auditor result must be a JSON object")
    execution_status = str(raw.get("execution_status", "OK")).upper()
    domain_verdict = str(
        raw.get("domain_verdict", raw.get("verdict", ""))
    ).upper()
    if execution_status not in EXECUTION_STATUSES:
        raise ValueError(f"Invalid auditor execution_status: {execution_status!r}")
    if execution_status == "ERROR":
        domain_verdict = "INCONCLUSIVE"
    if domain_verdict not in DOMAIN_VERDICTS:
        raise ValueError(f"Invalid auditor domain_verdict: {domain_verdict!r}")
    criteria = raw.get("criteria") or {}
    if not isinstance(criteria, dict):
        raise ValueError("Audit field criteria must be an object")
    authority_uses = raw.get("authority_uses") or raw.get("claim_authorities") or []
    if not isinstance(authority_uses, list):
        raise ValueError("Audit field authority_uses must be an array")
    return AuditResult(
        role=str(raw.get("role") or role),
        domain_verdict=domain_verdict,
        execution_status=execution_status,
        findings=_string_list(raw.get("findings"), field_name="findings"),
        failure_reasons=_string_list(
            raw.get("failure_reasons"), field_name="failure_reasons"
        ),
        cross_audit_notes=_string_list(
            raw.get("cross_audit_notes"), field_name="cross_audit_notes"
        ),
        computational_evidence=_string_list(
            raw.get("computational_evidence"), field_name="computational_evidence"
        ),
        summary=str(raw.get("summary") or ""),
        criteria={str(key): bool(value) for key, value in criteria.items()},
        authority_uses=list(authority_uses),
        execution_error=str(raw.get("execution_error") or ""),
    )


def audit_suite_outcome(results: dict[str, AuditResult]) -> str:
    if any(result.execution_status == "ERROR" for result in results.values()):
        return "INFRASTRUCTURE_ERROR"
    if any(result.domain_verdict == "FAIL" for result in results.values()):
        return "MATHEMATICAL_FAIL"
    if any(result.domain_verdict == "INCONCLUSIVE" for result in results.values()):
        return "INCONCLUSIVE"
    return "PASS"
