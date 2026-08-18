import json

from openprover.math_research.campaign import CampaignEngine, CampaignStore
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectStore
from openprover.math_research.scheduler import OVERNIGHT_PROFILE


def _mock_config(path):
    path.write_text(json.dumps({
        "schema_version": 1,
        "isolation": True,
        "budget": {"mode": "time", "limit": 120, "conclude_after": 0.99},
        "roles": {
            "planner": {"provider": "mock", "model": "mock-planner"},
            "worker": {"provider": "mock", "model": "mock-worker"},
            "cheap_auditor": {"provider": "mock", "model": "mock-auditor"},
            "final_auditor": {"provider": "mock", "model": "mock-final"},
        },
    }), encoding="utf-8")
    return path


def _demo_store(tmp_path):
    store = ProjectStore.initialize(tmp_path / "demo", "Demo", demo=True)
    store.add_theorem(
        "demo-next-square",
        "Next-square identity",
        "For every natural n, (n+1)^2 = n^2 + 2n + 1.",
        status="PROVED",
        claim_type="equality",
    )
    store.add_theorem(
        "demo-odd-sum",
        "Sum of odd numbers",
        "For every natural n, sum_{k=1}^n (2k-1) = n^2.",
        dependencies=["demo-next-square"],
        claim_type="equality",
    )
    return store


def _overnight_campaign(store, campaign_id, *, max_repair_cycles=1):
    profile = OVERNIGHT_PROFILE
    return CampaignStore(store).create(
        campaign_id,
        target_id="demo-odd-sum",
        profile=profile.name,
        max_repair_cycles=max_repair_cycles,
        infrastructure_retries=profile.infrastructure_retries,
        auto_successor=True,
        auto_dependency_repair=False,
        hard_blocker=True,
        budget_seconds=profile.budget_seconds,
        initial_workers=profile.initial_workers,
        max_workers=profile.max_workers,
        secondary_verification=True,
    )


def test_overnight_mock_campaign_hard_gate_and_secondary_complete(tmp_path):
    store = _demo_store(tmp_path)
    config = _mock_config(tmp_path / "config.json")
    _overnight_campaign(store, "campaign-1")
    result = CampaignEngine(
        store,
        config_path=config,
        worker_count=6,
    ).run("campaign-1")
    assert result["status"] == "COMPLETE_PROVED_REPLAY"
    run_id = result["runs"][0]["run_id"]
    run_dir = store.root / "runs" / run_id
    assert json.loads(
        (run_dir / "pre_submit_gate.json").read_text(encoding="utf-8")
    )["allowed"] is True
    secondary = json.loads(
        (run_dir / "secondary_verification" / "gate.json").read_text(
            encoding="utf-8"
        )
    )
    assert secondary["passed"] is True
    assert set(secondary["checks"]) == {
        "independent_reconstruction",
        "adversarial_review",
        "certificate_rerun",
        "dependency_coverage",
        "statement_scope_reconstruction",
    }
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["budget_limit_seconds"] == 12 * 60 * 60
    assignments = list((run_dir / "openprover" / "steps").glob("*/worker_assignments.json"))
    assert assignments
    roles = json.loads(assignments[0].read_text(encoding="utf-8"))["assignments"]
    assert len({item["role"] for item in roles}) == len(roles)


def test_secondary_failure_enters_bounded_repair_successor(tmp_path):
    store = _demo_store(tmp_path)
    config = _mock_config(tmp_path / "config.json")
    _overnight_campaign(store, "campaign-1", max_repair_cycles=1)

    class FailSecondaryOnce(ResearchOrchestrator):
        def _run_secondary_verification(self):
            if self.repair_cycle == 0:
                result = {
                    "schema_version": 1,
                    "passed": False,
                    "checks": {},
                    "failure_reasons": [
                        "secondary statement reconstruction found an omitted boundary"
                    ],
                    "execution_errors": [],
                    "inconclusive_checks": [],
                    "completed_at": "fixture",
                }
                directory = self.run_dir / "secondary_verification"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "gate.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
                return result
            return super()._run_secondary_verification()

    result = CampaignEngine(
        store,
        config_path=config,
        worker_count=6,
        orchestrator_factory=FailSecondaryOnce,
    ).run("campaign-1")
    assert result["status"] == "COMPLETE_PROVED_REPLAY"
    assert len(result["runs"]) == 2
    first = store.root / "runs" / result["runs"][0]["run_id"]
    assert (first / "FAILURE_MAP.json").is_file()
    assert result["runs"][1]["parent_run_id"] == result["runs"][0]["run_id"]
