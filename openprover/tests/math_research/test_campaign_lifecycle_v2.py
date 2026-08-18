import hashlib
import json

import pytest

from openprover.math_research.campaign import (
    CampaignEngine,
    CampaignStore,
    FailureMap,
    ReplayPolicy,
)
from openprover.math_research.codex_cli_provider import CodexCLIProviderError
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectError, ProjectStore
from openprover.math_research.state_machine import AuditGate


def _store(tmp_path):
    store = ProjectStore.initialize(tmp_path / "project", "Campaign fixture", demo=True)
    store.add_theorem(
        "target",
        "Target",
        "For all n, n = n.",
        status="OPEN",
        claim_type="equality",
    )
    return store


def _config(path):
    path.write_text(json.dumps({
        "schema_version": 1,
        "isolation": True,
        "budget": {"mode": "time", "limit": 1, "conclude_after": 0.5},
        "roles": {
            "planner": {"provider": "mock", "model": "mock-planner"},
            "worker": {"provider": "mock", "model": "mock-worker"},
            "cheap_auditor": {"provider": "mock", "model": "mock-auditor"},
            "final_auditor": {"provider": "mock", "model": "mock-final"},
        },
    }), encoding="utf-8")
    return path


def test_failure_map_writes_machine_and_human_artifacts(tmp_path):
    gate = AuditGate(
        failure_reasons=[
            "dependency authority SEM-X is unavailable",
            "boundary endpoint n=0 was omitted",
        ],
    )
    failure_map = FailureMap.from_gate(
        run_id="run-1",
        target_id="target",
        gate=gate,
        audits={
            "dependency_auditor": {
                "execution_status": "OK",
                "failure_reasons": ["dependency authority SEM-X is unavailable"],
            },
            "boundary_auditor": {
                "execution_status": "OK",
                "failure_reasons": ["boundary endpoint n=0 was omitted"],
            },
        },
    )
    json_path, md_path = failure_map.write(tmp_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert {item["category"] for item in data["items"]} == {
        "SEMANTIC_GAP",
        "BOUNDARY_GAP",
    }
    assert "authority_expected" in data["items"][0]
    assert "Repair:" in md_path.read_text(encoding="utf-8")


def test_successor_links_parent_and_cannot_duplicate(tmp_path):
    store = _store(tmp_path)
    campaigns = CampaignStore(store)
    campaigns.create(
        "campaign-1",
        target_id="target",
        max_repair_cycles=4,
        auto_successor=True,
    )
    campaigns.register_initial("campaign-1", "target-run-1")
    campaigns.mark_run("campaign-1", "target-run-1", status="REJECTED")
    child = campaigns.create_successor(
        "campaign-1", parent_run_id="target-run-1"
    )
    assert child["parent_run_id"] == "target-run-1"
    assert child["repair_cycle"] == 1
    with pytest.raises(ProjectError, match="Successor already exists"):
        campaigns.create_successor("campaign-1", parent_run_id="target-run-1")


def test_completed_campaign_run_record_is_immutable(tmp_path):
    store = _store(tmp_path)
    campaigns = CampaignStore(store)
    campaigns.create("campaign-1", target_id="target")
    campaigns.register_initial("campaign-1", "target-run-1")
    campaigns.mark_run("campaign-1", "target-run-1", status="PROVED")
    before = campaigns.path("campaign-1").read_bytes()
    campaigns.mark_run("campaign-1", "target-run-1", status="PROVED")
    assert campaigns.path("campaign-1").read_bytes() == before
    with pytest.raises(ProjectError, match="immutable"):
        campaigns.mark_run("campaign-1", "target-run-1", status="REJECTED")


def test_replay_policy_is_inherited_by_hash(tmp_path):
    store = _store(tmp_path)
    policy = ReplayPolicy(
        allowed_sources=("safe/gp3.md",),
        forbidden_sources=("hidden/answer.md",),
    )
    campaigns = CampaignStore(store)
    campaigns.create(
        "campaign-1",
        target_id="target",
        max_repair_cycles=1,
        auto_successor=True,
        replay_policy=policy,
    )
    campaigns.register_initial("campaign-1", "target-run-1")
    campaigns.mark_run("campaign-1", "target-run-1", status="REJECTED")
    child = campaigns.create_successor("campaign-1", parent_run_id="target-run-1")
    assert child["inheritance"]["replay_policy_hash"] == policy.policy_hash
    restored = ReplayPolicy.from_dict(campaigns.load("campaign-1")["replay_policy"])
    assert restored.to_dict() == policy.to_dict()


def test_mock_campaign_dependency_fail_repair_successor_then_pass(tmp_path):
    store = _store(tmp_path)
    config = _config(tmp_path / "config.json")
    scripted = iter(["REJECTED", "PROVED"])
    calls = []

    class ScriptedOrchestrator:
        def __init__(self, _project, _target, **kwargs):
            self.run_id = kwargs["run_id"]
            self.parent = kwargs["parent_run_id"]
            calls.append((self.run_id, self.parent))

        def run(self):
            status = next(scripted)
            return {"run_id": self.run_id, "phase": "COMPLETE", "status": status}

    campaigns = CampaignStore(store)
    campaigns.create(
        "campaign-1",
        target_id="target",
        profile="test-long-horizon",
        max_repair_cycles=4,
        auto_successor=True,
    )
    result = CampaignEngine(
        store,
        config_path=config,
        worker_count=2,
        orchestrator_factory=ScriptedOrchestrator,
    ).run("campaign-1")
    assert result["status"] == "COMPLETE_PROVED_REPLAY"
    assert len(result["runs"]) == 2
    assert calls[1][1] == calls[0][0]
    assert result["runs"][1]["repair_cycle"] == 1


def test_max_repair_cycles_ends_in_mathematical_exhaustion(tmp_path):
    store = _store(tmp_path)
    config = _config(tmp_path / "config.json")

    class RejectingOrchestrator:
        def __init__(self, _project, _target, **kwargs):
            self.run_id = kwargs["run_id"]

        def run(self):
            return {"run_id": self.run_id, "phase": "COMPLETE", "status": "REJECTED"}

    campaigns = CampaignStore(store)
    campaigns.create(
        "campaign-1",
        target_id="target",
        max_repair_cycles=2,
        auto_successor=True,
    )
    result = CampaignEngine(
        store,
        config_path=config,
        worker_count=2,
        orchestrator_factory=RejectingOrchestrator,
    ).run("campaign-1")
    assert result["status"] == "MATHEMATICAL_EXHAUSTION"
    assert result["repair_cycles_used"] == 2
    assert len(result["runs"]) == 3


def test_campaign_materializes_only_manifest_approved_dependency_repair(tmp_path):
    store = _store(tmp_path)
    config = _config(tmp_path / "config.json")
    source_root = tmp_path / "historical"
    (source_root / "safe").mkdir(parents=True)
    (source_root / "safe" / "gp3.md").write_text(
        "GP3 semantic source, not an answer file.", encoding="utf-8"
    )
    scripted = iter(["REJECTED", "PROVED"])

    class RepairFixtureOrchestrator:
        def __init__(self, project, _target, **kwargs):
            self.project = project
            self.run_id = kwargs["run_id"]

        def run(self):
            status = next(scripted)
            if status == "REJECTED":
                run_dir = self.project.root / "runs" / self.run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "FAILURE_MAP.json").write_text(json.dumps({
                    "items": [{
                        "category": "SEMANTIC_GAP",
                        "exact_rejected_claim": "MISSING_AUTHORITY SEM-G-PRIM-01",
                    }],
                }), encoding="utf-8")
            return {"run_id": self.run_id, "phase": "COMPLETE", "status": status}

    policy = ReplayPolicy(
        allowed_sources=("safe/gp3.md",),
        allowed_authority_ids=("SEM-G-PRIM-01",),
        approved_historical_authorities=(("SEM-G-PRIM-01", "safe/gp3.md"),),
        target_cutoff="2026-01-02T00:00:00+00:00",
    )
    campaigns = CampaignStore(store)
    campaigns.create(
        "campaign-1",
        target_id="target",
        max_repair_cycles=1,
        auto_successor=True,
        auto_dependency_repair=True,
        replay_policy=policy,
        dependency_repair_source_root=source_root,
        dependency_repair_catalog={
            "SEM-G-PRIM-01": {
                "authority_type": "semantic",
                "source_file": "safe/gp3.md",
                "source_created_at": "2026-01-01T00:00:00+00:00",
                "identity_verified": True,
                "leak_audit_pass": True,
            }
        },
    )
    result = CampaignEngine(
        store,
        config_path=config,
        worker_count=2,
        orchestrator_factory=RepairFixtureOrchestrator,
    ).run("campaign-1")
    child = result["runs"][1]
    repair_dir = store.root / "runs" / child["run_id"]
    repair = json.loads(
        (repair_dir / "dependency_repair.json").read_text(encoding="utf-8")
    )
    assert repair["leak_audit_pass"] is True
    assert [item["authority_id"] for item in repair["materialized_authorities"]] == [
        "SEM-G-PRIM-01"
    ]
    assert (repair_dir / "inherited_sources" / "safe" / "gp3.md").is_file()


