"""Adversarial reproductions for the v3.2 pre-root production blockers."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from math_research_agent.research.architecture_patch import PatchAuthorization
from math_research_agent.research.project import ProjectError, ProjectStore
from math_research_agent.research.orchestrator import ResearchOrchestrator
from math_research_agent.research.research_map import MapRevisionReason
from math_research_agent.research.research_store import ResearchStoreFacade
from math_research_agent.research.routing import ModelRouter, RoutedLLMClient
from math_research_agent.research.runtime_artifacts import RuntimeArtifactStore
from math_research_agent.research.runtime_backend import SQLiteRuntimeBackend
from math_research_agent.research.runtime_bindings import CrossPlaneExecutionBinding
from math_research_agent.research.runtime_dispatch import DurableProviderDispatcher
from math_research_agent.research.runtime_model import (
    AttemptState,
    RuntimeConflict,
    RuntimeResultRejected,
)
from math_research_agent.research.truth_identity import domain_hash
from math_research_agent.research.truth_store import TruthStoreFacade


def _research(tmp_path: Path):
    project = ProjectStore.initialize(tmp_path / "project", "authority repair")
    project.add_theorem("T1", "Root", "P holds for all n.")
    truth = TruthStoreFacade(project)
    snapshot = truth.capture_claim_snapshot("T1")
    store = ResearchStoreFacade(project, truth_store=truth)
    research_map = store.create_initial_map(
        research_map_id="map-T1",
        root_theorem_id="T1",
        root_claim_snapshot_hash=snapshot.claim_snapshot_hash,
        obligations=[
            {
                "obligation_id": "O1",
                "title": "O1",
                "statement": "Resolve O1",
                "obligation_kind": "LEMMA",
                "scope": ["O1"],
            }
        ],
        created_by="test",
        strategic_thesis="Original thesis",
    )
    return project, store, snapshot, research_map


def _forged_authorization(snapshot_hash: str, map_hash: str) -> PatchAuthorization:
    return PatchAuthorization.capture(
        patch_id="forged-patch",
        patch_hash=domain_hash("patch", {"id": "forged-patch"}),
        review_id="forged-review",
        review_hash=domain_hash("review", {"id": "forged-review"}),
        critic_id="forged-critic",
        critic_hash=domain_hash("critic", {"id": "forged-critic"}),
        probe_ids=(),
        probe_hashes=(),
        root_claim_snapshot_hash=snapshot_hash,
        source_map_hash=map_hash,
        status="AUTHORIZED",
        scope_validation_passed=False,
        truth_boundary_intact=False,
        invalidated_evidence_refs=(),
        reason="untrusted payload",
        authorized_by="untrusted-payload",
        created_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    "revision_kwargs",
    [
        {"strategic_thesis": "Forged thesis"},
        {
            "removed_or_reframed_scope": ("O1",),
            "revision_reason": MapRevisionReason.ARCHITECTURE_PATCH.value,
        },
    ],
)
def test_f005_forged_authorization_cannot_mutate_destructive_map(
    tmp_path: Path, revision_kwargs: dict
):
    _, store, snapshot, research_map = _research(tmp_path)
    authorization = _forged_authorization(
        snapshot.claim_snapshot_hash, research_map.research_map_hash
    )

    with pytest.raises(ProjectError, match="TRUSTED|durable|GOVERNANCE"):
        store.revise_map(
            research_map,
            created_by="untrusted-payload",
            revision_reason=revision_kwargs.pop(
                "revision_reason", MapRevisionReason.HUMAN_STEERING.value
            ),
            governance_authorization=authorization,
            **revision_kwargs,
        )

    assert store.load_current_map(research_map.research_map_id) == research_map


def _persisted_result(
    root: Path, *, claim_hash: str, tag: str = "result"
) -> tuple[SQLiteRuntimeBackend, dict, dict]:
    backend = SQLiteRuntimeBackend(root)
    binding = CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=claim_hash)
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="O1",
        idempotency_key=f"job-{tag}",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        model="mock",
        reasoning_tier="research",
        payload_hash=domain_hash("payload", {"tag": tag}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="test", ttl_seconds=60)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="test",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    artifact = RuntimeArtifactStore(root).persist_and_register(
        backend,
        {"structured": {"success": True}, "tag": tag},
        artifact_kind="PROVIDER_RESULT",
        producer_attempt_id=attempt["attempt_id"],
    )
    result = backend.record_result(
        attempt_id=attempt["attempt_id"],
        artifact_id=artifact["artifact_id"],
        completion_status="SUCCESS",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
        execution_binding=binding,
    )
    return backend, job, result


def test_f007_restart_does_not_accept_without_binding_validator(tmp_path: Path):
    claim_hash = domain_hash("claim", {"id": "C1"})
    backend, job, result = _persisted_result(tmp_path / "project", claim_hash=claim_hash)

    restarted = SQLiteRuntimeBackend(tmp_path / "project")
    restarted.reconcile()

    assert restarted.get_job(job["logical_job_id"])["accepted_result_id"] is None
    assert (
        next(
            row
            for row in restarted.list_rows("attempt_results")
            if row["result_id"] == result["result_id"]
        )["authoritative"]
        == 1
    )

    restarted.reconcile(
        binding_validator=lambda binding: (
            binding is not None and binding.root_claim_snapshot_hash == claim_hash
        )
    )
    assert restarted.get_job(job["logical_job_id"])["accepted_result_id"] == result["result_id"]


def test_f007_restart_fences_mismatched_binding(tmp_path: Path):
    c1 = domain_hash("claim", {"id": "C1"})
    c2 = domain_hash("claim", {"id": "C2"})
    backend, job, result = _persisted_result(tmp_path / "project", claim_hash=c1)
    assert backend.get_job(job["logical_job_id"])["accepted_result_id"] is None

    restarted = SQLiteRuntimeBackend(tmp_path / "project")
    restarted.reconcile(
        binding_validator=lambda binding: (
            True
            if binding is not None and binding.root_claim_snapshot_hash == c2
            else "STALE_CLAIM_SNAPSHOT: restart context changed"
        )
    )

    fenced = next(
        row
        for row in restarted.list_rows("attempt_results")
        if row["result_id"] == result["result_id"]
    )
    assert fenced["authoritative"] == 0
    assert fenced["ingestion_state"] == "STALE_FENCED"
    assert restarted.get_job(job["logical_job_id"])["accepted_result_id"] is None


def test_f007_configured_standalone_router_requires_binding(tmp_path: Path):
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    router = ModelRouter(
        {"provider": "mock", "model": "mock", "reasoning_effort": "low"},
        runtime_backend=backend,
        runtime_scope="standalone-test",
        require_execution_binding=True,
    )
    client = RoutedLLMClient(
        router,
        client_factory=lambda *args, **kwargs: object(),
        default_role="worker",
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "working",
    )

    with pytest.raises(RuntimeConflict, match="execution binding"):
        client.call("[Worker role: worker]", "system", label="worker")


def test_f007_configured_standalone_router_accepts_valid_binding(tmp_path: Path):
    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=domain_hash("claim", {"id": "standalone-valid"})
    )
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    router = ModelRouter(
        {"provider": "mock", "model": "mock", "reasoning_effort": "low"},
        runtime_backend=backend,
        runtime_scope="standalone-valid-test",
        execution_binding=binding,
        execution_binding_validator=lambda current: current == binding,
        require_execution_binding=True,
    )

    class ValidClient:
        def call(self, **_: object) -> dict:
            return {"structured": {"success": True}}

    client = RoutedLLMClient(
        router,
        client_factory=lambda *args, **kwargs: ValidClient(),
        default_role="worker",
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "working",
    )
    response = client.call("[Worker role: worker]", "system", label="worker")

    assert response["runtime"]["accepted"] is True
    assert next(row for row in backend.list_rows("attempt_results"))["authoritative"] == 1


def test_f007_current_domain_rejects_root_only_semantic_binding(tmp_path: Path):
    repository_root = Path(__file__).resolve().parents[2]
    project = ProjectStore.initialize(tmp_path / "project", "binding completeness")
    project.add_theorem("T1", "Root", "P holds.")
    orchestrator = ResearchOrchestrator(
        project,
        "T1",
        config_path=repository_root / "tests" / "fixtures" / "models.mock.toml",
        run_id="binding-completeness",
        worker_count=1,
    )
    orchestrator._ensure_research_plane_ready()
    current = orchestrator._current_execution_binding()
    assert current is not None and current.research_map_id is not None
    root_only = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=current.root_claim_snapshot_hash
    )

    assert orchestrator._validate_execution_binding(root_only) is not True

    class Provider:
        calls = 0

        def call(self, **_: object) -> dict:
            self.calls += 1
            return {"structured": {"success": True}}

        def cleanup(self) -> None:
            return None

    provider = Provider()
    client = RoutedLLMClient(
        orchestrator.model_router,
        client_factory=lambda *_args, **_kwargs: provider,
        default_role="worker",
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "working",
        execution_binding=root_only,
    )
    try:
        with pytest.raises(RuntimeConflict, match="binding rejected"):
            client.call("[Worker role: worker]", "system", label="root-only")
    finally:
        client.cleanup()
    assert provider.calls == 0


def test_nf004_no_backend_required_binding_rejects_before_provider(tmp_path: Path):
    class Provider:
        calls = 0

        def call(self, **_: object) -> dict:
            self.calls += 1
            return {"structured": {"success": True}}

        def cleanup(self) -> None:
            return None

    provider = Provider()
    router = ModelRouter(
        {"provider": "mock", "model": "mock", "reasoning_effort": "low"},
        require_execution_binding=True,
    )
    client = RoutedLLMClient(
        router,
        client_factory=lambda *_args, **_kwargs: provider,
        default_role="worker",
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "working",
    )
    try:
        with pytest.raises(RuntimeConflict, match="trusted execution binding"):
            client.call("[Worker role: worker]", "system", label="no-backend")
    finally:
        client.cleanup()
    assert provider.calls == 0


def test_f002_routed_client_cannot_parse_late_rejected_payload(tmp_path: Path, monkeypatch):
    import math_research_agent.research.runtime_dispatch as dispatch_module

    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=domain_hash("claim", {"id": "routed-late"})
    )
    backend = SQLiteRuntimeBackend(tmp_path / "project")
    router = ModelRouter(
        {"provider": "mock", "model": "mock", "reasoning_effort": "low"},
        runtime_backend=backend,
        runtime_scope="routed-late-test",
        execution_binding=binding,
        execution_binding_validator=lambda current: current == binding,
        require_execution_binding=True,
    )

    class SlowClient:
        def call(self, **_: object) -> dict:
            time.sleep(0.02)
            return {"structured": {"success": True, "high_value": True}}

    class SlowDispatcher(DurableProviderDispatcher):
        def __init__(self, runtime_backend):
            super().__init__(runtime_backend, lease_ttl_seconds=0.001)

    monkeypatch.setattr(dispatch_module, "DurableProviderDispatcher", SlowDispatcher)
    client = RoutedLLMClient(
        router,
        client_factory=lambda *args, **kwargs: SlowClient(),
        default_role="worker",
        archive_dir=tmp_path / "archive",
        working_dir=tmp_path / "working",
    )

    with pytest.raises(RuntimeResultRejected, match="terminal"):
        client.call("[Worker role: worker]", "system", label="worker")

    assert (
        backend.get_job(backend.list_rows("logical_jobs")[0]["logical_job_id"])[
            "accepted_result_id"
        ]
        is None
    )


def test_f002_rejected_provider_payload_is_terminal_and_non_consumable(tmp_path: Path):
    root = tmp_path / "project"
    claim_hash = domain_hash("claim", {"id": "C1"})
    binding = CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=claim_hash)
    backend = SQLiteRuntimeBackend(root)
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="O1",
        idempotency_key="late-result",
        execution_binding=binding,
    )
    response = DurableProviderDispatcher(backend, lease_ttl_seconds=0.001).execute(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        model="mock",
        reasoning_tier="research",
        payload={"prompt": "late"},
        invoke=lambda: time.sleep(0.02) or {"structured": {"success": True}},
        execution_binding=binding,
        binding_validator=lambda current: current == binding,
    )

    assert response["runtime"]["authoritative"] is False
    assert response["runtime"]["accepted"] is False
    assert "structured" not in response
    assert backend.get_job(job["logical_job_id"])["accepted_result_id"] is None

    fresh = SQLiteRuntimeBackend(root)
    fresh.reconcile(binding_validator=lambda current: current == binding)
    assert fresh.get_job(job["logical_job_id"])["accepted_result_id"] is None
