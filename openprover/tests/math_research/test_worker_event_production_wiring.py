from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from openprover.math_research.openprover_adapter import ResearchPolicy
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectStore
from openprover.math_research.schemas import (
    SchemaError,
    parse_worker_event_footer,
)


def _footer(event: str, *, verdict: str = "UNCERTAIN") -> str:
    return (
        "result body\n\n<!-- OPENPROVER_WORKER_EVENT\n"
        + json.dumps(
            {
                "event": event,
                "verdict": verdict,
                "failure_kind": "",
                "details": [],
                "progress_signals": [],
                "literature_request": None,
                "high_value": False,
            }
        )
        + "\n-->"
    )


def _custom_footer(**overrides) -> str:
    payload = {
        "event": "COMPLETED",
        "verdict": "UNCERTAIN",
        "failure_kind": "",
        "details": [],
        "progress_signals": [],
        "literature_request": None,
        "high_value": False,
    }
    payload.update(overrides)
    return "body\n<!-- OPENPROVER_WORKER_EVENT\n" + json.dumps(payload) + "\n-->"


def test_typed_footer_is_explicit_unique_and_strict():
    assert parse_worker_event_footer(_footer("NO_PROGRESS")).event.value == "NO_PROGRESS"
    with pytest.raises(SchemaError):
        parse_worker_event_footer("ordinary prose only")
    with pytest.raises(SchemaError):
        parse_worker_event_footer(_footer("COMPLETED") + "\n" + _footer("COMPLETED"))


def test_missing_sidecar_fails_closed_instead_of_completed(tmp_path: Path):
    missing = ResearchPolicy._load_worker_event(tmp_path / "event_0.json")
    assert missing.event.value == "ERROR"
    assert missing.failure_kind == "MISSING_TYPED_EVENT_SIDECAR"


def test_worker_and_verifier_sidecars_are_materialized_before_consumption(tmp_path: Path):
    policy = ResearchPolicy()
    step_dir = tmp_path / "step_001"
    plan = {"tasks": [{"description": "task"}]}
    policy.after_worker_batch(
        None,
        plan,
        step_dir,
        [{"result": _footer("FAILED_ROUTE"), "error": ""}],
    )
    policy.after_verifier_batch(
        None,
        plan,
        step_dir,
        {0: {"result": _footer("COMPLETED", verdict="FLAWED"), "error": ""}},
    )

    worker = json.loads((step_dir / "workers" / "event_0.json").read_text(encoding="utf-8"))
    verifier = json.loads(
        (step_dir / "workers" / "verifier_event_0.json").read_text(encoding="utf-8")
    )
    assert worker["event"] == "FAILED_ROUTE"
    assert verifier["verdict"] == "FLAWED"


def test_resumed_worker_sidecars_keep_original_artifact_index(tmp_path: Path):
    policy = ResearchPolicy()
    step_dir = tmp_path / "step_001"
    plan = {"tasks": [{"description": "retry", "_original_index": 4}]}
    policy.after_worker_batch(
        None,
        plan,
        step_dir,
        [{"result": _footer("COMPLETED", verdict="CORRECT"), "error": ""}],
    )
    policy.after_verifier_batch(
        None,
        plan,
        step_dir,
        {0: {"result": _footer("COMPLETED", verdict="CORRECT"), "error": ""}},
    )
    assert (step_dir / "workers" / "event_4.json").exists()
    assert (step_dir / "workers" / "verifier_event_4.json").exists()
    assert not (step_dir / "workers" / "event_0.json").exists()


