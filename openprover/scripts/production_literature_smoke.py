"""Bounded public Literature smoke through the unmodified production handlers."""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

from openprover.math_research.campaign import CampaignEngine, CampaignStore
from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.project import ProjectStore, utc_now


QUERY = "Pythagorean theorem"
DOI = "10.1073/pnas.032677199"
PUBLIC_THEOREM_STATEMENT = (
    "The sums of the squares of the lengths of the projections of the elements "
    "of an orthonormal basis for a Hilbert space H onto an m-dimensional "
    "subspace of H is m."
)
CASES = {
    "positive": PUBLIC_THEOREM_STATEMENT,
    "negative-hypothesis": (
        "The sum of squared projection lengths of an arbitrary family in a "
        "Hilbert space H, without assuming an orthonormal basis, onto an "
        "m-dimensional subspace is m."
    ),
    "negative-direction": (
        "If the sum of squared projection lengths of a family onto every "
        "m-dimensional subspace is m, then the family is an orthonormal basis "
        "for the Hilbert space H."
    ),
}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ProductionLiteratureSmokeOrchestrator(ResearchOrchestrator):
    """Bound the campaign loop while retaining every production handler."""

    def run(self) -> dict:
        scheduler = self.pipeline_scheduler
        obligation_id = "applicability-positive"
        if obligation_id not in scheduler.snapshot()["obligations"]:
            scheduler.add_obligation(
                obligation_id,
                target_statement=CASES["positive"],
                current_tier="routine",
                fresh_independent_obligation=True,
                context={
                    "expected_theorem_label": "PROPOSITION 2",
                    "authorized_assumptions": [],
                },
            )
            scheduler.add_literature_request({
                "obligation_id": obligation_id,
                "requested_statement": "Locate the public Pythagorean theorem formulation",
                "why_needed": "bounded public authority-path validation",
                "blocking_or_nonblocking": "blocking",
                "expected_impact": "validate production literature reuse",
                "search_hints": {
                    "public_query": QUERY,
                    "strategy": "exact_theorem",
                    "doi": DOI,
                },
            })
        runtime = self.pipeline_runtime
        if runtime is None:
            raise RuntimeError("production pipeline runtime was not initialized")
        negative_created = False
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            runtime.start_window({"proof": 0, "literature": 2, "verification": 2})
            runtime.poll()
            snapshot = scheduler.snapshot()
            positive = snapshot["obligations"][obligation_id]
            if positive.get("status") == "CLOSED" and not negative_created:
                authority = copy.deepcopy(positive.get("authority_candidate") or {})
                authority["status"] = "VERIFIED_SOURCE_THEOREM"
                for case in ("negative-hypothesis", "negative-direction"):
                    scheduler.add_obligation(
                        f"applicability-{case}",
                        target_statement=CASES[case],
                        current_tier="routine",
                        fresh_independent_obligation=True,
                        context={"authorized_assumptions": []},
                    )
                    scheduler.reuse_verified_source_theorem(
                        f"applicability-{case}", authority,
                    )
                negative_created = True
                continue
            if negative_created and all(
                snapshot["obligations"][f"applicability-{case}"].get(
                    "applicability_status"
                ) in {"APPLICABILITY_REJECTED", "APPLICABILITY_UNCERTAIN"}
                for case in ("negative-hypothesis", "negative-direction")
            ):
                break
            if snapshot.get("resource_budget_hard_stop"):
                break
            relevant_ready = any(
                task.get("obligation_id", "").startswith("applicability-")
                and task.get("status") in {"READY", "RETRY_READY", "ACTIVE"}
                and task.get("pipeline") in {"literature", "verification"}
                for task in snapshot["tasks"].values()
            )
            if not runtime.pending() and not relevant_ready:
                break
            time.sleep(0.05)
        snapshot = scheduler.snapshot()
        obligation = snapshot["obligations"][obligation_id]
        status = "PROVED" if obligation.get("status") == "CLOSED" else "HUMAN_REQUIRED"
        self._checkpoint(
            "COMPLETE", status=status, completed_at=utc_now(),
            public_literature_smoke_obligation=obligation_id,
            production_literature_smoke_status=obligation.get("status"),
        )
        return self.state


