import pytest

from openprover.math_research.state_machine import AuditGate, InvalidTransition, validate_transition


def complete_gate() -> AuditGate:
    return AuditGate(
        forward_implication=True,
        converse_if_applicable=True,
        exhaustive_cases=True,
        parameter_ranges=True,
        boundary_cases=True,
        dependencies_valid=True,
        no_counterexample=True,
        auditors_pass=True,
        final_auditor_pass=True,
        computational_evidence_separated=True,
    )


def test_only_archivist_can_mark_proved():
    with pytest.raises(InvalidTransition):
        validate_transition("AUDITING", "PROVED", actor="Worker", gate=complete_gate())
    validate_transition("AUDITING", "PROVED", actor="Archivist", gate=complete_gate())


def test_incomplete_gate_cannot_mark_proved():
    gate = complete_gate()
    gate.boundary_cases = False
    with pytest.raises(InvalidTransition):
        validate_transition("AUDITING", "PROVED", actor="Archivist", gate=gate)
