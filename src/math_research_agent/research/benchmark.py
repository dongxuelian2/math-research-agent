"""Measured Gemini benchmark runner with no synthetic accuracy reporting."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from .orchestrator import ResearchOrchestrator
from .project import ProjectError, ProjectStore


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Unable to read benchmark manifest {path}: {exc}") from exc
    if value.get("schema_version") != 1:
        raise ProjectError("Benchmark manifest must use schema_version 1")
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProjectError("Benchmark manifest must contain a non-empty cases array")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ProjectError("Benchmark case must be an object")
        required = {"id", "title", "statement", "claim_type", "difficulty"}
        missing = required - set(case)
        if missing:
            raise ProjectError("Benchmark case missing fields: " + ", ".join(sorted(missing)))
        case_id = str(case["id"])
        if case_id in seen:
            raise ProjectError(f"Duplicate benchmark case: {case_id}")
        seen.add(case_id)
        if case["claim_type"] not in {
            "implication",
            "iff",
            "classification",
            "equality",
        }:
            raise ProjectError(f"Unsupported claim type in benchmark case {case_id}")
    return cases


def run_benchmark(
    *,
    manifest_path: str | Path,
    config_path: str | Path,
    output_path: str | Path,
    worker_count: int = 3,
    budget_limit_seconds: int | None = None,
    max_cases: int | None = None,
    secondary_verification: bool = True,
) -> dict[str, Any]:
    """Run each case as a fresh project and archive only observed outcomes."""

    cases = _read_manifest(Path(manifest_path).resolve())
    if max_cases is not None:
        if max_cases < 1:
            raise ProjectError("max_cases must be positive")
        cases = cases[:max_cases]
    output = Path(output_path).resolve()
    if (output / "summary.json").exists():
        raise ProjectError(f"Benchmark output already exists: {output}; choose a new directory")
    projects = output / "projects"
    projects.mkdir(parents=True, exist_ok=False)
    results_path = output / "results.jsonl"
    records: list[dict[str, Any]] = []

    with results_path.open("w", encoding="utf-8") as stream:
        for case in cases:
            case_id = str(case["id"])
            case_project = projects / case_id
            started = time.perf_counter()
            record: dict[str, Any] = {
                "schema_version": 1,
                "case_id": case_id,
                "title": str(case["title"]),
                "difficulty": str(case["difficulty"]),
                "started_at": time.time(),
            }
            try:
                project = ProjectStore.initialize(
                    case_project,
                    f"Gemini benchmark: {case['title']}",
                    project_id=f"benchmark-{case_id}",
                    demo=False,
                )
                project.add_theorem(
                    case_id,
                    str(case["title"]),
                    str(case["statement"]),
                    status="OPEN",
                    claim_type=str(case["claim_type"]),
                    tags=["benchmark", "gemini-observatory", str(case["difficulty"])],
                )
                orchestrator = ResearchOrchestrator(
                    project,
                    case_id,
                    config_path=config_path,
                    worker_count=worker_count,
                    budget_limit_seconds=budget_limit_seconds,
                    role_scheduling=True,
                    secondary_verification=secondary_verification,
                    hard_submit_gate=False,
                )
                try:
                    state = orchestrator.run()
                finally:
                    orchestrator.close()
                record.update(
                    {
                        "status": state.get("status"),
                        "phase": state.get("phase"),
                        "theorem_status": project.load_theorem(case_id).get("status"),
                        "run_id": state.get("run_id"),
                        "run_dir": str(orchestrator.run_dir),
                        "metrics": state.get("metrics", {}),
                    }
                )
            except Exception as exc:
                record.update(
                    {
                        "status": "ERROR",
                        "phase": "ERROR",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            records.append(record)
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()

    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "schema_version": 1,
        "manifest": str(Path(manifest_path).resolve()),
        "config": str(Path(config_path).resolve()),
        "case_count": len(records),
        "observed_status_counts": counts,
        "results": str(results_path),
        "note": "Counts are measured from this run; no comparison metrics are fabricated.",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="math-research benchmark")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--config", default="configs/models.toml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--budget-seconds", type=int)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument(
        "--secondary-verification",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            run_benchmark(
                manifest_path=args.manifest,
                config_path=args.config,
                output_path=args.output,
                worker_count=args.workers,
                budget_limit_seconds=args.budget_seconds,
                max_cases=args.max_cases,
                secondary_verification=args.secondary_verification,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