def run(root: Path, base_config: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    config = json.loads(base_config.read_text(encoding="utf-8"))
    literature = config.setdefault("literature", {})
    literature["external_transmission_approved"] = False
    literature["external_public_search_approved"] = True
    literature["public_search_approval_source"] = "operator:bounded-phase-F-smoke"
    literature["scholarly_metadata_adapter"] = True
    literature["full_text_retrieval"] = True
    config["literature_budget"] = {
        "max_literature_calls": 8, "initial_literature_searchers": 1,
        "max_literature_searchers": 1, "max_reader_calls": 2,
        "max_deep_reads": 1, "max_citation_chain_depth": 0,
    }
    config["global_budget"] = {
        "provider_calls": 12, "input_tokens": 20000, "output_tokens": 10000,
        "reasoning_tokens": 10000, "cached_tokens": 20000, "total_tokens": 30000,
    }
    config["resource_estimates"] = {
        "input_tokens": 256, "output_tokens": 256, "reasoning_tokens": 0,
        "cached_tokens": 0, "unknown_usage_policy": "reserved_as_committed",
    }
    config_path = root / "public-smoke-config.json"
    _write(config_path, config)

    project = ProjectStore.initialize(root / "project", "production literature smoke", demo=True)
    project.add_theorem(
        "target", "Synthetic campaign target", "For every n, n = n.",
        status="OPEN", claim_type="equality",
    )
    store = CampaignStore(project)
    store.create("production-literature-smoke", target_id="target", initial_workers=2, max_workers=2)
    instances = []

    def factory(project_value, target_id, **kwargs):
        instance = ProductionLiteratureSmokeOrchestrator(project_value, target_id, **kwargs)
        instances.append(instance)
        return instance

    campaign = CampaignEngine(
        project, config_path=config_path, worker_count=2,
        orchestrator_factory=factory,
    ).run("production-literature-smoke")
    if not instances:
        raise RuntimeError("CampaignEngine did not construct ResearchOrchestrator")
    orchestrator = instances[-1]
    snapshot = orchestrator.pipeline_scheduler.snapshot()
    obligation = snapshot["obligations"]["applicability-positive"]
    tasks = [
        task for task in snapshot["tasks"].values()
        if task.get("obligation_id") == "applicability-positive"
    ]
    reader = next(task for task in tasks if task.get("role") in {"literature_reader", "literature_deep_reader"})
    searcher = next(task for task in tasks if task.get("role") == "literature_searcher")
    reconstruction = next(task for task in tasks if task.get("role") == "reconstruction")
    verifier = next(task for task in tasks if task.get("role") == "theorem_verifier")
    artifact = reader.get("result", {}).get("artifact", {})
    registry_path = orchestrator.run_dir / "literature" / "external_authority_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    authority_id = str((obligation.get("authority_candidate") or {}).get("authority_id") or "")
    authority = registry.get("source_theorems", {}).get(authority_id, {})
    phase_e["selected_extraction_id"] = authority.get("extraction_id")
    applicability_id = str(obligation.get("applicability_id") or "")
    applicability = registry.get("applicability_records", {}).get(applicability_id, {})
    raw_provider = next(
        (path for path in (orchestrator.run_dir / "literature" / "scholarly-cache" / "openalex").glob("*.json")),
        None,
    )
    if raw_provider is None:
        raise RuntimeError("OpenAlex raw response cache is missing")

    phase_d = {
        "phase": "D", "provider": "openalex", "query": QUERY,
        "project_context_transmitted": False,
        "search_task_id": searcher["task_id"],
        "search_payload": copy.deepcopy(searcher.get("payload")),
        "raw_provider_response_path": str(raw_provider),
        "source_ids": [source.get("source_id") for source in searcher.get("result", {}).get("sources", [])],
    }
    phase_e = {
        "phase": "E", "artifact_sha256": artifact.get("sha256"),
        "text_artifact_sha256": artifact.get("text_sha256"),
        "extraction_artifact_sha256": artifact.get("extraction_artifact_sha256"),
        "selected_extraction_id": (
            reader.get("result", {}).get("theorems", [{}])[0].get("extraction_id")
        ),
        "artifact_path": artifact.get("local_path"),
        "text_artifact_path": artifact.get("text_path"),
        "extraction_artifact_path": artifact.get("extraction_artifact_path"),
        "retrieval_status": reader.get("result", {}).get("retrieval_status"),
        "extraction_status": reader.get("result", {}).get("extraction_status"),
    }
    roles = [task.get("role") for task in tasks]
    components = {
        "NETWORK_DISCOVERY_PASS": searcher.get("result", {}).get("search_status") == "NETWORK_DISCOVERY_PASS",
        "PDF_RETRIEVAL_PASS": reader.get("result", {}).get("retrieval_status") == "PDF_RETRIEVAL_PASS",
        "THEOREM_EXTRACTION_PASS": reader.get("result", {}).get("extraction_status") == "THEOREM_EXTRACTION_PASS",
        "SOURCE_THEOREM_PROMOTION_PASS": authority.get("status") == "VERIFIED_SOURCE_THEOREM",
        "RECONSTRUCTION_PASS": reconstruction.get("result", {}).get("verdict") == "APPLICABILITY_CANDIDATE",
        "VERIFIER_PASS": verifier.get("result", {}).get("verdict") == "APPLICABLE",
        "APPLICABILITY_PROMOTION_PASS": applicability.get("status") == "APPLICABLE_EXTERNAL_AUTHORITY",
        "PRODUCTION_PIPELINE_PASS": obligation.get("status") == "CLOSED",
    }
    negative_cases = {}
    for case in ("negative-hypothesis", "negative-direction"):
        case_id = f"applicability-{case}"
        case_obligation = snapshot["obligations"][case_id]
        case_tasks = [task for task in snapshot["tasks"].values() if task.get("obligation_id") == case_id]
        case_reconstruction = next(task for task in case_tasks if task.get("role") == "reconstruction")
        case_verifier = next(task for task in case_tasks if task.get("role") == "theorem_verifier")
        case_app_id = str(case_obligation.get("applicability_id") or "")
        case_record = registry.get("applicability_records", {}).get(case_app_id, {})
        expected = "HYPOTHESIS_MISMATCH" if case == "negative-hypothesis" else "WRONG_DIRECTION"
        negative_cases[case] = {
            "obligation_id": case_id,
            "obligation_status": case_obligation.get("status"),
            "closed": case_obligation.get("status") == "CLOSED",
            "expected_verdict": expected,
            "actual_verdict": case_verifier.get("result", {}).get("verdict"),
            "applicability_id": case_app_id,
            "applicability_status": case_record.get("status"),
            "assumption_snapshot_hash": case_record.get("assumption_snapshot_hash"),
            "reconstruction_result_path": case_reconstruction.get("result", {}).get("result_artifact"),
            "verifier_result_path": case_verifier.get("result", {}).get("result_artifact"),
        }
    components["NEGATIVE_HYPOTHESIS_PASS"] = (
        negative_cases["negative-hypothesis"]["actual_verdict"] in {"HYPOTHESIS_MISMATCH", "NOT_APPLICABLE"}
        and not negative_cases["negative-hypothesis"]["closed"]
    )
    components["NEGATIVE_DIRECTION_PASS"] = (
        negative_cases["negative-direction"]["actual_verdict"] == "WRONG_DIRECTION"
        and not negative_cases["negative-direction"]["closed"]
    )
    phase_f = {
        "phase": "F", "authority_id": authority_id,
        "applicability_id": applicability_id,
        "assumption_snapshot_hash": applicability.get("assumption_snapshot_hash"),
        "obligation_status": obligation.get("status"),
        "campaign_status": campaign.get("status"),
        "components": components,
        "production_executor_class": "openprover.math_research.literature.LiteratureTaskExecutor",
        "smoke_local_handlers": False,
        "task_roles": roles,
        "registry_path": str(registry_path),
        "reconstruction_result_path": reconstruction.get("result", {}).get("result_artifact"),
        "verifier_result_path": verifier.get("result", {}).get("result_artifact"),
        "pipeline_state_path": str(orchestrator.run_dir / "pipeline_state.json"),
        "negative_cases": negative_cases,
        "run_dir": str(orchestrator.run_dir),
    }
    _write(root / "phase-D" / "result.json", phase_d)
    _write(root / "phase-E" / "result.json", phase_e)
    _write(root / "phase-F" / "result.json", phase_f)
    if not all(components.values()):
        raise RuntimeError(f"production literature smoke did not close: {components}")
    return {"phase_D": phase_d, "phase_E": phase_e, "phase_F": phase_f}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.config), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
