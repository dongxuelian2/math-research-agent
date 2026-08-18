"""Build a self-contained pilot-readiness evidence directory and ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from readiness_audit import audit


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_file(source: Path, destination: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def copy_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def evidence_entry(root: Path, path: Path) -> dict:
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


def phase_hashes(root: Path, phase: str) -> None:
    phase_root = root / "validation-evidence" / f"phase-{phase}"
    files = []
    for path in sorted(phase_root.rglob("*")):
        if path.is_file() and path.name != "hashes.json":
            files.append(evidence_entry(root, path))
    write_json(phase_root / "hashes.json", {"phase": phase, "files": files})


def build(args) -> tuple[Path, Path, dict]:
    repo = args.repo.resolve()
    system_root = args.system_root.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite archive staging directory: {output}")
    output.mkdir(parents=True)

    sources = [
        "openprover/math_research/__init__.py",
        "openprover/math_research/routing.py",
        "openprover/math_research/pipelines.py",
        "openprover/math_research/literature.py",
        "openprover/math_research/orchestrator.py",
        "openprover/math_research/scheduler.py",
        "openprover/math_research/campaign.py",
        "openprover/math_research/certification.py",
        "openprover/math_research/providers.py",
        "openprover/math_research/codex_cli_provider.py",
        "openprover/math_research/scholarly.py",
        "scripts/live_provider_validation.py",
        "scripts/heterogeneous_mini_smoke.py",
        "scripts/production_literature_smoke.py",
        "scripts/readiness_audit.py",
        "scripts/build_pilot_readiness_archive.py",
    ]
    tests = [
        "tests/math_research/test_heterogeneous_routing.py",
        "tests/math_research/test_async_literature_pipelines.py",
        "tests/math_research/test_production_async_integration.py",
        "tests/math_research/test_literature_trust.py",
        "tests/math_research/test_scholarly_adapter.py",
        "tests/math_research/test_heterogeneous_config_resume.py",
        "tests/math_research/test_pilot_readiness_repairs.py",
        "tests/math_research/test_external_applicability_trust.py",
    ]
    docs = [
        "docs/HETEROGENEOUS_ROUTING_AND_LITERATURE.md",
        "docs/LIVE_PROVIDER_AND_LITERATURE_VALIDATION.md",
        "docs/PILOT_READINESS_EVIDENCE.md",
    ]
    for relative in sources:
        copy_file(repo / relative, output / "source" / relative)
    for relative in tests:
        copy_file(repo / relative, output / relative)
    for relative in docs:
        copy_file(repo / relative, output / relative)
    config_source = system_root / "configs" / "models.heterogeneous.example.json"
    config_target = copy_file(
        config_source, output / "configs" / "models.heterogeneous.example.json"
    )

    evidence: dict[str, dict] = {}
    phase_a_result = copy_file(
        args.phase_a / "phase-A.json",
        output / "validation-evidence" / "phase-A" / "result.json",
    )
    evidence["phase_A_result"] = evidence_entry(output, phase_a_result)
    copy_file(
        args.phase_a / "pipeline_state.json",
        output / "validation-evidence" / "phase-A" / "artifacts" / "pipeline_state.json",
    )
    copy_tree(
        args.phase_a / "archive",
        output / "validation-evidence" / "phase-A" / "logs" / "provider-calls",
    )

    phase_b_result = copy_file(
        args.phase_b / "phase-B.json",
        output / "validation-evidence" / "phase-B" / "result.json",
    )
    evidence["phase_B_result"] = evidence_entry(output, phase_b_result)
    copy_tree(
        args.phase_b / "archive",
        output / "validation-evidence" / "phase-B" / "logs" / "provider-call",
    )
    for name in ("account-call.md", "account-call.raw.json"):
        source = args.phase_b / name
        if source.is_file():
            copy_file(
                source,
                output / "validation-evidence" / "phase-B" / "logs" / name,
            )
    catalog_target = copy_file(
        args.catalog,
        output / "validation-evidence" / "phase-B" / "artifacts" / "models_cache.json",
    )
    evidence["model_catalog"] = evidence_entry(output, catalog_target)

    phase_c_result = copy_file(
        args.phase_c / "phase-C.json",
        output / "validation-evidence" / "phase-C" / "result.json",
    )
    evidence["phase_C_result"] = evidence_entry(output, phase_c_result)
    copy_tree(
        args.phase_c / "project",
        output / "validation-evidence" / "phase-C" / "artifacts" / "project",
    )

    phase_d_source = args.phase_def / "phase-D" / "result.json"
    phase_e_source = args.phase_def / "phase-E" / "result.json"
    phase_f_source = args.phase_def / "phase-F" / "result.json"
    raw_d = json.loads(phase_d_source.read_text(encoding="utf-8"))
    raw_e = json.loads(phase_e_source.read_text(encoding="utf-8"))
    raw_f = json.loads(phase_f_source.read_text(encoding="utf-8"))
    phase_d_result = copy_file(
        phase_d_source, output / "validation-evidence" / "phase-D" / "result.json"
    )
    evidence["phase_D_result"] = evidence_entry(output, phase_d_result)
    openalex_target = copy_file(
        Path(raw_d["raw_provider_response_path"]),
        output / "validation-evidence" / "phase-D" / "artifacts" / "openalex-response.json",
    )
    evidence["openalex_response"] = evidence_entry(output, openalex_target)

    phase_e_result = copy_file(
        phase_e_source, output / "validation-evidence" / "phase-E" / "result.json"
    )
    evidence["phase_E_result"] = evidence_entry(output, phase_e_result)
    pdf_target = copy_file(
        Path(raw_e["artifact_path"]),
        output / "validation-evidence" / "phase-E" / "artifacts" / "source.pdf",
    )
    text_target = copy_file(
        Path(raw_e["text_artifact_path"]),
        output / "validation-evidence" / "phase-E" / "artifacts" / "source.txt",
    )
    extraction_target = copy_file(
        Path(raw_e["extraction_artifact_path"]),
        output / "validation-evidence" / "phase-E" / "artifacts" / "THEOREM_EXTRACTION.json",
    )
    evidence["pdf_artifact"] = evidence_entry(output, pdf_target)
    evidence["text_artifact"] = evidence_entry(output, text_target)
    evidence["extraction_artifact"] = evidence_entry(output, extraction_target)

    phase_f_result = copy_file(
        phase_f_source, output / "validation-evidence" / "phase-F" / "result.json"
    )
    evidence["phase_F_result"] = evidence_entry(output, phase_f_result)
    registry_target = copy_file(
        Path(raw_f["registry_path"]),
        output / "validation-evidence" / "phase-F" / "artifacts" / "external_authority_registry.json",
    )
    reconstruction_target = copy_file(
        Path(raw_f["reconstruction_result_path"]),
        output / "validation-evidence" / "phase-F" / "artifacts" / "reconstruction.json",
    )
    verifier_target = copy_file(
        Path(raw_f["verifier_result_path"]),
        output / "validation-evidence" / "phase-F" / "artifacts" / "theorem_verifier.json",
    )
    copy_file(
        Path(raw_f["pipeline_state_path"]),
        output / "validation-evidence" / "phase-F" / "artifacts" / "pipeline_state.json",
    )
    evidence["authority_registry"] = evidence_entry(output, registry_target)
    evidence["reconstruction_result"] = evidence_entry(output, reconstruction_target)
    evidence["verifier_result"] = evidence_entry(output, verifier_target)

    applicability_root = output / "validation-evidence" / "applicability"
    registry_value = json.loads(Path(raw_f["registry_path"]).read_text(encoding="utf-8"))
    source_theorem = (registry_value.get("source_theorems") or {}).get(raw_f["authority_id"], {})
    for case, destination in (
        ("positive", applicability_root / "positive"),
        ("negative-hypothesis", applicability_root / "negative-hypothesis"),
        ("negative-direction", applicability_root / "negative-direction"),
    ):
        destination.mkdir(parents=True, exist_ok=True)
        if case == "positive":
            reconstruction_source = Path(raw_f["reconstruction_result_path"])
            verifier_source = Path(raw_f["verifier_result_path"])
            app_id = raw_f["applicability_id"]
        else:
            case_value = raw_f["negative_cases"][case]
            reconstruction_source = Path(case_value["reconstruction_result_path"])
            verifier_source = Path(case_value["verifier_result_path"])
            app_id = case_value["applicability_id"]
        reconstruction_copy = copy_file(
            reconstruction_source, destination / "EXTERNAL_AUTHORITY_RECONSTRUCTION.json"
        )
        verifier_copy = copy_file(
            verifier_source, destination / "INDEPENDENT_APPLICABILITY_VERIFICATION.json"
        )
        write_json(destination / "SOURCE_THEOREM_RECORD.json", source_theorem)
        write_json(
            destination / "APPLICABILITY_RECORD.json",
            (registry_value.get("applicability_records") or {}).get(app_id, {}),
        )
        if case == "positive":
            evidence["reconstruction_result"] = evidence_entry(output, reconstruction_copy)
            evidence["verifier_result"] = evidence_entry(output, verifier_copy)
        elif case == "negative-hypothesis":
            evidence["negative_hypothesis_reconstruction"] = evidence_entry(output, reconstruction_copy)
            evidence["negative_hypothesis_verifier"] = evidence_entry(output, verifier_copy)
        else:
            evidence["negative_direction_reconstruction"] = evidence_entry(output, reconstruction_copy)
            evidence["negative_direction_verifier"] = evidence_entry(output, verifier_copy)

    full_junit = copy_file(
        args.pytest_evidence / "pytest-full.xml",
        output / "validation-evidence" / "phase-G" / "pytest-full.xml",
    )
    focused_junit = copy_file(
        args.pytest_evidence / "pytest-focused.xml",
        output / "validation-evidence" / "phase-G" / "pytest-focused.xml",
    )
    evidence["pytest_full"] = evidence_entry(output, full_junit)
    evidence["pytest_focused"] = evidence_entry(output, focused_junit)
    evidence["model_config"] = evidence_entry(output, config_target)

    required_testcases = [
        "test_literature_lead_public_query_handoff",
        "test_searcher_rejects_unapproved_query",
        "test_nonblocking_literature_query_executes",
        "test_real_executor_end_to_end_literature_pipeline",
        "test_authority_rejects_fake_statement_real_pdf",
        "test_authority_rejects_wrong_span",
        "test_authority_rejects_modified_text_artifact",
        "test_cancellation_completed_before_cancel_not_pass",
        "test_budget_underestimate_commit", "test_budget_overestimate_release",
        "test_budget_unknown_usage_after_interrupt",
        "test_budget_global_hard_cap_after_reconcile",
        "test_readiness_missing_evidence_fails_closed",
        "test_readiness_hash_mismatch_fails_closed",
        "test_readiness_does_not_trust_phase_pass_string",
        "test_readiness_parses_real_pytest_xml",
        "test_source_theorem_does_not_close_obligation",
        "test_missing_applicability_defaults_fail_closed",
        "test_context_true_flags_cannot_bypass_applicability",
        "test_applicability_positive_exact_match",
        "test_applicability_hypothesis_mismatch",
        "test_applicability_wrong_direction",
        "test_applicability_exception_mismatch",
        "test_applicability_requires_independent_verifier",
        "test_same_theorem_different_obligation_has_separate_applicability",
        "test_assumption_snapshot_change_invalidates_applicability",
        "test_successor_does_not_reuse_stale_applicability",
        "test_dual_track_not_cancelled_on_source_theorem_only",
        "test_dual_track_cancelled_after_applicable_authority",
    ]
    readiness_inputs = {
        "schema_version": 1,
        "evidence": evidence,
        "required_testcases": required_testcases,
        "scope_exclusions": [
            "tests/test_interrupt_race.py (Windows os.killpg unavailable)",
            "tests/test_tui_keys.py (Windows termios unavailable)",
            "no C2/G main project", "no long campaign",
        ],
    }
    write_json(output / "READINESS_INPUTS.json", readiness_inputs)

    readiness = audit(output)
    write_json(output / "FINAL_READINESS.json", readiness)
    copy_file(
        output / "FINAL_READINESS.json",
        output / "validation-evidence" / "phase-G" / "result.json",
    )
    for phase in "ABCDEFG":
        phase_hashes(output, phase)

    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repo, capture_output=True,
        text=True, check=True,
    ).stdout.splitlines()
    manifest_files = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "VALIDATION_MANIFEST.json":
            continue
        relative = str(path.relative_to(output)).replace("\\", "/")
        if relative.startswith("validation-evidence/"):
            evidence_type = "validation_evidence"
        elif relative.startswith("source/"):
            evidence_type = "source"
        elif relative.startswith("tests/"):
            evidence_type = "test"
        elif relative.startswith("configs/"):
            evidence_type = "configuration"
        elif relative.startswith("docs/"):
            evidence_type = "documentation"
        else:
            evidence_type = "readiness_control"
        manifest_files.append({
            "path": relative, "sha256": sha256(path), "size": path.stat().st_size,
            "evidence_type": evidence_type,
        })
    manifest = {
        "archive_schema_version": 1,
        "git_commit": commit, "git_branch": branch, "dirty_files": dirty,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": manifest_files,
    }
    write_json(output / "VALIDATION_MANIFEST.json", manifest)
    zip_path = args.zip_path.resolve()
    if zip_path.exists():
        raise FileExistsError(f"refusing to overwrite archive ZIP: {zip_path}")
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=output)
    return output, zip_path, readiness


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--system-root", type=Path, required=True)
    parser.add_argument("--phase-a", type=Path, required=True)
    parser.add_argument("--phase-b", type=Path, required=True)
    parser.add_argument("--phase-c", type=Path, required=True)
    parser.add_argument("--phase-def", type=Path, required=True)
    parser.add_argument("--pytest-evidence", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip-path", type=Path, required=True)
    args = parser.parse_args()
    output, zip_path, readiness = build(args)
    print(json.dumps({
        "output_dir": str(output), "zip_path": str(zip_path),
        "verdict": readiness["verdict"],
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