def test_typed_events_drive_all_policy_consumers(tmp_path: Path):
    class Router:
        def __init__(self):
            self.failures = []
            self.disagreements = []
            self.promotions = []
            self.frontier = []

        def record_failure(self, obligation_id, failure_kind, detail):
            self.failures.append((obligation_id, failure_kind, detail))

        def record_verifier_disagreement(self, obligation_id, **verdicts):
            self.disagreements.append((obligation_id, verdicts))

        def promote_high_value(self, obligation_id, **flags):
            self.promotions.append((obligation_id, flags))

        def record_frontier_cycle(self, frontier_id, *, progress):
            self.frontier.append((frontier_id, progress))

    class Pipeline:
        def __init__(self):
            self.requests = []

        def add_literature_request(self, request):
            self.requests.append(request)

    class Prover:
        work_dir = tmp_path / "run"

    router = Router()
    pipeline = Pipeline()
    policy = ResearchPolicy(
        model_router=router,
        pipeline_scheduler=pipeline,
        root_obligation_id="root",
    )
    step_dir = tmp_path / "step_001"
    plan = {
        "tasks": [
            {"obligation_id": "O1", "branch_id": "main"},
            {"obligation_id": "O2", "branch_id": "side"},
        ]
    }
    policy.after_worker_batch(
        Prover(),
        plan,
        step_dir,
        [
            {
                "result": _custom_footer(
                    event="NO_PROGRESS",
                    verdict="CORRECT",
                    progress_signals=["PARAMETER_REDUCTION"],
                    literature_request={"requested_statement": "needed lemma"},
                    high_value=True,
                ),
                "error": "",
            },
            {
                "result": _custom_footer(
                    event="FAILED_ROUTE",
                    failure_kind="COUNTEREXAMPLE",
                ),
                "error": "",
            },
        ],
    )
    policy.after_verifier_batch(
        Prover(),
        plan,
        step_dir,
        {
            0: {"result": _custom_footer(verdict="FLAWED"), "error": ""},
            1: {"result": _custom_footer(verdict="UNCERTAIN"), "error": ""},
        },
    )
    policy.after_spawn(Prover(), plan, step_dir, "ok")

    assert pipeline.requests[0]["obligation_id"] == "O1"
    assert router.disagreements[0][0] == "O1"
    assert ("O1", "NO_PROGRESS", "typed_worker_event") in router.failures
    assert ("O2", "COUNTEREXAMPLE", "typed_worker_event") in router.failures
    assert router.promotions == [("O1", {"theorem_level": True})]
    assert router.frontier[0][0] == "root"
    assert router.frontier[0][1]["parameter_reduction"] is True


def test_t13_production_planner_worker_verifier_truth_mutation_e2e(tmp_path: Path):
    """Exercise the real deterministic production route, not the showcase replay."""

    repository_root = Path(__file__).resolve().parents[3]
    project_root = tmp_path / "project"
    shutil.copytree(repository_root / "projects" / "demo", project_root)
    theorem_path = project_root / "theorems" / "demo-odd-sum.json"
    theorem = json.loads(theorem_path.read_text(encoding="utf-8"))
    theorem.update(
        {
            "status": "OPEN",
            "proof_file": "",
            "last_run": "",
            "history": [],
        }
    )
    theorem_path.write_text(json.dumps(theorem, indent=2) + "\n", encoding="utf-8")

    store = ProjectStore(project_root)
    ResearchOrchestrator(
        store,
        "demo-odd-sum",
        config_path=repository_root / "configs" / "models.mock.json",
        worker_count=3,
        dry_run=False,
    ).run()

    run_dir = max(project_root.glob("runs/demo-odd-sum-*"), key=lambda path: path.stat().st_mtime)
    events = sorted(run_dir.glob("openprover/steps/*/workers/event_*.json"))
    verifier_events = sorted(run_dir.glob("openprover/steps/*/workers/verifier_event_*.json"))
    assert len(events) >= 3
    assert len(verifier_events) >= 3
    assert all(json.loads(path.read_text(encoding="utf-8"))["event"] != "ERROR" for path in events)
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["verdict"] == "CORRECT"
        for path in verifier_events
    )
    assert (run_dir / "CANDIDATE_PROOF.md").exists()
    assert (
        json.loads((run_dir / "audits" / "gate.json").read_text(encoding="utf-8"))["outcome"]
        == "PASS"
    )
    assert store.load_theorem("demo-odd-sum")["status"] == "PROVED"
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    mutation_id = state["truth_mutation_id"]
    intent_path = (
        project_root
        / "truth"
        / "mutations"
        / "intents"
        / (mutation_id.removeprefix("sha256:") + ".json")
    )
    receipt_path = (
        project_root
        / "truth"
        / "mutations"
        / "receipts"
        / (mutation_id.removeprefix("sha256:") + ".json")
    )
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert intent["claim_snapshot_hash"] == intent["audited_claim_snapshot_hash"]
    assert receipt["resulting_status"] == "PROVED"
    assert receipt["claim_snapshot_hash"] == intent["claim_snapshot_hash"]
