"""Read-only certification of an existing replay-generated candidate.

This module never invokes the planner and never mutates the source project or
historical replay.  It applies a hash-pinned authority-only normalization,
validates the Trust Kernel deterministically, then runs bounded verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .audit_prompts import AUDITOR_ROLES, auditor_prompt, final_auditor_prompt
from .audit_protocol import AuditResult, normalize_audit_result
from .campaign import ReplayPolicy, classify_provider_exception
from .project import ProjectError, ProjectStore, utc_now
from .providers import create_client, load_model_config, resolve_role_config
from .routing import ModelRouter, RoutedLLMClient
from .runtime_backend import SQLiteRuntimeBackend
from .runtime_bindings import CrossPlaneExecutionBinding
from .schemas import AuditResultSchema, SchemaError, parse_structured_response
from .state_machine import AuditGate
from .trust_kernel import (
    DependencyAuthorityResolver,
    FoundationRegistry,
    SemanticRegistry,
)
from .truth_store import TruthStoreFacade


CERTIFICATION_AUDIT_CONTRACT = """Return exactly one JSON object with these fields:
- domain_verdict: exactly one of \"PASS\", \"FAIL\", or \"INCONCLUSIVE\";
- execution_status: exactly one of \"OK\" or \"ERROR\";
- findings: JSON array of strings;
- failure_reasons: JSON array of strings;
- cross_audit_notes: JSON array of strings.

