"""Independent audit execution component."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .audit_prompts import AUDITOR_ROLES, auditor_prompt, final_auditor_prompt
from .audit_protocol import AuditResult, normalize_audit_result, parse_audit_response
from .providers import create_client, is_mock_config
from .project import ProjectError, utc_now
from .routing import RoutedLLMClient
from .schemas import AuditResultSchema
from .state_machine import AuditGate
from .trust_kernel import DependencyAuthorityResolver, TrustKernel


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _usage_metrics(client: object | None) -> dict:
    if client is None:
        return {}
    usage = getattr(client, "total_usage", None)
    return dict(usage) if isinstance(usage, dict) else {}


def _sum_usage(clients: list[object]) -> dict:
    result: dict[str, int | bool] = {}
    for client in clients:
        for key, value in _usage_metrics(client).items():
            if isinstance(value, bool):
                result[key] = bool(result.get(key, False) or value)
            else:
                result[key] = int(result.get(key, 0)) + int(value)
    return result


def _api_request_count(client: object | None) -> int:
    if client is None:
        return 0
    if hasattr(client, "api_request_count"):
        return int(getattr(client, "api_request_count"))
    return int(getattr(client, "request_count", 0))


class _OwnerComponent:
    def __init__(self, owner):
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name):
        return getattr(self._owner, name)

    def __setattr__(self, name, value):
        if name == "_owner":
            object.__setattr__(self, name, value)
        else:
            setattr(self._owner, name, value)


class AuditCoordinator(_OwnerComponent):
    """Run specialist, final, and secondary audits outside the lifecycle object."""

    def run_with_retry(self) -> tuple[dict[str, dict], AuditGate]:
        attempts = 0
        while True:
            audits, gate = self.run_audits()
            if not gate.execution_errors or attempts >= self.infrastructure_retries:
                self.state["infrastructure_retry_count"] = attempts
                return audits, gate
            attempts += 1
            _write_json(
                self.run_dir / "audits" / f"infrastructure_retry_{attempts}.json",
                {
                    "attempt": attempts,
                    "errors": gate.execution_errors,
                    "created_at": utc_now(),
                },
            )

    def run_secondary_verification(self) -> dict:
        """Run five bounded checks after the primary gate first passes."""

        checks = {
            "independent_reconstruction": (
                "Reconstruct the proof independently from the theorem statement and authorized sources. "
                "Do not trust the primary audit summaries."
            ),
            "adversarial_review": (
                "Attack the candidate for a concrete mathematical gap, omitted branch, or counterexample."
            ),
            "certificate_rerun": (
                "Recheck every cited computational certificate and its finite-reduction claim. "
                "If none is used, verify that none is silently required."
            ),
            "dependency_coverage": (
                "Reconstruct every external claim and confirm exact Foundation, Semantic, Project, "
                "Local Proof, or Computational Certificate coverage."
            ),
            "statement_scope_reconstruction": (
                "Reconstruct the theorem statement, notation scope, parameter ranges, converse, and branches "
                "from source, then compare them with the candidate."
            ),
        }
        secondary_dir = self.run_dir / "secondary_verification"
        secondary_dir.mkdir(parents=True, exist_ok=True)
        context = (self.run_dir / "context" / "CONTEXT.md").read_text(encoding="utf-8")
        candidate = (self.run_dir / "CANDIDATE_PROOF.md").read_text(encoding="utf-8")
        claim_snapshot_hash = str(self.state.get("claim_snapshot_hash") or "")
        if not claim_snapshot_hash:
            raise ProjectError("Secondary verification requires a bound ClaimSnapshot")
        clients: dict[str, object] = {}

        def execute(name: str, directive: str) -> tuple[str, dict, object]:
            system = (
                "You are an independent secondary verifier. Return one JSON object with "
                "domain_verdict PASS/FAIL/INCONCLUSIVE, execution_status OK/ERROR, "
                "failure_reasons, findings, and cross_audit_notes. Mathematical doubts are "
                "not infrastructure errors."
            )
            prompt = f"""# Secondary check: {name}

