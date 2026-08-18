from openprover.math_research.audit_protocol import (
    AuditResult,
    audit_suite_outcome,
    normalize_audit_result,
)


def test_counterexample_pass_with_dependency_note_remains_pass():
    result = normalize_audit_result("counterexample_hunter", {
        "role": "counterexample_hunter",
        "domain_verdict": "PASS",
        "execution_status": "OK",
        "findings": ["No counterexample found."],
        "failure_reasons": [],
        "cross_audit_notes": ["dependency unverified"],
        "computational_evidence": ["Checked 1 <= n <= 100"],
        "authority_uses": [],
    })
    assert result.passed
    assert result.domain_verdict == "PASS"
    assert result.cross_audit_notes == ["dependency unverified"]


def test_auditor_execution_error_is_not_mathematical_fail():
    result = AuditResult.from_exception(
        "boundary_auditor", UnicodeEncodeError("gbk", "−", 0, 1, "bad")
    )
    assert result.execution_status == "ERROR"
    assert result.domain_verdict == "INCONCLUSIVE"
    assert not result.mathematically_failed
    assert audit_suite_outcome({"boundary_auditor": result}) == "INFRASTRUCTURE_ERROR"


def test_legacy_pass_is_normalized_for_backward_compatibility():
    result = normalize_audit_result("legacy", {
        "role": "legacy",
        "verdict": "PASS",
        "pass": True,
        "findings": [],
        "failure_reasons": [],
        "computational_evidence": [],
    })
    assert result.passed
    assert result.to_dict()["schema_version"] == 2