If you completed the requested check, execution_status must be \"OK\". Never
use synonyms such as \"COMPLETED\" or \"CERTIFIED\" for execution_status.
Mathematical acceptance belongs only in domain_verdict. Return no prose outside
the JSON object."""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProjectError(f"Certification path escapes its declared root: {relative}") from exc
    if not candidate.is_file():
        raise ProjectError(f"Certification input is missing: {candidate}")
    return candidate


@dataclass(slots=True)
class PreparedCertification:
    candidate: str
    candidate_hash: str
    amended_candidate_hash: str
    context: str
    dependency_report: dict
    leak_audit: dict
    foundation_ids_used: list[str]


class ReplayCertificationRunner:
    """Certify a fixed candidate with no exploratory proof-search phase."""

    def __init__(
        self,
        *,
        spec_path: str | Path,
        repair_root: str | Path,
        source_root: str | Path,
        config_path: str | Path,
        output_dir: str | Path,
        semantic_registry_path: str | Path,
        worker_count: int = 2,
    ):
        if worker_count != 2:
            raise ProjectError("Replay certification requires exactly two bounded Worker verifiers")
        self.spec_path = Path(spec_path).resolve()
        self.spec = json.loads(self.spec_path.read_text(encoding="utf-8"))
        self.repair_root = Path(repair_root).resolve()
        self.source_root = Path(source_root).resolve()
        self.config_path = Path(config_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.semantic_registry_path = Path(semantic_registry_path).resolve()
        self.worker_count = worker_count
        for label, protected_root in (
            ("repair", self.repair_root),
            ("source", self.source_root),
        ):
            try:
                self.output_dir.relative_to(protected_root)
            except ValueError:
                continue
            raise ProjectError(
                f"Certification output must be outside the read-only {label} root: {protected_root}"
            )
        self.project = ProjectStore(self.repair_root)
        self.truth_store = TruthStoreFacade(self.project)
        self.claim_snapshot = self.truth_store.capture_claim_snapshot(
            self.spec["target_id"], persist=False
        )
        self.execution_binding = CrossPlaneExecutionBinding.capture(
            root_claim_snapshot_hash=self.claim_snapshot.claim_snapshot_hash
        )
        self.config = load_model_config(self.config_path)
        self.model_router = ModelRouter(
            self.config,
            state_path=self.output_dir / "routing_state.json",
            runtime_backend=SQLiteRuntimeBackend(self.project.root),
            runtime_scope=f"certification:{self.output_dir.name}",
            execution_binding=self.execution_binding,
            execution_binding_validator=self._validate_execution_binding,
            require_execution_binding=True,
        )
        self.clients: list[object] = []

    def _validate_execution_binding(self, binding):
        if binding is None:
            return "REVALIDATION_REQUIRED: certification execution binding is missing"
        if binding != self.execution_binding:
            return "STALE_CLAIM_SNAPSHOT: certification binding is not current"
        try:
            self.truth_store.validate_snapshot_for_execution(self.claim_snapshot)
        except Exception as exc:
            return f"STALE_CLAIM_SNAPSHOT: {exc}"
        return True

    def prepare(self) -> PreparedCertification:
        spec = self.spec
        candidate_path = _safe_path(self.repair_root, spec["candidate_file"])
        candidate_hash = _sha256(candidate_path)
        if candidate_hash.casefold() != spec["candidate_sha256"].casefold():
            raise ProjectError("Replay candidate hash mismatch")
        candidate = candidate_path.read_text(encoding="utf-8")
        replacement_log = []
        for replacement in spec.get("authority_only_replacements", []):
            old = replacement["old"]
            count = candidate.count(old)
            if count != 1:
                raise ProjectError(
                    f"Authority-only replacement expected one match, found {count}: {old[:80]}"
                )
            candidate = candidate.replace(old, replacement["new"], 1)
            replacement_log.append(
                {
                    "old_sha256": hashlib.sha256(old.encode("utf-8")).hexdigest(),
                    "new_sha256": hashlib.sha256(replacement["new"].encode("utf-8")).hexdigest(),
                }
            )
        for phrase in spec.get("forbidden_authority_phrases", []):
            if phrase.casefold() in candidate.casefold():
                raise ProjectError(
                    f"Forbidden authority phrase remains after normalization: {phrase}"
                )

        authority_uses = list(spec.get("authority_uses", []))
        authority_manifest = {
            "all_external_claims_classified": True,
            "branches_resolved": True,
            "unresolved": [],
            "authority_uses": authority_uses,
            "source_paths": list(spec.get("manifest_source_paths", [])),
        }
        candidate = candidate.rstrip() + (
            "\n\n<!-- OPENPROVER_AUTHORITY_MANIFEST\n"
            + json.dumps(authority_manifest, ensure_ascii=False, indent=2)
            + "\n-->\n"
        )

        foundations = FoundationRegistry.load_builtin()
        semantics = SemanticRegistry.load(
            self.semantic_registry_path,
            source_root=self.source_root,
        )
        resolver = DependencyAuthorityResolver(
            foundations=foundations,
            semantics=semantics,
            project=self.project,
            notation_scope=spec["notation_scope"],
        )
        dependency_report = resolver.resolve(authority_uses)
        if not dependency_report.admissible:
            raise ProjectError(
                "Certification dependency reconstruction failed: "
                + "; ".join(dependency_report.errors)
            )

        replay_manifest = _safe_path(self.repair_root, spec["replay_manifest"])
        replay_policy = ReplayPolicy.from_manifest(replay_manifest)
        allowed_ok, allowed_errors = replay_policy.audit_sources(
            spec.get("manifest_source_paths", [])
        )
        extension_ok, extension_errors = replay_policy.audit_explicit_extension(
            spec.get("explicit_extension_paths", [])
        )
        source_records = []
        source_texts = []
        for item in spec.get("source_material", []):
            root = self.repair_root if item["root"] == "repair" else self.source_root
            path = _safe_path(root, item["path"])
            actual_hash = _sha256(path)
            if actual_hash.casefold() != item["sha256"].casefold():
                raise ProjectError(f"Certification source hash mismatch: {item['path']}")
            text = path.read_text(encoding="utf-8")
            start = int(item.get("line_start", 1))
            end = int(item.get("line_end", 0))
            if end:
                lines = text.splitlines()
                text = "\n".join(lines[start - 1 : end])
            source_records.append(
                {
                    "path": item["path"],
                    "root": item["root"],
                    "sha256": actual_hash,
                    "line_start": start,
                    "line_end": end or None,
                    "purpose": item["purpose"],
                }
            )
            source_texts.append(
                f"## Source: {item['purpose']}\n\nPath: `{item['path']}`\n"
                f"SHA-256: `{actual_hash}`\n\n{text}"
            )
        prior_audit_texts = []
        for relative in spec.get("prior_repair_audits", []):
            path = _safe_path(self.repair_root, relative)
            prior_audit_texts.append(
                f"### `{relative}`\n\n```json\n"
                + path.read_text(encoding="utf-8").rstrip()
                + "\n```"
            )
            source_records.append(
                {
                    "path": relative,
                    "root": "repair",
                    "sha256": _sha256(path),
                    "purpose": "prior repair audit state; not mathematical authority",
                }
            )
        leak_errors = allowed_errors + extension_errors
        leak_audit = {
            "passed": allowed_ok and extension_ok and not leak_errors,
            "inherited_policy_hash": replay_policy.policy_hash,
            "inherited_manifest_hash": replay_policy.source_manifest_hash,
            "allowed_sources_checked": list(spec.get("manifest_source_paths", [])),
            "explicit_hash_pinned_extensions": list(spec.get("explicit_extension_paths", [])),
            "forbidden_sources_materialized": [],
            "errors": leak_errors,
            "source_records": source_records,
        }
        if not leak_audit["passed"]:
            raise ProjectError("Replay leak audit failed: " + "; ".join(leak_errors))

        foundation_lines = []
        for authority_id in dependency_report.foundation_ids_used:
            item = foundations.get(authority_id)
            foundation_lines.append(
                f"- `{authority_id}`: {item.statement}\n  Conditions: " + "; ".join(item.conditions)
            )
        semantic_lines = []
        for authority_id in dependency_report.semantics:
            item = semantics.get(authority_id, notation_scope=spec["notation_scope"])
            semantic_lines.append(
                f"- `{authority_id}` ({item.authority_kind}): {item.statement}\n"
                f"  Source: `{item.provenance['source_file']}` / "
                f"`{item.provenance['source_hash']}`"
            )
        target = self.project.load_theorem(spec["target_id"])
        context = f"""# Read-only Replay Certification Context