{directive}

# Authorized context

{context}

# Candidate

{candidate}
"""
            client = None
            try:
                client = RoutedLLMClient(
                    self.model_router,
                    client_factory=create_client,
                    default_role="secondary_verifier",
                    archive_dir=self.run_dir / "archive" / "secondary" / name,
                    working_dir=self.run_dir / "gemini" / "secondary" / name,
                )
                response = client.call(
                    prompt=prompt,
                    system_prompt=system,
                    label=f"secondary_{name}",
                    archive_path=secondary_dir / f"{name}_call.md",
                    response_schema=AuditResultSchema,
                )
                result = parse_audit_response(name, response).to_dict()
            except Exception as exc:
                result = AuditResult.from_exception(name, exc).to_dict()
            result["audited_claim_snapshot_hash"] = claim_snapshot_hash
            return name, result, client

        results = {}
        with ThreadPoolExecutor(max_workers=len(checks)) as pool:
            futures = [pool.submit(execute, name, directive) for name, directive in checks.items()]
            for future in as_completed(futures):
                name, result, client = future.result()
                results[name] = result
                if client is not None:
                    clients[name] = client
                _write_json(secondary_dir / f"{name}.json", result)

        deterministic_failures = []
        dependency_path = self.run_dir / "audits" / "dependency_report.json"
        if not dependency_path.exists():
            deterministic_failures.append(
                "secondary dependency coverage: dependency report is missing"
            )
        else:
            dependency_report = json.loads(dependency_path.read_text(encoding="utf-8"))
            if not dependency_report.get("admissible", False):
                deterministic_failures.append(
                    "secondary dependency coverage: deterministic authority report is not admissible"
                )
            certificate_ids = dependency_report.get("computational_certificates", [])
            for certificate_id in certificate_ids:
                candidates = [
                    self.project.root / "certificates" / f"{certificate_id}{suffix}"
                    for suffix in (".json", ".md", ".txt")
                ]
                if not any(path.is_file() for path in candidates):
                    deterministic_failures.append(
                        f"secondary certificate rerun: missing certificate {certificate_id}"
                    )
        if self.hard_submit_gate:
            pre_submit_path = self.run_dir / "pre_submit_gate.json"
            if not pre_submit_path.exists() or not json.loads(
                pre_submit_path.read_text(encoding="utf-8")
            ).get("allowed", False):
                deterministic_failures.append(
                    "secondary scope reconstruction: pre-submit hard gate was not PASS"
                )

        failure_reasons = list(deterministic_failures)
        execution_errors = []
        inconclusive = []
        for name, data in results.items():
            result = normalize_audit_result(name, data)
            if result.execution_status == "ERROR":
                execution_errors.append(
                    f"secondary {name}: {result.execution_error or 'execution failed'}"
                )
            elif result.domain_verdict == "INCONCLUSIVE":
                inconclusive.append(name)
            elif result.domain_verdict == "FAIL":
                failure_reasons.extend(
                    result.failure_reasons or [f"secondary {name} returned FAIL"]
                )
        for client in clients.values():
            client.cleanup()
        result = {
            "schema_version": 1,
            "passed": not failure_reasons and not execution_errors and not inconclusive,
            "checks": results,
            "failure_reasons": failure_reasons,
            "execution_errors": execution_errors,
            "inconclusive_checks": inconclusive,
            "completed_at": utc_now(),
        }
        _write_json(secondary_dir / "gate.json", result)
        self.metrics["secondary_verification"] = {
            "calls": sum(getattr(client, "call_count", 0) for client in clients.values()),
            "success": result["passed"],
            "api_request_count": sum(_api_request_count(client) for client in clients.values()),
            "usage": _sum_usage(list(clients.values())),
        }
        return result

    def run_audits(self) -> tuple[dict[str, dict], AuditGate]:
        context = (self.run_dir / "context" / "CONTEXT.md").read_text(encoding="utf-8")
        candidate = (self.run_dir / "CANDIDATE_PROOF.md").read_text(encoding="utf-8")
        claim_snapshot_hash = str(self.state.get("claim_snapshot_hash") or "")
        if not claim_snapshot_hash:
            raise ProjectError("Audit execution requires a bound ClaimSnapshot")
        audits_dir = self.run_dir / "audits"
        audits_dir.mkdir(parents=True, exist_ok=True)
        clients = {}
        started = time.perf_counter()

        def execute(role: str) -> tuple[str, dict, object]:
            client = RoutedLLMClient(
                self.model_router,
                client_factory=create_client,
                default_role=role,
                archive_dir=self.run_dir / "archive" / role,
                working_dir=self.run_dir / "gemini" / role,
            )
            system, prompt = auditor_prompt(role, context, candidate)
            try:
                response = client.call(
                    prompt=prompt,
                    system_prompt=system,
                    label=f"audit_{role}",
                    archive_path=audits_dir / f"{role}_call.md",
                    response_schema=AuditResultSchema,
                )
                data = parse_audit_response(role, response).to_dict()
            except Exception as exc:
                data = AuditResult.from_exception(role, exc).to_dict()
            data["audited_claim_snapshot_hash"] = claim_snapshot_hash
            return role, data, client

        audits: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(AUDITOR_ROLES)) as pool:
            futures = [pool.submit(execute, role) for role in AUDITOR_ROLES]
            for future in as_completed(futures):
                role, data, client = future.result()
                audits[role] = data
                clients[role] = client
                _write_json(audits_dir / f"{role}.json", data)

        context_data = json.loads(
            (self.run_dir / "context" / "context.json").read_text(encoding="utf-8")
        )
        dependency_audit = normalize_audit_result(
            "dependency_auditor", audits["dependency_auditor"]
        )
        trust_kernel = TrustKernel.for_project(self.project)
        resolver = DependencyAuthorityResolver(
            foundations=trust_kernel.foundations,
            semantics=trust_kernel.semantics,
            project=self.project,
            notation_scope=context_data.get("notation_scope", ""),
        )
        dependency_report = resolver.resolve(dependency_audit.authority_uses)
        if dependency_audit.execution_status == "OK" and not dependency_report.admissible:
            dependency_audit.domain_verdict = "FAIL"
            dependency_audit.failure_reasons.extend(dependency_report.errors)
        audits["dependency_auditor"] = dependency_audit.to_dict()
        _write_json(
            audits_dir / "dependency_auditor.json",
            audits["dependency_auditor"],
        )
        _write_json(audits_dir / "dependency_report.json", dependency_report.to_dict())

        final_client = RoutedLLMClient(
            self.model_router,
            client_factory=create_client,
            default_role="final_proof_auditor",
            archive_dir=self.run_dir / "archive" / "final_auditor",
            working_dir=self.run_dir / "gemini" / "final_auditor",
        )
        system, prompt = final_auditor_prompt(context, candidate, audits)
        system += (
            " Your verdict is valid only for the exact ClaimSnapshot hash supplied in the prompt."
        )
        prompt += f"\n\n# Audited ClaimSnapshot\n\n`{claim_snapshot_hash}`\n"
        try:
            response = final_client.call(
                prompt=prompt,
                system_prompt=system,
                label="final_proof_auditor",
                archive_path=audits_dir / "final_proof_auditor_call.md",
                response_schema=AuditResultSchema,
            )
            final = parse_audit_response("final_proof_auditor", response).to_dict()
        except Exception as exc:
            final = AuditResult.from_exception("final_proof_auditor", exc).to_dict()
        final["audited_claim_snapshot_hash"] = claim_snapshot_hash
        audits["final_proof_auditor"] = final
        _write_json(audits_dir / "final_proof_auditor.json", final)

        for client in list(clients.values()) + [final_client]:
            client.cleanup()
        normalized = {role: normalize_audit_result(role, data) for role, data in audits.items()}
        specialist_pass = all(normalized[role].passed for role in AUDITOR_ROLES)
        criteria = final.get("criteria", {})
        failure_reasons = []
        execution_errors = []
        inconclusive_audits = []
        for role, result in normalized.items():
            if result.execution_status == "ERROR":
                execution_errors.append(
                    f"{role}: {result.execution_error or 'auditor execution failed'}"
                )
            elif result.domain_verdict == "INCONCLUSIVE":
                inconclusive_audits.append(role)
            elif result.mathematically_failed:
                reasons = result.failure_reasons or [f"{role} returned FAIL"]
                failure_reasons.extend(str(reason) for reason in reasons)
        blocked = self.state.get("blocked_dependencies", [])
        cycles = self.state.get("dependency_cycles", [])
        if blocked:
            failure_reasons.append("Non-PROVED dependencies in slice: " + ", ".join(blocked))
        if cycles:
            failure_reasons.append("Dependency cycle detected")
        if is_mock_config(self.config) and not self.project.load_project().get("demo", False):
            failure_reasons.append("Mock auditors cannot promote a non-demo project to PROVED")

        gate = AuditGate(
            forward_implication=bool(criteria.get("forward_implication")),
            converse_if_applicable=bool(criteria.get("converse_if_applicable")),
            exhaustive_cases=bool(criteria.get("exhaustive_cases")),
            parameter_ranges=bool(criteria.get("parameter_ranges")),
            boundary_cases=bool(criteria.get("boundary_cases")),
            dependencies_valid=(
                bool(criteria.get("dependencies_valid"))
                and dependency_report.admissible
                and normalized["dependency_auditor"].passed
                and not blocked
                and not cycles
            ),
            no_counterexample=(
                bool(criteria.get("no_counterexample"))
                and normalized["counterexample_hunter"].passed
            ),
            auditors_pass=bool(criteria.get("auditors_pass")) and specialist_pass,
            final_auditor_pass=normalized["final_proof_auditor"].passed,
            computational_evidence_separated=bool(criteria.get("computational_evidence_separated")),
            failure_reasons=failure_reasons,
            execution_errors=execution_errors,
            inconclusive_audits=inconclusive_audits,
            dependency_report=dependency_report.to_dict(),
            audited_claim_snapshot_hash=claim_snapshot_hash,
        )
        self.metrics["specialist_auditors"] = {
            "calls": sum(getattr(client, "call_count", 0) for client in clients.values()),
            "wall_clock_seconds": round(time.perf_counter() - started, 3),
            "success": specialist_pass,
            "retry_count": 0,
            "provider_retry_count": sum(
                getattr(client, "total_retries", 0) for client in clients.values()
            ),
            "api_request_count": sum(_api_request_count(client) for client in clients.values()),
            "billing_modes": sorted(
                {
                    mode
                    for client in clients.values()
                    if (mode := getattr(client, "billing_mode", None))
                }
            ),
            "usage": _sum_usage(list(clients.values())),
        }
        self.metrics["final_auditor"] = {
            "calls": getattr(final_client, "call_count", 0),
            "success": normalized["final_proof_auditor"].passed,
            "retry_count": 0,
            "provider_retry_count": getattr(final_client, "total_retries", 0),
            "api_request_count": _api_request_count(final_client),
            "billing_mode": getattr(final_client, "billing_mode", None),
            "usage": _usage_metrics(final_client),
        }
        self.metrics["routing"] = self.model_router.snapshot()
        self.metrics["pipelines"] = self.pipeline_scheduler.snapshot()
        _write_json(self.run_dir / "usage.json", self.metrics)
        return audits, gate
