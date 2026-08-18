import json

import pytest

from openprover.math_research.campaign import (
    HARD_BLOCKERS,
    PreSubmitGate,
    ReplayPolicy,
)
from openprover.math_research.project import ProjectError, ProjectStore
from openprover.math_research.trust_kernel import (
    DependencyAuthorityResolver,
    TrustKernel,
)


def _resolver(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Gate fixture", demo=True)
    store.add_theorem(
        "proved-dependency",
        "Proved dependency",
        "For every n, n = n.",
        status="PROVED",
        claim_type="equality",
    )
    kernel = TrustKernel.for_project(store)
    return store, DependencyAuthorityResolver(
        foundations=kernel.foundations,
        semantics=kernel.semantics,
        project=store,
    )


def _candidate(*, unresolved=None, sources=None, classified=True, branches=True):
    manifest = {
        "all_external_claims_classified": classified,
        "branches_resolved": branches,
        "unresolved": unresolved or [],
        "authority_uses": [{
            "claim": "The displayed equality",
            "claim_class": "LOCAL_PROOF",
            "authority_id": "",
            "authority_type": "local_proof",
            "proof_location": "§1",
        }],
        "source_paths": sources or [],
    }
    return (
        "# Candidate\n\nThe equality follows by direct algebra.\n\n"
        "<!-- OPENPROVER_AUTHORITY_MANIFEST\n"
        + json.dumps(manifest, ensure_ascii=False, indent=2)
        + "\n-->\n"
    )


def test_hard_gate_accepts_complete_local_proof_manifest(tmp_path):
    _, resolver = _resolver(tmp_path)
    decision = PreSubmitGate(resolver=resolver).evaluate(_candidate())
    assert decision.allowed is True
    assert decision.blockers == []
    assert decision.dependency_report["local_proofs"]


def test_hard_gate_rejects_missing_manifest(tmp_path):
    _, resolver = _resolver(tmp_path)
    decision = PreSubmitGate(resolver=resolver).evaluate("A proof without a manifest")
    assert decision.allowed is False
    assert {item["type"] for item in decision.blockers} == {"MISSING_AUTHORITY"}


@pytest.mark.parametrize("blocker", sorted(HARD_BLOCKERS))
def test_every_structured_hard_blocker_forbids_submit(tmp_path, blocker):
    _, resolver = _resolver(tmp_path)
    decision = PreSubmitGate(resolver=resolver).evaluate(
        _candidate(unresolved=[{"type": blocker, "detail": "still open"}])
    )
    assert decision.allowed is False
    assert blocker in {item["type"] for item in decision.blockers}


def test_dependency_slice_blockers_are_code_level(tmp_path):
    _, resolver = _resolver(tmp_path)
    decision = PreSubmitGate(
        resolver=resolver,
        blocked_dependencies=["unproved-dependency"],
        dependency_cycles=[["a", "b", "a"]],
    ).evaluate(_candidate())
    assert decision.allowed is False
    assert {item["type"] for item in decision.blockers} == {
        "BLOCKED_DEPENDENCY",
        "DEPENDENCY_GAP",
    }


def test_replay_policy_loads_real_manifest_shape_and_inherits_exactly(tmp_path):
    manifest = {
        "schema_version": 1,
        "materialized_sources": {"target": "safe/target.md"},
        "allowed_proved_dependencies": ["GP3"],
        "excluded_later_results": ["later/critical_G_A2_*.md"],
        "excluded_answer_leaks": [{"source_file": "answers/resolution.md"}],
        "approved_historical_authorities": {
            "SEM-G-PRIM-01": "safe/gp3.md"
        },
        "target_cutoff": "2026-01-02T00:00:00+00:00",
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    policy = ReplayPolicy.from_manifest(path)
    restored = ReplayPolicy.from_dict(policy.to_dict())
    assert restored.to_dict() == policy.to_dict()
    assert restored.policy_hash == policy.policy_hash
    assert set(policy.allowed_authority_ids) == {"GP3"}
    assert policy.audit_sources(["safe/target.md", "safe/gp3.md"])[0] is True
    assert policy.audit_sources(["answers/resolution.md"])[0] is False
    assert policy.audit_sources(["later/critical_G_A2_test.md"])[0] is False


def test_replay_manifest_section_exclusion_does_not_forbid_safe_trimmed_file(tmp_path):
    manifest = {
        "materialized_sources": {
            "safe_campaign": "safe/campaign.md"
        },
        "excluded_answer_leaks": [{
            "source_file": "safe/campaign.md",
            "section": "§12 only",
            "action": "exclude section",
        }],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    policy = ReplayPolicy.from_manifest(path)
    assert policy.audit_sources(["safe/campaign.md"])[0] is True


def test_pre_submit_replay_leak_guard_rejects_forbidden_source(tmp_path):
    _, resolver = _resolver(tmp_path)
    policy = ReplayPolicy(
        allowed_sources=("safe/gp3.md",),
        forbidden_sources=("hidden/answer.md",),
    )
    decision = PreSubmitGate(
        resolver=resolver,
        replay_policy=policy,
    ).evaluate(_candidate(sources=["hidden/answer.md"]))
    assert decision.allowed is False
    assert "ANSWER_LEAK_RISK" in {item["type"] for item in decision.blockers}


def test_auto_dependency_repair_requires_provenance_and_leak_pass(tmp_path):
    policy = ReplayPolicy(
        allowed_sources=("safe/gp3.md",),
        forbidden_sources=("hidden/*",),
        allowed_authority_ids=("SEM-G-PRIM-01",),
        approved_historical_authorities=(("SEM-G-PRIM-01", "safe/gp3.md"),),
        target_cutoff="2026-01-02T00:00:00+00:00",
    )
    record = {
        "authority_id": "SEM-G-PRIM-01",
        "authority_type": "semantic",
        "source_file": "safe/gp3.md",
        "source_created_at": "2026-01-01T00:00:00+00:00",
        "identity_verified": True,
        "leak_audit_pass": True,
    }
    allowed, errors = policy.authorize_dependency_repair(record)
    assert allowed is True
    assert errors == []

    source_root = tmp_path / "source"
    (source_root / "safe").mkdir(parents=True)
    (source_root / "safe" / "gp3.md").write_text("真实语义：γ → β，且 ± 保持。", encoding="utf-8")
    materialized = policy.materialize_dependency(
        record,
        source_root=source_root,
        destination_root=tmp_path / "isolated",
    )
    assert materialized.read_text(encoding="utf-8") == "真实语义：γ → β，且 ± 保持。"

    leaked = dict(record, source_file="hidden/answer.md")
    allowed, errors = policy.authorize_dependency_repair(leaked)
    assert allowed is False
    assert any("forbidden" in error or "manifest identity" in error for error in errors)


def test_auto_dependency_repair_rejects_late_or_unverified_source(tmp_path):
    policy = ReplayPolicy(
        allowed_sources=("safe/gp3.md",),
        allowed_authority_ids=("GP3",),
        target_cutoff="2026-01-02T00:00:00+00:00",
    )
    allowed, errors = policy.authorize_dependency_repair({
        "authority_id": "GP3",
        "authority_type": "project_theorem",
        "source_file": "safe/gp3.md",
        "source_created_at": "2026-01-03T00:00:00+00:00",
        "identity_verified": False,
        "leak_audit_pass": True,
    })
    assert allowed is False
    assert any("earlier" in error for error in errors)
    assert any("identity" in error for error in errors)
