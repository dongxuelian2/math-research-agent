from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from openprover.math_research.providers import (
    create_client,
    load_model_config,
    provider_capabilities,
)
from openprover.math_research.routing import ModelRouter, RoutedLLMClient


def heterogeneous_config(*, routine_enabled=True, strategic_cap=20):
    return {
        "tiers": {
            "routine": {
                "enabled": routine_enabled,
                "provider": "mock",
                "model": "mock-routine",
                "reasoning_effort": "low",
            },
            "research": {
                "provider": "mock",
                "model": "mock-research",
                "reasoning_effort": "high",
            },
            "strategic": {
                "provider": "mock",
                "model": "mock-strategic",
                "reasoning_effort": "max",
            },
        },
        "roles": {
            "planner": "strategic",
            "boundary": "routine",
            "dependency": "routine",
            "constructive": "research",
            "alternative-proof": "strategic",
            "literature_searcher": "routine",
            "literature_lead": "research",
        },
        "routing": {
            "fallback_tier": "research",
            "routine_failure_threshold": 2,
            "research_failure_threshold": 3,
            "stalled_frontier_cycles": 2,
            "max_strategic_calls": strategic_cap,
            "max_strategic_calls_per_step": strategic_cap,
            "max_strategic_calls_per_obligation": strategic_cap,
        },
    }


def test_role_to_tier_mapping_and_explicit_disabled_fallback():
    router = ModelRouter(heterogeneous_config())
    expected = {
        "boundary": "routine",
        "dependency": "routine",
        "constructive": "research",
        "alternative-proof": "strategic",
        "planner": "strategic",
        "literature_searcher": "routine",
        "literature_lead": "research",
    }
    assert {role: router.resolve(role, reserve=False).tier for role in expected} == expected

    fallback = ModelRouter(heterogeneous_config(routine_enabled=False)).resolve(
        "boundary", reserve=False
    )
    assert fallback.requested_tier == "routine"
    assert fallback.tier == "research"
    assert fallback.model == "mock-research"
    assert fallback.fallback is True
    assert "disabled" in fallback.fallback_reason


def test_mixed_model_scheduling_archives_per_call_metadata(tmp_path):
    router = ModelRouter(heterogeneous_config(), state_path=tmp_path / "routing_state.json")
    client = RoutedLLMClient(
        router,
        client_factory=create_client,
        default_role="worker",
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "gemini",
    )
    calls = [
        ("boundary", "O-boundary", "B1"),
        ("dependency", "O-dependency", "B2"),
        ("constructive", "O-constructive", "B3"),
        ("alternative-proof", "O-alternative", "B4"),
    ]

    def execute(item):
        role, obligation, branch = item
        return client.call(
            f"[Worker role: {role}]\n[Obligation ID: {obligation}]\n"
            f"[Branch ID: {branch}]\nDo the bounded task.",
            "Base mathematical prompt.",
            label=f"worker_1_{branch}",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(execute, calls))

    routes = {response["routing"]["role"]: response["routing"] for response in responses}
    assert routes["boundary"]["model"] == "mock-routine"
    assert routes["dependency"]["model"] == "mock-routine"
    assert routes["constructive"]["model"] == "mock-research"
    assert routes["alternative-proof"]["model"] == "mock-strategic"
    for metadata in routes.values():
        assert {
            "call_id",
            "parent_call_id",
            "obligation_id",
            "role",
            "tier",
            "provider",
            "model",
            "reasoning_effort",
            "escalation_level",
            "escalation_reason",
            "branch_id",
            "created_at",
        } <= set(metadata)
    snapshot = router.snapshot()
    assert snapshot["usage_by_tier"]["routine"]["calls"] == 2
    assert snapshot["usage_by_tier"]["research"]["calls"] == 1
    assert snapshot["usage_by_tier"]["strategic"]["calls"] == 1
    assert snapshot["calls_by_model"] == {
        "mock-routine": 2,
        "mock-research": 1,
        "mock-strategic": 1,
    }


