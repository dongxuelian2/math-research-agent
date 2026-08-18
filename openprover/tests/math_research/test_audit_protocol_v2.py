from openprover.math_research.audit_protocol import (
    AuditResult,
    audit_suite_outcome,
    normalize_audit_result,
)


def test_counterexample_pass_with_dependency_note_remains_pass():
    result = normalize_audit_result("counterexample_hunter", {
        "schema_version": 3,
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


def test_audit_documents_are_archived_as_schema_v3():
    result = normalize_audit_result("legacy", {
        "schema_version": 3,
        "role": "legacy",
        "domain_verdict": "PASS",
        "execution_status": "OK",
        "findings": [],
        "failure_reasons": [],
        "computational_evidence": [],
        "criteria": {
            "forward_implication": True,
            "converse_if_applicable": True,
            "exhaustive_cases": True,
            "parameter_ranges": True,
            "boundary_cases": True,
            "dependencies_valid": True,
            "no_counterexample": True,
            "auditors_pass": True,
            "computational_evidence_separated": True,
        },
    })
    assert result.passed
    assert result.to_dict()["schema_version"] == 3