def test_time_exhaustion_after_blocked_submit_checkpoints_without_candidate(tmp_path):
    store = _store(tmp_path)
    config = _config(tmp_path / "config.json")

    class BlockedNoCandidate(ResearchOrchestrator):
        def _run_openprover_candidate(self):
            (self.run_dir / "pre_submit_gate.json").write_text(
                json.dumps({
                    "allowed": False,
                    "blockers": [{"type": "SCOPE_GAP", "detail": "open branch"}],
                }),
                encoding="utf-8",
            )

    orchestrator = BlockedNoCandidate(
        store,
        "target",
        config_path=config,
        worker_count=2,
        campaign_id="campaign-1",
        run_id="target-run-1",
        hard_submit_gate=True,
    )
    state = orchestrator.run()
    assert state["phase"] == "CHECKPOINT"
    assert state["status"] == "TIME_BUDGET_EXHAUSTED"
    assert state["pre_submit_gate"]["allowed"] is False
    assert not (orchestrator.run_dir / "CANDIDATE_PROOF.md").exists()
    assert not (orchestrator.run_dir / "openprover" / "PROOF.md").exists()


def test_provider_quota_becomes_resumable_checkpoint_not_rejection(tmp_path):
    store = _store(tmp_path)
    config = _config(tmp_path / "config.json")

    class QuotaBlocked(ResearchOrchestrator):
        def _run_openprover_candidate(self):
            raise CodexCLIProviderError(
                error_type="usage_limit_reached",
                role="planner",
                model="mock",
                reasoning_effort="high",
                executable="codex",
                status=1,
                retry_count=0,
                retryable=False,
                retry_exhausted=False,
                human_explanation="usage limit reached",
            )

    orchestrator = QuotaBlocked(
        store,
        "target",
        config_path=config,
        worker_count=2,
        campaign_id="campaign-1",
        run_id="target-run-1",
    )
    state = orchestrator.run()
    assert state["phase"] == "CHECKPOINT"
    assert state["status"] == "BLOCKED_PROVIDER_QUOTA"
    assert store.load_theorem("target")["status"] == "IN_RESEARCH"


