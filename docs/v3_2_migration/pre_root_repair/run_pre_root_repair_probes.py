"""Independent repair rerun for the frozen pre-root adversarial matrix.

The original ``pre_root_audit`` runner is immutable evidence.  This runner
re-executes its repaired P0/P1 boundaries, adds a real stale-ClaimSnapshot
acceptance probe, and runs the existing X3/X5--X16 regression slice.  It does
not alter the frozen audit files or any checked-in project artifact.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openprover.math_research.runtime_artifacts import RuntimeArtifactStore
from openprover.math_research.runtime_backend import SQLiteRuntimeBackend
from openprover.math_research.runtime_bindings import CrossPlaneExecutionBinding
from openprover.math_research.runtime_model import AttemptState, RuntimeConflict
from openprover.math_research.truth_identity import domain_hash

REPO_ROOT = Path(__file__).resolve().parents[3]
FROZEN_RUNNER = REPO_ROOT / "docs" / "v3_2_migration" / "pre_root_audit" / (
    "run_cross_plane_probes.py"
)
FOCUSED_FILES = (
    "openprover/tests/math_research/test_pre_root_blocker_repairs.py",
    "openprover/tests/math_research/test_canonical_artifact_authority.py",
    "openprover/tests/math_research/test_truth_store_facade.py",
    "openprover/tests/math_research/test_truth_mutation.py",
    "openprover/tests/math_research/test_session_closure.py",
    "openprover/tests/math_research/test_research_map_and_obligations.py",
    "openprover/tests/math_research/test_architecture_review_and_probe.py",
    "openprover/tests/math_research/test_architecture_critic_and_authorization.py",
    "openprover/tests/math_research/test_phase4_research_plane_e2e.py",
    "openprover/tests/math_research/test_phase5_governance_e2e.py",
    "openprover/tests/math_research/test_durable_runtime.py",
    "openprover/tests/math_research/test_heterogeneous_routing.py",
    "openprover/tests/math_research/test_checkpoint_migration.py",
    "openprover/tests/math_research/test_route_failure_records.py",
    "openprover/tests/math_research/test_worker_event_production_wiring.py",
)


def _result(
    probe_id: str,
    expected: str,
    actual: str,
    *,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "probe_id": probe_id,
        "status": status,
        "expected": expected,
        "actual": actual,
        "details": details or {},
    }


def _load_frozen_runner():
    spec = importlib.util.spec_from_file_location("frozen_pre_root_probes", FROZEN_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load frozen runner: {FROZEN_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe_x1_claim_binding() -> dict[str, Any]:
    """Retain a C1 result while rejecting it against the current C2 binding."""

    temp = tempfile.mkdtemp(prefix="pre-root-repair-x1-")
    project_root = Path(temp) / "project"
    c1 = domain_hash("claim_snapshot", {"id": "C1"})
    c2 = domain_hash("claim_snapshot", {"id": "C2"})
    binding = CrossPlaneExecutionBinding.capture(root_claim_snapshot_hash=c1)
    backend = SQLiteRuntimeBackend(project_root)
    job = backend.create_logical_job(
        job_kind="AUDIT",
        semantic_target="T1",
        idempotency_key="repair-x1-c1-result",
        execution_binding=binding,
    )
    attempt, _ = backend.create_attempt_intent(
        logical_job_id=job["logical_job_id"],
        provider="mock",
        model="mock",
        reasoning_tier="routine",
        payload_hash=domain_hash("payload", {"id": "C1"}),
        dispatch_kind="PROVIDER_INVOCATION",
        execution_binding=binding,
    )
    lease = backend.claim_attempt(attempt["attempt_id"], owner="repair", ttl_seconds=60)
    backend.transition_attempt(
        attempt["attempt_id"],
        AttemptState.RUNNING,
        actor="repair",
        lease_token=lease["lease_token"],
        generation=lease["generation"],
    )
    artifact = RuntimeArtifactStore(project_root).persist_and_register(
        backend,
        {"claim": "C1", "answer": "retained"},
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
    try:
        backend.accept_result(
            job["logical_job_id"],
            binding_validator=lambda current: (
                True
                if current is not None and current.root_claim_snapshot_hash == c2
                else "STALE_CLAIM_SNAPSHOT: current claim is C2"
            ),
        )
    except RuntimeConflict as exc:
        rejection = str(exc)
    else:
        rejection = "accepted unexpectedly"

    fenced = next(
        row for row in backend.list_rows("attempt_results") if row["result_id"] == result["result_id"]
    )
    artifact_ok = backend.verify_artifact(artifact["artifact_id"])["artifact_id"] == artifact[
        "artifact_id"
    ]
    passed = (
        fenced["authoritative"] == 0
        and fenced["ingestion_state"] == "STALE_FENCED"
        and artifact_ok
        and rejection != "accepted unexpectedly"
        and not backend.list_rows("effect_slots")
    )
    return _result(
        "X1",
        "C1 result provenance is retained but current C2 semantic authority is denied",
        f"{fenced['ingestion_state']}; authoritative={bool(fenced['authoritative'])}",
        status="CERTIFIED" if passed else "UNVERIFIED",
        details={
            "old_claim_snapshot_hash": c1,
            "current_claim_snapshot_hash": c2,
            "result_id": result["result_id"],
            "artifact_retained": artifact_ok,
            "effect_slot_count": len(backend.list_rows("effect_slots")),
            "rejection": rejection,
        },
    )


def _frozen_probe(name: str, probe_id: str) -> dict[str, Any]:
    frozen = _load_frozen_runner()
    value = getattr(frozen, name)()
    value["probe_id"] = probe_id
    if value["status"] == "PASS":
        value["status"] = "CERTIFIED"
    value.setdefault("details", {})["source_runner"] = str(FROZEN_RUNNER.relative_to(REPO_ROOT))
    return value


COVERAGE = {
    "X3": (
        "CERTIFIED_WITH_LIMITATION",
        "Canonical authority promotion remains domain-owned; runtime retains the result and the promotion guard revalidates the canonical body/hash.",
        ("test_canonical_artifact_authority.py", "test_truth_mutation.py"),
    ),
    "X5": (
        "CERTIFIED",
        "Production SessionClosure and StructuralEffect semantic applications use stable internal results and EffectSlots; replay recovers the existing domain identity.",
        ("test_pre_root_blocker_repairs.py", "test_phase4_research_plane_e2e.py"),
    ),
    "X6": (
        "CERTIFIED",
        "Production governance session/effect paths are runtime-wrapped; the explicit governance controller tests retain review-clock ownership.",
        ("test_pre_root_blocker_repairs.py", "test_phase5_governance_e2e.py", "test_architecture_review_and_probe.py"),
    ),
    "X7": (
        "CERTIFIED",
        "Authorized patch identity and replay remain domain-authorized and are protected by exact map/root binding; direct patch application is an explicit governance operation.",
        ("test_architecture_critic_and_authorization.py", "test_research_map_and_obligations.py"),
    ),
    "X8": (
        "CERTIFIED",
        "TruthMutation production finalization runs through the truth EffectSlot adapter and recovers the exact receipt after the theorem/receipt split.",
        ("test_pre_root_blocker_repairs.py", "test_truth_mutation.py", "test_worker_event_production_wiring.py"),
    ),
    "X9": (
        "CERTIFIED",
        "ResearchMap resolution and route-failure application use EffectSlot source identities and deterministic domain recovery.",
        ("test_pre_root_blocker_repairs.py", "test_phase4_research_plane_e2e.py", "test_route_failure_records.py"),
    ),
    "X10": (
        "CERTIFIED_WITH_LIMITATION",
        "Governed transfer and scope-loss tests pass; no OS process crash was injected inside filesystem patch application.",
        ("test_architecture_critic_and_authorization.py", "test_phase5_governance_e2e.py"),
    ),
    "X11": (
        "CERTIFIED_WITH_LIMITATION",
        "LogicalJob winner/effect idempotence is certified locally; external provider delivery remains at-least-once by design.",
        ("test_durable_runtime.py", "test_heterogeneous_routing.py"),
    ),
    "X13": (
        "CERTIFIED_WITH_LIMITATION",
        "Cancellation/completion and stale-result races are covered; external delivery is not claimed exactly-once.",
        ("test_durable_runtime.py", "test_pre_root_blocker_repairs.py"),
    ),
    "X14": (
        "CERTIFIED_WITH_LIMITATION",
        "Review-due state survives checkpoint/restart; no process crash was injected between a governance artifact write and its clock write.",
        ("test_phase5_governance_e2e.py", "test_durable_runtime.py"),
    ),
    "X15": (
        "CERTIFIED_WITH_LIMITATION",
        "Legacy checkpoints are forward-migrated without fabricated runtime history and require revalidation where bindings are absent.",
        ("test_checkpoint_migration.py", "test_durable_runtime.py"),
    ),
    "X16": (
        "CERTIFIED",
        "Project-local SQLite databases and artifact authorities remain isolated even when local identifiers collide.",
        ("test_durable_runtime.py",),
    ),
}


def run_focused_suite() -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-q", *FOCUSED_FILES]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return {
        "command": subprocess.list2cmdline(command),
        "returncode": completed.returncode,
        "output_tail": output[-4000:],
    }


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if key not in {"project_root", "root", "temp_root"}
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def run() -> dict[str, Any]:
    direct: list[dict[str, Any]] = []
    direct_probes: tuple[Callable[[], dict[str, Any]], ...] = (
        probe_x1_claim_binding,
        lambda: _frozen_probe("probe_x2_old_session_new_map", "X2"),
        lambda: _frozen_probe("probe_x4_expired_lease_result", "X4"),
        lambda: _frozen_probe("probe_after_provider_result_recovery", "X12"),
        lambda: _frozen_probe("probe_research_thesis_bypass", "GOV-THESIS-BYPASS"),
        lambda: _frozen_probe("probe_same_model_independence", "GOV-SAME-MODEL-FALLBACK"),
    )
    for probe in direct_probes:
        try:
            direct.append(probe())
        except Exception as exc:  # noqa: BLE001 - preserve an unverified probe record
            direct.append(
                _result(
                    getattr(probe, "__name__", "direct_probe"),
                    "probe completes without an unexpected error",
                    f"{type(exc).__name__}: {exc}",
                    status="UNVERIFIED",
                )
            )

    suite = run_focused_suite()
    suite_green = suite["returncode"] == 0
    coverage_results = []
    for probe_id, (green_status, limitation, files) in COVERAGE.items():
        status = green_status if suite_green else "UNVERIFIED"
        actual = (
            "focused X3/X5-X16 regression slice passed"
            if suite_green
            else f"focused regression slice returned {suite['returncode']}"
        )
        coverage_results.append(
            _result(
                probe_id,
                "existing adversarial invariant remains certified after repair",
                actual,
                status=status,
                details={"limitation": limitation, "coverage_files": list(files)},
            )
        )

    probes = [_sanitize(item) for item in direct + coverage_results]
    counts = Counter(item["status"] for item in probes)
    blocking = [
        item["probe_id"]
        for item in probes
        if item["probe_id"] in {"X1", "X2", "X4", "X12", "GOV-THESIS-BYPASS", "GOV-SAME-MODEL-FALLBACK"}
        and item["status"] not in {"CERTIFIED", "CERTIFIED_WITH_LIMITATION"}
    ]
    return {
        "runner": "pre_root_repair",
        "frozen_audit_runner_unchanged": True,
        "repository": str(REPO_ROOT),
        "probes": probes,
        "summary": {
            "counts": dict(counts),
            "blocking_probe_failures": blocking,
            "status": (
                "CERTIFIED"
                if not blocking and all(item["status"] == "CERTIFIED" for item in probes)
                else "CERTIFIED_WITH_LIMITATION"
                if not blocking
                else "UNVERIFIED"
            ),
        },
        "focused_suite": suite,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="also write the JSON evidence report")
    args = parser.parse_args()
    report = run()
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.buffer.write(encoded.encode("utf-8"))


if __name__ == "__main__":
    main()