No planner or proof search is authorized. Certify only the fixed, hash-pinned
candidate after the recorded authority-only normalization.

## Target

`{target["id"]}`: {target["statement"]}

Notation scope: `{spec["notation_scope"]}`

## Foundations

{chr(10).join(foundation_lines)}

## Semantics

{chr(10).join(semantic_lines)}

## Deterministic dependency report

```json
{json.dumps(dependency_report.to_dict(), ensure_ascii=False, indent=2)}
```

## Source bodies

{chr(10).join(source_texts)}

## Prior repair audit state (diagnostic only, never authority)

{chr(10).join(prior_audit_texts)}
"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "GA1-1-candidate-v2-certified.md").write_text(
            candidate, encoding="utf-8"
        )
        _write_json(
            self.output_dir / "authority_normalization.json",
            {
                "source_candidate": str(candidate_path),
                "source_candidate_sha256": candidate_hash,
                "amended_candidate_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
                "replacement_log": replacement_log,
                "mathematical_proof_search_performed": False,
            },
        )
        _write_json(
            self.output_dir / "dependency_report.json",
            dependency_report.to_dict(),
        )
        _write_json(self.output_dir / "replay_leak_audit.json", leak_audit)
        (self.output_dir / "CERTIFICATION_CONTEXT.md").write_text(context, encoding="utf-8")
        return PreparedCertification(
            candidate=candidate,
            candidate_hash=candidate_hash,
            amended_candidate_hash=hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
            context=context,
            dependency_report=dependency_report.to_dict(),
            leak_audit=leak_audit,
            foundation_ids_used=dependency_report.foundation_ids_used,
        )

    def run(self) -> dict:
        started = time.perf_counter()
        prepared = self.prepare()
        try:
            worker_results = self._run_worker_verifiers(prepared)
            audits = self._run_specialist_audits(prepared)
            final = self._run_final_audit(prepared, audits)
            audits["final_proof_auditor"] = final
            gate = self._build_gate(prepared, worker_results=worker_results, audits=audits)
            secondary = None
            if gate.passed:
                secondary = self._run_secondary_reconstruction(prepared, audits)
                normalized_secondary = normalize_audit_result("secondary_reconstruction", secondary)
                if normalized_secondary.execution_status == "ERROR":
                    gate.execution_errors.append(
                        "secondary_reconstruction: "
                        + (normalized_secondary.execution_error or "execution failed")
                    )
                elif normalized_secondary.domain_verdict == "INCONCLUSIVE":
                    gate.inconclusive_audits.append("secondary_reconstruction")
                elif normalized_secondary.domain_verdict == "FAIL":
                    gate.failure_reasons.extend(
                        normalized_secondary.failure_reasons
                        or ["secondary reconstruction returned FAIL"]
                    )
                    gate.final_auditor_pass = False
            status = "PASS" if gate.passed else gate.outcome
            if gate.execution_errors:
                joined = " ".join(gate.execution_errors)
                status, _ = classify_provider_exception(RuntimeError(joined))
            result = {
                "schema_version": 1,
                "benchmark": self.spec.get("benchmark_id", "replay-certification"),
                "status": status,
                "proved_replay": gate.passed,
                "worker_count": self.worker_count,
                "planner_calls": 0,
                "proof_search_performed": False,
                "candidate_source_sha256": prepared.candidate_hash,
                "candidate_certified_sha256": prepared.amended_candidate_hash,
                "foundation_ids_used": prepared.foundation_ids_used,
                "dependency_report": prepared.dependency_report,
                "replay_leak_audit": prepared.leak_audit,
                "worker_verifiers": worker_results,
                "formal_audits": audits,
                "secondary_reconstruction": secondary,
                "gate": gate.to_dict(),
                "wall_clock_seconds": round(time.perf_counter() - started, 3),
                "completed_at": utc_now(),
            }
            _write_json(self.output_dir / "CERTIFICATION_RESULT.json", result)
            self._write_summary(result)
            return result
        finally:
            for client in self.clients:
                client.cleanup()

    def _call(
        self,
        *,
        role_name: str,
        role_config: dict,
        label: str,
        system: str,
        prompt: str,
        output_path: Path,
    ) -> dict:
        routing_role = role_name
        if role_name.startswith("worker_verifier"):
            routing_role = "worker_verifier"
        elif role_name == "final_proof_auditor":
            routing_role = "final_proof_auditor"
        client = RoutedLLMClient(
            self.model_router,
            client_factory=create_client,
            default_role=routing_role,
            archive_dir=self.output_dir / "archive" / label,
            working_dir=self.output_dir / "gemini" / label,
        )
        self.clients.append(client)
        response = client.call(
            prompt=prompt,
            system_prompt=system,
            label=label,
            archive_path=output_path,
            response_schema=AuditResultSchema,
        )
        try:
            return parse_structured_response(response, AuditResultSchema).model_dump(mode="python")
        except SchemaError as exc:
            raise ProjectError(f"{label} returned invalid structured output: {exc}") from exc

    def _run_worker_verifiers(self, prepared: PreparedCertification) -> dict:
        directives = {
            "worker_verifier_mathematics": (
                "Independently check the low-E contradiction and high-E Jacobi contradiction, "
                "including every endpoint. Do not propose a new proof."
            ),
            "worker_verifier_authority": (
                "Reconstruct scope and authority coverage. Confirm that the semantic bridge comes "
                "from SEM-G-PRIM-01 and classical claims use the exact Foundation IDs."
            ),
        }
        role = resolve_role_config(self.config, "worker")
        results = {}
        with ThreadPoolExecutor(max_workers=self.worker_count) as pool:
            futures = {}
            for label, directive in directives.items():
                prompt = f"""{directive}

{CERTIFICATION_AUDIT_CONTRACT}

{prepared.context}

# Fixed candidate

{prepared.candidate}
"""
                futures[
                    pool.submit(
                        self._safe_call,
                        role_name=label,
                        role_config=role,
                        label=f"certification_{label}",
                        system="You are a bounded Worker Verifier, not a proof-search agent.",
                        prompt=prompt,
                        output_path=self.output_dir / "worker_verifiers" / f"{label}_call.md",
                    )
                ] = label
            for future in as_completed(futures):
                label = futures[future]
                results[label] = future.result()
                _write_json(
                    self.output_dir / "worker_verifiers" / f"{label}.json",
                    results[label],
                )
        return results

    def _safe_call(self, **kwargs) -> dict:
        role_name = kwargs["role_name"]
        try:
            raw = self._call(**kwargs)
            return normalize_audit_result(role_name, raw).to_dict()
        except Exception as exc:
            return AuditResult.from_exception(role_name, exc).to_dict()

    def _run_specialist_audits(self, prepared: PreparedCertification) -> dict:
        audits = {}
        with ThreadPoolExecutor(max_workers=len(AUDITOR_ROLES)) as pool:
            futures = {}
            for role_name in AUDITOR_ROLES:
                role = resolve_role_config(self.config, role_name)
                system, prompt = auditor_prompt(role_name, prepared.context, prepared.candidate)
                futures[
                    pool.submit(
                        self._safe_call,
                        role_name=role_name,
                        role_config=role,
                        label=f"audit_{role_name}",
                        system=system,
                        prompt=prompt,
                        output_path=self.output_dir / "audits" / f"{role_name}_call.md",
                    )
                ] = role_name
            for future in as_completed(futures):
                role_name = futures[future]
                audits[role_name] = future.result()
                _write_json(
                    self.output_dir / "audits" / f"{role_name}.json",
                    audits[role_name],
                )
        return audits

    def _run_final_audit(self, prepared: PreparedCertification, audits: dict) -> dict:
        system, prompt = final_auditor_prompt(prepared.context, prepared.candidate, audits)
        result = self._safe_call(
            role_name="final_proof_auditor",
            role_config=resolve_role_config(self.config, "final_proof_auditor"),
            label="final_proof_auditor",
            system=system,
            prompt=prompt,
            output_path=self.output_dir / "audits" / "final_proof_auditor_call.md",
        )
        _write_json(self.output_dir / "audits" / "final_proof_auditor.json", result)
        return result

    def _build_gate(
        self,
        prepared: PreparedCertification,
        *,
        worker_results: dict,
        audits: dict,
    ) -> AuditGate:
        normalized_workers = {
            name: normalize_audit_result(name, value) for name, value in worker_results.items()
        }
        normalized = {name: normalize_audit_result(name, value) for name, value in audits.items()}
        final = normalized["final_proof_auditor"]
        criteria = final.criteria
        failure_reasons = []
        execution_errors = []
        inconclusive = []
        for name, result in {**normalized_workers, **normalized}.items():
            if result.execution_status == "ERROR":
                execution_errors.append(f"{name}: {result.execution_error or 'execution failed'}")
            elif result.domain_verdict == "INCONCLUSIVE":
                inconclusive.append(name)
            elif result.domain_verdict == "FAIL":
                failure_reasons.extend(result.failure_reasons or [f"{name} returned FAIL"])
        specialists_pass = all(normalized[role].passed for role in AUDITOR_ROLES)
        workers_pass = all(result.passed for result in normalized_workers.values())
        return AuditGate(
            forward_implication=bool(criteria.get("forward_implication")),
            converse_if_applicable=bool(criteria.get("converse_if_applicable")),
            exhaustive_cases=bool(criteria.get("exhaustive_cases")),
            parameter_ranges=bool(criteria.get("parameter_ranges")),
            boundary_cases=bool(criteria.get("boundary_cases")),
            dependencies_valid=(
                bool(criteria.get("dependencies_valid"))
                and prepared.dependency_report.get("admissible", False)
                and normalized["dependency_auditor"].passed
            ),
            no_counterexample=(
                bool(criteria.get("no_counterexample"))
                and normalized["counterexample_hunter"].passed
            ),
            auditors_pass=(
                bool(criteria.get("auditors_pass")) and specialists_pass and workers_pass
            ),
            final_auditor_pass=final.passed,
            computational_evidence_separated=bool(criteria.get("computational_evidence_separated")),
            failure_reasons=failure_reasons,
            execution_errors=execution_errors,
            inconclusive_audits=inconclusive,
            dependency_report=prepared.dependency_report,
        )

    def _run_secondary_reconstruction(
        self,
        prepared: PreparedCertification,
        audits: dict,
    ) -> dict:
        prompt = f"""Independently reconstruct the theorem statement, the
G_prim/h=1 semantic routing, the exhaustive E<=4/E>=5 split, and both
contradictions. Treat the primary audits only as claims to challenge. Do not
open a new proof route.

{CERTIFICATION_AUDIT_CONTRACT}

{prepared.context}

# Fixed candidate

{prepared.candidate}

# Primary audit results

{json.dumps(audits, ensure_ascii=False, indent=2)}
"""
        result = self._safe_call(
            role_name="secondary_reconstruction",
            role_config=resolve_role_config(self.config, "final_proof_auditor"),
            label="certification_secondary_reconstruction",
            system="You are the independent final replay-certification reconstruction check.",
            prompt=prompt,
            output_path=self.output_dir / "secondary" / "reconstruction_call.md",
        )
        _write_json(self.output_dir / "secondary" / "reconstruction.json", result)
        return result

    def _write_summary(self, result: dict) -> None:
        reasons = result["gate"].get("failure_reasons", [])
        errors = result["gate"].get("execution_errors", [])
        lines = [
            "# GA1-1 Replay Certification",
            "",
            f"Status: **{result['status']}**",
            "",
            f"- Planner calls: `{result['planner_calls']}`",
            f"- Proof search performed: `{str(result['proof_search_performed']).lower()}`",
            f"- Worker verifiers: `{result['worker_count']}`",
            f"- Foundation IDs used: {', '.join(result['foundation_ids_used'])}",
            f"- Replay leak audit: `{'PASS' if result['replay_leak_audit']['passed'] else 'FAIL'}`",
            "",
            "## Remaining proof obligations",
            "",
        ]
        if reasons or errors:
            lines.extend(f"- {item}" for item in reasons + errors)
        else:
            lines.append("- None.")
        (self.output_dir / "CERTIFICATION_RESULT.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded, planner-free replay certification")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--repair-root", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--semantic-registry", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    runner = ReplayCertificationRunner(
        spec_path=args.spec,
        repair_root=args.repair_root,
        source_root=args.source_root,
        config_path=args.config,
        output_dir=args.output,
        semantic_registry_path=args.semantic_registry,
        worker_count=2,
    )
    try:
        result = asdict(runner.prepare()) if args.prepare_only else runner.run()
    except (ProjectError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if isinstance(result, dict):
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