def test_campaign_exposes_quota_status_and_can_resume(tmp_path):
    store = _store(tmp_path)
    config = _config(tmp_path / "config.json")

    class QuotaCheckpoint:
        def __init__(self, _project, _target, **kwargs):
            self.run_id = kwargs["run_id"]

        def run(self):
            return {
                "run_id": self.run_id,
                "phase": "CHECKPOINT",
                "status": "BLOCKED_PROVIDER_QUOTA",
            }

    campaigns = CampaignStore(store)
    campaigns.create("campaign-1", target_id="target")
    result = CampaignEngine(
        store,
        config_path=config,
        worker_count=2,
        orchestrator_factory=QuotaCheckpoint,
    ).run("campaign-1")
    assert result["status"] == "BLOCKED_PROVIDER_QUOTA"
    resumed = campaigns.resume("campaign-1")
    assert resumed["status"] == "RUNNING"


def test_complete_run_state_is_byte_immutable_on_resume(tmp_path):
    store = _store(tmp_path)
    config = _config(tmp_path / "config.json")
    run_dir = store.root / "runs" / "target-complete"
    run_dir.mkdir(parents=True)
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "target-complete",
        "target_id": "target",
        "phase": "COMPLETE",
        "status": "REJECTED",
        "campaign_id": "campaign-1",
        "metrics": {},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = hashlib.sha256(state_path.read_bytes()).hexdigest()
    state = ResearchOrchestrator(
        store,
        "target",
        config_path=config,
        worker_count=2,
        campaign_id="campaign-1",
        run_id="target-complete",
    ).run()
    after = hashlib.sha256(state_path.read_bytes()).hexdigest()
    assert state["status"] == "REJECTED"
    assert after == before
