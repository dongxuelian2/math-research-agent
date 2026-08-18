import json

import pytest

from openprover.math_research.campaign import CampaignEngine, CampaignStore
from openprover.math_research.project import ProjectError, ProjectStore
from openprover.math_research.scheduler import (
    NORMAL_PROFILE,
    OVERNIGHT_PROFILE,
    RoleScheduler,
    StopController,
    StrategyFingerprint,
    StrategyFingerprintStore,
    resolve_profile,
)


def _store(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Scheduler fixture", demo=True)
    store.add_theorem("target", "Target", "For all n, n=n.", claim_type="equality")
    return store


def test_normal_profile_preserves_conservative_defaults():
    profile = resolve_profile("normal")
    assert profile == NORMAL_PROFILE
    assert profile.budget_seconds == 4 * 60 * 60
    assert profile.initial_workers == profile.max_workers == 3
    assert profile.auto_successor is False
    assert profile.hard_blocker is False
    assert profile.secondary_verification is False


def test_overnight_profile_parses_to_explicit_bounded_settings():
    profile = resolve_profile("overnight")
    assert profile == OVERNIGHT_PROFILE
    assert profile.budget_seconds == 12 * 60 * 60
    assert profile.initial_workers == 4
    assert profile.max_workers == 6
    assert profile.max_repair_cycles == 4
    assert profile.infrastructure_retries == 3
    assert profile.auto_successor is True
    assert profile.auto_dependency_repair is True
    assert profile.hard_blocker is True
    assert profile.secondary_verification is True
    with pytest.raises(ProjectError, match="Unknown research profile"):
        resolve_profile("burn-quota")


def test_scheduler_stays_at_four_without_five_independent_obligations():
    scheduler = RoleScheduler(initial_workers=4, max_workers=6)
    tasks = [
        {"summary": "Construct branch", "description": "Prove the main branch"},
        {"summary": "Adversarial hunt", "description": "Search for counterexample"},
        {"summary": "Boundary endpoints", "description": "Check n=0 and n=1"},
        {"summary": "Dependency audit", "description": "Resolve authority IDs"},
        {"summary": "Construct branch", "description": "Duplicate obligation"},
        {"summary": "Construct branch", "description": "Duplicate obligation"},
    ]
    assignments = scheduler.assign_tasks(tasks)
    assert len(assignments) == 4
    assert len({item.role for item in assignments}) == 4


def test_scheduler_expands_to_six_for_real_parallel_obligations():
    scheduler = RoleScheduler(initial_workers=4, max_workers=6)
    tasks = [
        {
            "summary": f"Obligation {index}",
            "description": description,
            "obligation": f"lemma-{index}",
        }
        for index, description in enumerate([
            "Construct the direct branch",
            "Search adversarially for a counterexample",
            "Reconstruct the exhaustive classification",
            "Develop an alternative independent proof",
            "Check every boundary endpoint",
            "Resolve semantic and foundation authority IDs",
        ])
    ]
    assignments = scheduler.assign_tasks(tasks)
    assert len(assignments) == 6
    assert len({item.role for item in assignments}) == 6
    assert all(item.description.startswith("[Worker role:") for item in assignments)


def test_strategy_fingerprint_freezes_after_same_failure_twice(tmp_path):
    store = _store(tmp_path)
    fingerprints = StrategyFingerprintStore(store)
    strategy = StrategyFingerprint(
        theorem="target",
        branch="high",
        target_lemma="Jacobi reduction",
        method="direct reciprocity",
        key_dependency="FOUND-NT-QR-02",
        failure_point="missing positive-odd condition",
    )
    first = fingerprints.record_failure(strategy)
    second = fingerprints.record_failure(strategy)
    assert first["frozen"] is False
    assert second["frozen"] is True
    assert fingerprints.can_attempt(strategy)[0] is False
    assert fingerprints.can_attempt(strategy, new_dependency=True)[0] is True
    changed = StrategyFingerprint(
        theorem="target",
        branch="high",
        target_lemma="Jacobi reduction",
        method="direct reciprocity",
        key_dependency="FOUND-NT-QR-02",
        failure_point="different parity condition",
    )
    assert changed.fingerprint != strategy.fingerprint
    assert fingerprints.can_attempt(changed)[0] is True


def test_graceful_stop_prevents_new_orchestrator_and_is_resumable(tmp_path):
    store = _store(tmp_path)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    campaigns = CampaignStore(store)
    campaigns.create("campaign-1", target_id="target")
    controller = StopController(store, "campaign-1")
    controller.request(reason="operator maintenance")

    class MustNotStart:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("a new Worker-bearing orchestrator was started")

    result = CampaignEngine(
        store,
        config_path=config,
        worker_count=3,
        orchestrator_factory=MustNotStart,
    ).run("campaign-1")
    assert result["status"] == "STOPPED_AT_CHECKPOINT"
    assert controller.load()["status"] == "CHECKPOINTED"
    resumed = campaigns.resume("campaign-1")
    assert resumed["status"] == "RUNNING"
    assert controller.load()["status"] == "NONE"