def test_escalation_triggers_are_monotone_and_strategic_budget_is_hard():
    router = ModelRouter(heterogeneous_config(strategic_cap=1))

    router.record_failure("repeat", "NO_PROGRESS")
    repeated = router.record_failure("repeat", "VERIFIER_REJECTION")
    assert repeated["tier"] == "research"

    disagreement = router.record_verifier_disagreement(
        "conflict", worker_verdict="CORRECT", verifier_verdict="FLAWED"
    )
    assert disagreement["tier"] == "research"

    assert router.promote_high_value("closure")["tier"] == "research"
    assert router.promote_high_value("candidate", proof_candidate=True)["tier"] == "strategic"

    router.record_frontier_cycle("frontier", progress={})
    stalled = router.record_frontier_cycle("frontier", progress={})
    assert stalled["tier"] == "strategic"
    strategic_route = router.resolve("planner", obligation_id="candidate", step_id="step-1")
    assert strategic_route.tier == "strategic"
    capped = router.resolve("alternative-proof", obligation_id="other", step_id="step-2")
    assert capped.tier == "research"
    assert capped.fallback_reason == "strategic call cap reached"

    # An escalated obligation cannot silently fall back to routine.
    no_downgrade = router.resolve(
        "boundary",
        obligation_id="candidate",
        requested_tier="routine",
        reserve=False,
    )
    assert no_downgrade.requested_tier == "routine"
    assert no_downgrade.tier in {"strategic", "research"}


def test_resume_preserves_strategic_obligation_and_completed_call_usage(tmp_path):
    path = tmp_path / "routing.json"
    router = ModelRouter(heterogeneous_config(), state_path=path)
    router.promote_high_value("O", proof_candidate=True)
    route = router.resolve("boundary", obligation_id="O", reserve=False)
    metadata = router.begin_call(route, obligation_id="O", branch_id="B")
    router.finish_call(
        metadata["call_id"],
        response={
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "reasoning_tokens": 1,
                "cached_tokens": 3,
            }
        },
    )

    resumed = ModelRouter(heterogeneous_config(), state_path=path)
    resumed_route = resumed.resolve(
        "boundary", obligation_id="O", requested_tier="routine", reserve=False
    )
    assert resumed_route.tier == "strategic"
    assert resumed.snapshot()["usage_by_tier"]["strategic"] == {
        "calls": 1,
        "input_tokens": 10,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "cached_tokens": 3,
    }


def test_provider_capabilities_and_mixed_provider_role_routing(tmp_path):
    assert provider_capabilities("gemini").supports_native_tools is True
    assert provider_capabilities("vertex_gemini").supports_structured_output is True
    assert provider_capabilities("codex_cli").supports_interrupt is True
    assert provider_capabilities("codex_cli").supports_native_tools is False
    assert provider_capabilities("openai").supports_usage is True
    assert provider_capabilities("mock").supports_reasoning_tiers is False

    config = {
        "tiers": {
            "routine": {"provider": "gemini", "model": "gemini-routine"},
            "research": {
                "provider": "codex_cli",
                "model": None,
                "reasoning_effort": "high",
            },
            "strategic": {
                "provider": "openai",
                "model": "gpt-strategic",
                "reasoning_effort": "high",
            },
        },
        "roles": {
            "planner": "strategic",
            "worker": "research",
            "dependency_auditor": "routine",
        },
    }
    path = tmp_path / "mixed-providers.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    loaded = load_model_config(path)
    router = ModelRouter(loaded)
    assert (
        router.resolve("planner", reserve=False).provider,
        router.resolve("planner", reserve=False).model,
    ) == (
        "openai",
        "gpt-strategic",
    )
    assert router.resolve("worker", reserve=False).provider == "codex_cli"
    assert router.resolve("dependency_auditor", reserve=False).provider == "gemini"
