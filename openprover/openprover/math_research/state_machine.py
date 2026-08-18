"""Strict theorem lifecycle and proof audit gate."""

from __future__ import annotations

from dataclasses import dataclass, field


THEOREM_STATUSES = {
    "UNCLASSIFIED",
    "OPEN",
    "IN_RESEARCH",
    "CANDIDATE_PROOF",
    "AUDITING",
    "REJECTED",
    "PARTIAL",
    "PROVED",
    "CONJECTURE",
    "FROZEN",
}

CAMPAIGN_STATUSES = {
    "RUNNING",
    "COMPLETE_PROVED_REPLAY",
    "MATHEMATICAL_EXHAUSTION",
    "BLOCKED_PROVIDER_QUOTA",
    "BLOCKED_INFRASTRUCTURE",
    "HUMAN_REQUIRED",
}

RUN_CHECKPOINT_STATUSES = {
    "TIME_BUDGET_EXHAUSTED",
    "BLOCKED_PROVIDER_QUOTA",
    "BLOCKED_INFRASTRUCTURE",
    "HUMAN_REQUIRED",
}

TRANSITIONS = {
    "UNCLASSIFIED": {"OPEN", "CONJECTURE", "FROZEN"},
    "OPEN": {"IN_RESEARCH", "FROZEN"},
    "IN_RESEARCH": {"OPEN", "CANDIDATE_PROOF", "PARTIAL", "FROZEN"},
    "CANDIDATE_PROOF": {"AUDITING", "IN_RESEARCH", "PARTIAL", "FROZEN"},
    "AUDITING": {"PROVED", "REJECTED", "PARTIAL"},
    "REJECTED": {"IN_RESEARCH", "PARTIAL", "FROZEN"},
    "PARTIAL": {"IN_RESEARCH", "FROZEN"},
    "CONJECTURE": {"OPEN", "FROZEN"},
    "PROVED": {"FROZEN", "IN_RESEARCH"},
    "FROZEN": {"OPEN", "PARTIAL", "CONJECTURE", "PROVED"},
}


class InvalidTransition(ValueError):
    """Raised when a theorem lifecycle transition is not permitted."""


@dataclass(slots=True)
class AuditGate:
    """Machine-readable evidence required before Archivist may mark PROVED."""

    forward_implication: bool = False
    converse_if_applicable: bool = False
    exhaustive_cases: bool = False
    parameter_ranges: bool = False
    boundary_cases: bool = False
    dependencies_valid: bool = False
    no_counterexample: bool = False
    auditors_pass: bool = False
    final_auditor_pass: bool = False
    computational_evidence_separated: bool = False
    failure_reasons: list[str] = field(default_factory=list)
    execution_errors: list[str] = field(default_factory=list)
    inconclusive_audits: list[str] = field(default_factory=list)
    dependency_report: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        flags = (
            self.forward_implication,
            self.converse_if_applicable,
            self.exhaustive_cases,
            self.parameter_ranges,
            self.boundary_cases,
            self.dependencies_valid,
            self.no_counterexample,
            self.auditors_pass,
            self.final_auditor_pass,
            self.computational_evidence_separated,
        )
        return (
            all(flags)
            and not self.failure_reasons
            and not self.execution_errors
            and not self.inconclusive_audits
        )

    @property
    def outcome(self) -> str:
        if self.execution_errors:
            return "INFRASTRUCTURE_ERROR"
        if self.inconclusive_audits:
            return "INCONCLUSIVE"
        if self.passed:
            return "PASS"
        return "MATHEMATICAL_FAIL"

    def to_dict(self) -> dict:
        return {
            "forward_implication": self.forward_implication,
            "converse_if_applicable": self.converse_if_applicable,
            "exhaustive_cases": self.exhaustive_cases,
            "parameter_ranges": self.parameter_ranges,
            "boundary_cases": self.boundary_cases,
            "dependencies_valid": self.dependencies_valid,
            "no_counterexample": self.no_counterexample,
            "auditors_pass": self.auditors_pass,
            "final_auditor_pass": self.final_auditor_pass,
            "computational_evidence_separated": self.computational_evidence_separated,
            "failure_reasons": list(self.failure_reasons),
            "execution_errors": list(self.execution_errors),
            "inconclusive_audits": list(self.inconclusive_audits),
            "dependency_report": dict(self.dependency_report),
            "outcome": self.outcome,
            "passed": self.passed,
        }


def validate_transition(current: str, new: str, *, actor: str,
                        gate: AuditGate | None = None) -> None:
    if current not in THEOREM_STATUSES:
        raise InvalidTransition(f"Unknown current theorem status: {current}")
    if new not in THEOREM_STATUSES:
        raise InvalidTransition(f"Unknown target theorem status: {new}")
    if new == current:
        return
    if new not in TRANSITIONS[current]:
        raise InvalidTransition(f"Transition {current} -> {new} is not allowed")
    if current == "PROVED" and new == "IN_RESEARCH" and actor != "Human":
        raise InvalidTransition("Only an explicit Human re-audit may reopen a PROVED theorem")
    if new == "PROVED":
        if actor != "Archivist":
            raise InvalidTransition("Only Archivist may transition a theorem to PROVED")
        if gate is None or not gate.passed:
            reasons = gate.failure_reasons if gate else ["audit gate was not supplied"]
            raise InvalidTransition(
                "PROVED transition rejected: " + "; ".join(reasons)
            )
