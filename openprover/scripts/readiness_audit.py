"""Fail-closed, evidence-driven pilot-readiness verifier.

The verifier reads only paths explicitly enumerated in READINESS_INPUTS.json.
Producer-supplied phase status strings are provenance, never verdict inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


REQUIRED_EVIDENCE = (
    "phase_A_result", "phase_B_result", "phase_C_result", "phase_D_result",
    "phase_E_result", "phase_F_result", "openalex_response", "pdf_artifact",
    "text_artifact", "extraction_artifact", "authority_registry",
    "reconstruction_result", "verifier_result", "pytest_full", "pytest_focused",
    "negative_hypothesis_reconstruction", "negative_hypothesis_verifier",
    "negative_direction_reconstruction", "negative_direction_verifier",
    "model_config", "model_catalog",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _resolve(root: Path, relative: str) -> Path:
    path = (root / str(relative)).resolve()
    path.relative_to(root.resolve())
    return path


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON evidence is not an object: {path}")
    return value


def _junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    names = []
    for suite in suites:
        for key in totals:
            totals[key] += int(float(suite.attrib.get(key, 0) or 0))
        names.extend(
            str(case.attrib.get("name") or "") for case in suite.findall(".//testcase")
        )
    totals["testcase_names"] = names
    return totals


def _phase_a(raw: dict) -> tuple[str, dict]:
    records = raw.get("records") if isinstance(raw.get("records"), dict) else {}
    a_id = str(raw.get("task_A_id") or "")
    b_id = str(raw.get("task_B_id") or "")
    a = records.get(a_id, {}) if a_id else {}
    b = records.get(b_id, {}) if b_id else {}
    state_a = str(raw.get("task_a_status") or a.get("final_task_state") or "")
    state_b = str(raw.get("task_b_status") or b.get("final_task_state") or "")
    interrupt_at = str(a.get("interrupt_dispatch_timestamp") or "")
    exit_at = str(a.get("process_exit_timestamp") or "")
    error = a.get("provider_error") if isinstance(a.get("provider_error"), dict) else {}
    checks = {
        "task_a_final_state_interrupted": state_a == "INTERRUPTED",
        "cancel_requested": bool(a.get("cancel_request_timestamp")),
        "interrupt_dispatched": bool(interrupt_at),
        "process_exit_after_interrupt": bool(interrupt_at and exit_at and exit_at >= interrupt_at),
        "root_pid_recorded": bool((a.get("processes") or [{}])[0].get("pid")),
        "retry_count_zero": int(error.get("retry_count", a.get("retry_count", 0)) or 0) == 0,
        "fallback_count_zero": int(a.get("fallback_count", raw.get("fallback_count", 0)) or 0) == 0,
        "task_b_complete": state_b == "COMPLETE",
    }
    if state_a == "COMPLETED_BEFORE_CANCEL":
        return "INCONCLUSIVE", checks
    return ("PASS" if all(checks.values()) else "FAIL"), checks


def _phase_b(raw: dict) -> tuple[str, dict]:
    checks = {
        "requested_model": raw.get("requested_model") == "gpt-5.6-luna",
        "actual_model": raw.get("actual_model") == "gpt-5.6-luna",
        "requested_effort": raw.get("requested_effort") == "max",
        "actual_effort": raw.get("actual_effort") == "max",
        "actual_provider": raw.get("actual_provider") == "codex_cli",
        "no_fallback": raw.get("fallback_used") is False,
        "process_success": bool(raw.get("processes")) and all(
            item.get("returncode") == 0 for item in raw.get("processes", [])
        ),
        "usage_recorded": isinstance(raw.get("usage"), dict),
    }
    return ("PASS" if all(checks.values()) else "FAIL"), checks


def _phase_c(raw: dict) -> tuple[str, dict]:
    routes = raw.get("route_classes") if isinstance(raw.get("route_classes"), dict) else {}
    expected = {
        "routine": ("gpt-5.6-luna", "max"),
        "research": ("gpt-5.6-sol", "high"),
        "strategic": ("gpt-5.6-sol", "max"),
    }
    checks = {
        tier: (
            routes.get(tier, {}).get("actual_model") == model
            and routes.get(tier, {}).get("reasoning_effort") == effort
            and routes.get(tier, {}).get("fallback") is False
        )
        for tier, (model, effort) in expected.items()
    }
    checks["checkpoint"] = raw.get("checkpoint_campaign_status") == "STOPPED_AT_CHECKPOINT"
    checks["resume"] = raw.get("final_campaign_status") == "COMPLETE_PROVED_REPLAY"
    checks["synthetic_provider_calls_declared"] = raw.get("no_external_provider_calls") is True
    return ("PASS" if all(checks.values()) else "FAIL"), checks


def audit(archive_root: Path, *, inputs_path: Path | None = None) -> dict:
    archive_root = archive_root.resolve()
    inputs_path = (inputs_path or archive_root / "READINESS_INPUTS.json").resolve()
    errors: list[str] = []
    phase_details: dict[str, Any] = {}
    try:
        inputs_path.relative_to(archive_root)
        inputs = _json(inputs_path)
    except Exception as exc:
        return {
            "verdict": "PARTIALLY_READY", "phase_A": "MISSING", "phase_B": "MISSING",
            "phase_C": "MISSING", "phase_D": "MISSING", "phase_E": "MISSING",
            "phase_F": "MISSING", "regression": {"tests": 0, "failures": 0, "errors": 1, "skipped": 0},
            "evidence_complete": False, "hashes_verified": False,
            "audit_errors": [f"READINESS_INPUTS unavailable: {exc}"],
        }
    evidence = inputs.get("evidence") if isinstance(inputs.get("evidence"), dict) else {}
    loaded: dict[str, Path] = {}
    hashes_verified = True
    for name in REQUIRED_EVIDENCE:
        entry = evidence.get(name)
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
            errors.append(f"missing evidence input: {name}")
            hashes_verified = False
            continue
        try:
            path = _resolve(archive_root, entry["path"])
            if not path.is_file():
                raise FileNotFoundError(entry["path"])
            actual = _sha256(path)
            if actual.casefold() != str(entry["sha256"]).casefold():
                raise ValueError(f"hash mismatch: {name}")
            loaded[name] = path
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            hashes_verified = False
    phase_status = {name: "MISSING" for name in "ABCDEF"}
    regression = {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
    if not errors:
        try:
            raw_a = _json(loaded["phase_A_result"])
            phase_status["A"], phase_details["A"] = _phase_a(raw_a)
            raw_b = _json(loaded["phase_B_result"])
            phase_status["B"], phase_details["B"] = _phase_b(raw_b)
            raw_c = _json(loaded["phase_C_result"])
            phase_status["C"], phase_details["C"] = _phase_c(raw_c)

            raw_d = _json(loaded["phase_D_result"])
            provider_payload = _json(loaded["openalex_response"])
            if isinstance(provider_payload.get("payload"), dict):
                provider_payload = provider_payload["payload"]
            d_checks = {
                "provider": raw_d.get("provider") == "openalex",
                "public_query": raw_d.get("query") == "Pythagorean theorem",
                "private_context_not_transmitted": raw_d.get("project_context_transmitted") is False,
                "raw_response_results": isinstance(provider_payload.get("results"), list)
                and bool(provider_payload.get("results")),
            }
            phase_status["D"] = "PASS" if all(d_checks.values()) else "FAIL"
            phase_details["D"] = d_checks

            raw_e = _json(loaded["phase_E_result"])
            pdf_hash = _sha256(loaded["pdf_artifact"])
            text_hash = _sha256(loaded["text_artifact"])
            extraction = _json(loaded["extraction_artifact"])
            selected_id = str(raw_e.get("selected_extraction_id") or "")
            selected = next(
                (item for item in extraction.get("extractions", [])
                 if isinstance(item, dict) and item.get("extraction_id") == selected_id),
                None,
            )
            span_ok = False
            if selected:
                text = loaded["text_artifact"].read_text(encoding="utf-8")
                start, end = int(selected.get("span_start", -1)), int(selected.get("span_end", -1))
                if 0 <= start < end <= len(text):
                    normalized = " ".join(text[start:end].strip().split())
                    span_hash = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                    span_ok = (
                        span_hash.casefold() == str(selected.get("extracted_statement_sha256") or "").casefold()
                        and normalized == " ".join(str(selected.get("normalized_extracted_text") or "").split())
                    )
            e_checks = {
                "pdf_magic": loaded["pdf_artifact"].read_bytes().startswith(b"%PDF-"),
                "pdf_hash": pdf_hash.casefold() == str(raw_e.get("artifact_sha256") or "").casefold(),
                "text_hash": text_hash.casefold() == str(raw_e.get("text_artifact_sha256") or "").casefold(),
                "extraction_source_hash": str(extraction.get("source_artifact_sha256") or "").casefold() == pdf_hash.casefold(),
                "extraction_text_hash": str(extraction.get("text_artifact_sha256") or "").casefold() == text_hash.casefold(),
                "span_recomputed": span_ok,
            }
            phase_status["E"] = "PASS" if all(e_checks.values()) else "FAIL"
            phase_details["E"] = e_checks

            raw_f = _json(loaded["phase_F_result"])
            registry = _json(loaded["authority_registry"])
            reconstruction = _json(loaded["reconstruction_result"])
            verifier = _json(loaded["verifier_result"])
            authority_id = str(raw_f.get("authority_id") or "")
            authority = (registry.get("source_theorems") or {}).get(authority_id, {})
            applicability_id = str(raw_f.get("applicability_id") or "")
            applicability = (registry.get("applicability_records") or {}).get(applicability_id, {})
            negative_cases = raw_f.get("negative_cases") if isinstance(raw_f.get("negative_cases"), dict) else {}
            components = raw_f.get("components") if isinstance(raw_f.get("components"), dict) else {}
            required_components = (
                "NETWORK_DISCOVERY_PASS", "PDF_RETRIEVAL_PASS", "THEOREM_EXTRACTION_PASS",
                "SOURCE_THEOREM_PROMOTION_PASS", "RECONSTRUCTION_PASS", "VERIFIER_PASS",
                "APPLICABILITY_PROMOTION_PASS", "NEGATIVE_HYPOTHESIS_PASS",
                "NEGATIVE_DIRECTION_PASS",
                "PRODUCTION_PIPELINE_PASS",
            )
            f_checks = {
                "all_component_statuses": all(components.get(name) is True for name in required_components),
                "production_executor": raw_f.get("production_executor_class")
                == "openprover.math_research.literature.LiteratureTaskExecutor",
                "no_smoke_local_handlers": raw_f.get("smoke_local_handlers") is False,
                "source_registry_promoted": authority.get("status") == "VERIFIED_SOURCE_THEOREM",
                "registry_has_no_errors": not authority.get("authority_verification_errors"),
                "reconstruction_artifact": reconstruction.get("status") == "APPLICABILITY_CANDIDATE"
                and isinstance(reconstruction.get("hypothesis_mapping"), list)
                and isinstance(reconstruction.get("conclusion_mapping"), dict)
                and isinstance(reconstruction.get("direction_analysis"), dict)
                and isinstance(reconstruction.get("exception_analysis"), dict),
                "independent_verifier_artifact": verifier.get("verdict") == "APPLICABLE"
                and verifier.get("promotion_status") == "APPLICABLE_EXTERNAL_AUTHORITY"
                and verifier.get("verifier_call_id") != reconstruction.get("reconstructor_call_id"),
                "applicability_promoted": applicability.get("status") == "APPLICABLE_EXTERNAL_AUTHORITY",
                "snapshot_bound": applicability.get("assumption_snapshot_hash")
                == raw_f.get("assumption_snapshot_hash"),
                "negative_hypothesis_rejected": negative_cases.get("negative-hypothesis", {}).get("closed") is False
                and negative_cases.get("negative-hypothesis", {}).get("actual_verdict")
                in {"HYPOTHESIS_MISMATCH", "NOT_APPLICABLE"},
                "negative_direction_rejected": negative_cases.get("negative-direction", {}).get("closed") is False
                and negative_cases.get("negative-direction", {}).get("actual_verdict") == "WRONG_DIRECTION",
                "obligation_closed": raw_f.get("obligation_status") == "CLOSED",
            }
            phase_status["F"] = "PASS" if all(f_checks.values()) else "FAIL"
            phase_details["F"] = f_checks

            regression = _junit(loaded["pytest_full"])
            focused = _junit(loaded["pytest_focused"])
            required_tests = set(inputs.get("required_testcases") or [])
            present = set(focused.pop("testcase_names", [])) | set(regression.pop("testcase_names", []))
            missing_tests = sorted(required_tests - present)
            if missing_tests:
                errors.append("required focused tests absent from JUnit: " + ", ".join(missing_tests))
            if focused["failures"] or focused["errors"]:
                errors.append("focused JUnit contains failures/errors")
            phase_details["tests"] = {"focused": focused, "required_missing": missing_tests}
        except Exception as exc:
            errors.append(f"evidence verification error: {type(exc).__name__}: {exc}")
    regression_ok = regression.get("failures") == 0 and regression.get("errors") == 0
    evidence_complete = len(loaded) == len(REQUIRED_EVIDENCE) and not errors
    ready = (
        evidence_complete and hashes_verified and regression_ok
        and all(phase_status[name] == "PASS" for name in "ABCDEF")
    )
    result = {
        "verdict": "READY_FOR_PILOT_CAMPAIGN" if ready else "PARTIALLY_READY",
        **{f"phase_{name}": phase_status[name] for name in "ABCDEF"},
        "regression": {key: int(regression.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")},
        "evidence_complete": evidence_complete,
        "hashes_verified": hashes_verified,
        "phase_checks": phase_details,
        "audit_errors": errors,
        "inputs_sha256": _sha256(inputs_path),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.archive_root, inputs_path=args.inputs)
    output = args.output or args.archive_root / "FINAL_READINESS.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
