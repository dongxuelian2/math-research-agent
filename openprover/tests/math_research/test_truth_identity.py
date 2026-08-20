from __future__ import annotations

import copy

import pytest

from openprover.math_research.claim_snapshot import (
    ClaimSnapshot,
    SnapshotComparisonStatus,
    compare_claim_snapshots,
)
from openprover.math_research.project import ProjectError
from openprover.math_research.truth_identity import (
    AssertionIdentity,
    AssumptionSnapshot,
    AuthorityBinding,
    DependencyEntry,
    DependencySnapshot,
    domain_hash,
    project_record_hash,
    prompt_projection_hash,
    source_artifact_sha256,
    trust_policy_fingerprint,
)


def _assertion(statement: str = "For every n, n = n.") -> AssertionIdentity:
    return AssertionIdentity.capture(
        assertion_kind="PROJECT_THEOREM",
        stable_id="target",
        statement=statement,
        claim_type="equality",
        notation_scope="integers",
    )


def _binding(assertion: AssertionIdentity, *, content: str = "v1") -> AuthorityBinding:
    return AuthorityBinding.capture(
        authority_kind="FOUNDATION_REGISTRY",
        authority_id="foundation-core",
        assertion_identity_hash=assertion.assertion_identity_hash,
        authority_content_hash=domain_hash("registry_content", content),
        authority_status="VALID",
        provenance={"registry_version": 1},
    )


def _snapshot(
    *,
    assertion: AssertionIdentity | None = None,
    dependency_content: str = "dep-v1",
    assumption: str = "n is an integer",
    authority_content: str = "v1",
    policy: str = "P1",
    status: str = "AUDITING",
) -> ClaimSnapshot:
    assertion = assertion or _assertion()
    binding = _binding(assertion, content=authority_content)
    dependency_binding = AuthorityBinding.capture(
        authority_kind="PROJECT_THEOREM",
        authority_id="dep",
        assertion_identity_hash=domain_hash("assertion_identity", dependency_content),
        authority_content_hash=domain_hash("project_record", dependency_content),
        authority_status="PROVED",
        provenance={"theorem_id": "dep"},
    )
    dependencies = DependencySnapshot.capture(
        target_assertion_hash=assertion.assertion_identity_hash,
        dependencies=[
            DependencyEntry(
                dependency_id="dep",
                kind="THEOREM",
                assertion_identity_hash=dependency_binding.assertion_identity_hash,
                authority_binding_hash=dependency_binding.binding_hash,
                captured_status="PROVED",
            )
        ],
    )
    assumptions = AssumptionSnapshot.capture(
        target_id="target",
        assumptions=[{"id": "A1", "statement": assumption}],
        semantic_scope={"notation_scope": "integers"},
    )
    return ClaimSnapshot.capture(
        theorem_id="target",
        assertion_identity=assertion,
        dependency_snapshot=dependencies,
        assumption_snapshot=assumptions,
        authority_bindings=(binding, dependency_binding),
        trust_policy_fingerprint=trust_policy_fingerprint({"policy": policy}),
        captured_status=status,
        captured_at="2026-08-20T00:00:00+00:00",
        project_record_hash=project_record_hash({"id": "target", "status": status}),
    )


def test_t1_same_assertion_different_source_artifact_has_separate_hash_domains():
    first = _assertion("e\u0301 = é\r\n")
    second = _assertion("é = é")

    assert first.assertion_identity_hash == second.assertion_identity_hash
    assert source_artifact_sha256(b"source bytes one") != source_artifact_sha256(
        b"source bytes two"
    )


def test_t2_same_filename_changed_assertion_changes_identity():
    filename = "theorem.md"
    before = _assertion("x = x")
    after = _assertion("x = x + 1")

    assert filename == filename
    assert before.assertion_identity_hash != after.assertion_identity_hash


def test_t3_dependency_mutation_is_revalidation_required():
    comparison = compare_claim_snapshots(
        _snapshot(dependency_content="dep-v1"),
        _snapshot(dependency_content="dep-v2"),
    )

    assert comparison.status == SnapshotComparisonStatus.DEPENDENCY_CHANGED.value
    assert comparison.disposition == "REVALIDATION_REQUIRED"


def test_t4_assumption_mutation_is_revalidation_required():
    comparison = compare_claim_snapshots(
        _snapshot(assumption="n is positive"),
        _snapshot(assumption="n is nonnegative"),
    )

    assert comparison.status == "ASSUMPTION_CHANGED"
    assert comparison.disposition == "REVALIDATION_REQUIRED"


def test_t5_authority_mutation_is_revalidation_required():
    comparison = compare_claim_snapshots(
        _snapshot(authority_content="registry-v1"),
        _snapshot(authority_content="registry-v2"),
    )

    assert comparison.status == "AUTHORITY_CHANGED"
    assert comparison.disposition == "REVALIDATION_REQUIRED"


def test_t6_trust_policy_mutation_is_revalidation_required():
    before = _snapshot(policy="P1")
    after = _snapshot(policy="P2")

    assert before.assertion_identity_hash == after.assertion_identity_hash
    comparison = compare_claim_snapshots(before, after)
    assert comparison.status == "TRUST_POLICY_CHANGED"


def test_t7_prompt_projection_is_not_assertion_or_claim_identity():
    snapshot = _snapshot()
    first_projection = prompt_projection_hash("compact prompt")
    second_projection = prompt_projection_hash("expanded prompt")

    assert first_projection != second_projection
    assert snapshot == _snapshot()


def test_assertion_change_is_hard_stale_and_snapshot_tampering_fails_closed():
    before = _snapshot(assertion=_assertion("x = x"))
    after = _snapshot(assertion=_assertion("x = x + 1"))
    comparison = compare_claim_snapshots(before, after)
    assert comparison.status == "ASSERTION_CHANGED"
    assert comparison.disposition == "HARD_STALE"

    tampered = copy.deepcopy(before.to_dict())
    tampered["dependency_snapshot_hash"] = domain_hash("dependency_snapshot", "forged")
    with pytest.raises(ProjectError, match="dependency_snapshot_hash mismatch"):
        ClaimSnapshot.from_dict(tampered)


def test_unknown_schema_and_fields_require_migration():
    snapshot = _snapshot().to_dict()
    snapshot["schema_version"] = 2
    with pytest.raises(ProjectError, match="migration is required"):
        ClaimSnapshot.from_dict(snapshot)

    snapshot = _snapshot().to_dict()
    snapshot["future_field"] = True
    with pytest.raises(ProjectError, match="fields do not match schema 1"):
        ClaimSnapshot.from_dict(snapshot)
