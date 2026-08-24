"""Config-driven provider factory plus a deterministic no-cost smoke backend."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .codex_cli_provider import CODEX_REASONING_EFFORTS, CodexCLIClient
from .gemini_provider import GeminiClient
from ..tools import make_tool_executor
from .openai_provider import (
    OPENAI_REASONING_EFFORTS,
    OpenAICompatibleResponsesClient,
    OpenAIResponsesClient,
)
from .project import ProjectError


SPECIALIST_ROLES = (
    "counterexample_hunter",
    "dependency_auditor",
    "exhaustiveness_auditor",
    "boundary_auditor",
)
SUPPORTED_PROVIDERS = {
    "gemini",
    "vertex_gemini",
    "codex_cli",
    "openai",
    "openai_compatible",
    "mock",
}


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_structured_output: bool
    supports_native_tools: bool
    supports_interrupt: bool
    supports_usage: bool
    supports_reasoning_tiers: bool


_PROVIDER_CAPABILITIES = {
    "gemini": ProviderCapabilities(True, True, True, True, True),
    "vertex_gemini": ProviderCapabilities(True, True, True, True, True),
    "codex_cli": ProviderCapabilities(True, False, True, True, True),
    "openai": ProviderCapabilities(True, True, True, True, True),
    "openai_compatible": ProviderCapabilities(True, True, True, True, True),
    "mock": ProviderCapabilities(True, False, True, True, False),
}


def provider_capabilities(provider: str) -> ProviderCapabilities:
    try:
        return _PROVIDER_CAPABILITIES[str(provider)]
    except KeyError as exc:
        raise ProjectError(f"Unsupported provider type in model config: {provider}") from exc


def _mock_candidate_proof() -> str:
    return r"""Summary: Complete induction proof for the demo odd-sum identity.

# Candidate Proof

## Scope

This proof concerns the demo theorem only and uses the allowed dependency `demo-next-square`.

## Statement

For every natural number $n$, the sum of the first $n$ positive odd integers is $n^2$:
\[
\sum_{k=1}^{n}(2k-1)=n^2.
\]

## Definitions

The empty sum for $n=0$ is defined to be $0$.

## Proof

We use induction on $n$. For $n=0$, the left side is the empty sum, hence $0=0^2$.
Assume for some $n\ge 0$ that $\sum_{k=1}^{n}(2k-1)=n^2$. Then
\[
\sum_{k=1}^{n+1}(2k-1)
=\sum_{k=1}^{n}(2k-1)+(2(n+1)-1)
=n^2+2n+1
=(n+1)^2,
\]
where the last equality is the proved dependency `demo-next-square`. This completes the induction.

## Converse / reconstruction

The theorem is an equality, not an iff classification; no converse is claimed.

## Boundary cases

The case $n=0$ was handled explicitly. The first positive case $n=1$ gives $1=1$.

## Computational evidence

None is used.

## Status

CANDIDATE_PROOF pending the outer independent audit gate.

<!-- MRA_AUTHORITY_MANIFEST
{
  "all_external_claims_classified": true,
  "branches_resolved": true,
  "unresolved": [],
  "authority_uses": [
    {
      "claim": "(n+1)^2=n^2+2n+1",
      "claim_class": "PROJECT_THEOREM",
      "authority_id": "demo-next-square",
      "authority_type": "project_theorem",
      "proof_location": ""
    }
  ],
  "source_paths": []
}
-->
"""


def _typed_worker_footer(
    *,
    event: str = "COMPLETED",
    verdict: str = "CORRECT",
    progress_signals: list[str] | None = None,
    high_value: bool = False,
) -> str:
    payload = {
        "event": event,
        "verdict": verdict,
        "failure_kind": "",
        "details": [],
        "progress_signals": progress_signals or [],
        "literature_request": None,
        "high_value": high_value,
    }
    return (
        "<!-- MRA_WORKER_EVENT\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n-->"
    )


class MockLLMClient:
    """Deterministic Math Research Agent-compatible client for tests and demo only."""

    context_length = 200_000
    answer_reserve = 4096

    def __init__(self, model: str, archive_dir: Path, **_: object):
        self.model = model
        self.archive_dir = Path(archive_dir)
        self.call_count = 0
        self.total_cost = 0.0
        self.total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
        }
        self._lock = threading.Lock()
        self._interrupted = False

    def interrupt(self):
        self._interrupted = True

    def soft_interrupt(self):
        self._interrupted = True

    def clear_interrupt(self):
        self._interrupted = False

    def clear_soft_interrupt(self):
        self._interrupted = False

    def cleanup(self):
        pass

    def call(
        self,
        prompt: str,
        system_prompt: str,
        json_schema=None,
        response_schema=None,
        label: str = "",
        web_search: bool = False,
        stream_callback=None,
        archive_path: Path | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> dict:
        if self._interrupted:
            raise RuntimeError("mock client interrupted")
        start = time.perf_counter()
        with self._lock:
            self.call_count += 1
            call_num = self.call_count
        outcome_match = re.search(r"MOCK_OUTCOME\s*:\s*([A-Z_]+)", prompt + "\n" + system_prompt)
        forced_outcome = outcome_match.group(1) if outcome_match else None
        if forced_outcome == "PROVIDER_FAILURE":
            raise RuntimeError("mock provider_failure")
        if forced_outcome == "USAGE_LIMIT_REACHED":
            raise RuntimeError("mock usage_limit_reached")
        result = self._result_for(label, prompt, system_prompt)
        if forced_outcome in {
            "CORRECT",
            "UNCERTAIN",
            "FLAWED",
            "CRITICALLY_FLAWED",
            "NO_PROGRESS",
        }:
            result = self._forced_structured_result(label, forced_outcome)
        elif forced_outcome in {
            "EXACT_RESULT_FOUND",
            "PARTIAL_RESULT_FOUND",
            "NO_SUFFICIENT_RESULT_FOUND",
            "INSUFFICIENT_SEARCH",
        }:
            result = json.dumps(
                {
                    "schema_version": 3,
                    "literature_verdict": forced_outcome,
                    "sources": [],
                }
            )
        duration_ms = max(1, int((time.perf_counter() - start) * 1000))
        response = {
            "result": result,
            "thinking": "",
            "cost": 0.0,
            "duration_ms": duration_ms,
            "raw": {
                "mock": True,
                "model": self.model,
                "label": label,
                "usage": {
                    "input_tokens": max(1, len(prompt) // 4),
                    "output_tokens": max(1, len(result) // 4),
                },
            },
            "finish_reason": "end_turn",
            "usage": {
                "input_tokens": max(1, len(prompt) // 4),
                "output_tokens": max(1, len(result) // 4),
            },
        }
        if json_schema is not None or response_schema is not None:
            try:
                response["structured"] = json.loads(result)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "mock structured response is not a complete JSON document"
                ) from exc
        with self._lock:
            self.total_usage["input_tokens"] += response["usage"]["input_tokens"]
            self.total_usage["output_tokens"] += response["usage"]["output_tokens"]
        if archive_path:
            path = Path(archive_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "---\nmock: true\n"
                f"label: {label}\ncall: {call_num}\n---\n\n"
                f"# Prompt\n\n{prompt}\n\n# Response\n\n{result}\n",
                encoding="utf-8",
            )
        return response

    @staticmethod
    def _forced_structured_result(label: str, outcome: str) -> str:
        if label.startswith("audit_") or label.startswith("secondary_"):
            role = label.removeprefix("audit_").removeprefix("secondary_")
            return json.dumps(
                {
                    "schema_version": 3,
                    "role": role,
                    "domain_verdict": "PASS" if outcome == "CORRECT" else "FAIL",
                    "execution_status": "OK",
                    "findings": [],
                    "failure_reasons": [] if outcome == "CORRECT" else [outcome],
                    "cross_audit_notes": [],
                    "computational_evidence": [],
                    "summary": "Deterministic structured mock result.",
                    "execution_error": "",
                    "authority_uses": [],
                    "criteria": {},
                }
            )
        return f"Mock forced outcome: {outcome}"

    def _result_for(self, label: str, prompt: str, system_prompt: str) -> str:
        if label == "project_plan":
            return json.dumps(
                {
                    "schema_version": 1,
                    "analysis_summary": "Mock supervisor decomposed the project purpose into one open child obligation.",
                    "subproblems": [
                        {
                            "id": "purpose-analysis",
                            "title": "Purpose analysis obligation",
                            "statement": "Derive and verify the first exact consequence required by the project purpose.",
                            "dependencies": [],
                            "tags": ["orchestrator", "mock"],
                            "branch": "main",
                            "proof_type": "NATURAL_LANGUAGE",
                            "claim_type": "implication",
                        }
                    ],
                    "open_questions": [
                        "The mock planner does not infer domain-specific child statements."
                    ],
                }
            )
        if label == "formalization_agent":
            return json.dumps(
                {
                    "schema_version": 3,
                    "status": "PENDING_FORMALIZATION",
                    "theorem_id": "",
                    "lean_code": "",
                    "compiler_output": "",
                    "certificate_path": "",
                    "certificate_sha256": "",
                    "summary": "Mock fixture does not run a Lean compiler.",
                    "error": "",
                }
            )
        if label.startswith("literature_lead"):
            return json.dumps(
                {
                    "schema_version": 3,
                    "search_tasks": [
                        {"strategy": "exact_theorem", "public_query": "exact theorem"},
                        {
                            "strategy": "equivalent_formulation",
                            "public_query": "equivalent formulation",
                        },
                        {"strategy": "method_search", "public_query": "method search"},
                    ],
                }
            )
        if label.startswith("literature_searcher"):
            return json.dumps(
                {
                    "schema_version": 3,
                    "sources": [
                        {
                            "title": "Mock discovery source",
                            "stable_identifier": "mock:source:1",
                            "source_type": "original_paper",
                            "deep_read_required": True,
                        }
                    ],
                }
            )
        if label.startswith("literature_reader") or label.startswith("literature_deep_reader"):
            return json.dumps(
                {
                    "schema_version": 3,
                    "reader_verdict": "THEOREM_EXTRACTED",
                    "theorems": [{"statement": "Mock theorem; never real authority."}],
                }
            )
        if label.startswith("literature_authority_auditor"):
            return json.dumps(
                {
                    "schema_version": 3,
                    "verdict": "UNVERIFIED_REFERENCE",
                }
            )
        if label.startswith("planner_step_1"):
            return '''I will ask three workers to pursue independent checks.

<MRA_ACTION>
action = "spawn"

[[tasks]]
summary = "Inductive route"
description = """Prove the stated odd-sum identity by induction, explicitly handling n=0 and using only the allowed dependency demo-next-square."""

[[tasks]]
summary = "Telescoping route"
description = """Explore the distinct telescoping-square route (k^2-(k-1)^2=2k-1) and report a complete proof or exact obstruction."""

[[tasks]]
summary = "Adversarial boundary check"
description = """Act adversarially: test n=0, n=1, the empty-sum convention, indexing, and any hidden assumptions. Seek a counterexample rather than helping the proof."""
</MRA_ACTION>'''
        if label.startswith("planner_step_"):
            proof = _mock_candidate_proof().replace('"""', "'''")
            return f'''The independent routes agree and the candidate is ready for the outer gate.

<MRA_ACTION>
action = "write_items"

[[items]]
slug = "candidate-proof"
content = """
{proof}
"""
</MRA_ACTION>

<MRA_ACTION>
action = "submit_proof"
proof_slug = "candidate-proof"
</MRA_ACTION>'''
        if label.startswith("worker_"):
            if label.endswith("_0"):
                body = "Induction proves the identity, with base n=0 and step using (n+1)^2=n^2+2n+1. No computational evidence is needed."
                return (
                    body
                    + "\n\n"
                    + _typed_worker_footer(
                        event="PROGRESS",
                        progress_signals=["VERIFIED_LEMMA"],
                        high_value=True,
                    )
                )
            if label.endswith("_1"):
                body = "The summand is k^2-(k-1)^2, so the finite sum telescopes from 0^2 to n^2. This is independent of the induction route."
                return (
                    body
                    + "\n\n"
                    + _typed_worker_footer(
                        event="PROGRESS",
                        progress_signals=["BRANCH_CLOSURE"],
                    )
                )
            body = "No counterexample occurs at n=0 or n=1. Indexing and the empty-sum convention are consistent; no division or gcd assumptions occur."
            return body + "\n\n" + _typed_worker_footer(event="COMPLETED")
        if label.startswith("verifier_"):
            return (
                "The worker's claim follows by direct algebra and respects the stated scope."
                "\n\nVERDICT: CORRECT\n\n"
                + _typed_worker_footer(event="COMPLETED", verdict="CORRECT")
            )
        if label.startswith("secondary_"):
            role = label.removeprefix("secondary_")
            return json.dumps(
                {
                    "schema_version": 3,
                    "role": role,
                    "domain_verdict": "PASS",
                    "execution_status": "OK",
                    "findings": ["Independent bounded secondary check passed."],
                    "failure_reasons": [],
                    "cross_audit_notes": [],
                    "authority_uses": [],
                    "summary": "Independent bounded secondary check passed.",
                    "execution_error": "",
                    "criteria": {},
                }
            )
        if (
            label.startswith("certification_worker_verifier_")
            or label == "certification_secondary_reconstruction"
        ):
            return json.dumps(
                {
                    "schema_version": 3,
                    "role": label,
                    "domain_verdict": "PASS",
                    "execution_status": "OK",
                    "findings": ["Bounded replay certification check passed."],
                    "failure_reasons": [],
                    "cross_audit_notes": [],
                    "authority_uses": [],
                    "summary": "Bounded replay certification check passed.",
                    "execution_error": "",
                    "criteria": {},
                }
            )
        if label == "discussion":
            return "# Discussion\n\nMocked demo completed two planner steps, three parallel workers, worker verification, and candidate submission. The outer project audit remains authoritative."
        if label.startswith("audit_"):
            role = label.removeprefix("audit_")
            authority_uses = []
            if role == "dependency_auditor":
                authority_uses = [
                    {
                        "claim": "(n+1)^2=n^2+2n+1",
                        "claim_class": "PROJECT_THEOREM",
                        "authority_id": "demo-next-square",
                        "authority_type": "project_theorem",
                        "proof_location": "",
                    }
                ]
            return json.dumps(
                {
                    "schema_version": 3,
                    "role": role,
                    "domain_verdict": "PASS",
                    "execution_status": "OK",
                    "findings": ["No defect found in the finite induction argument."],
                    "failure_reasons": [],
                    "cross_audit_notes": [],
                    "computational_evidence": [],
                    "authority_uses": authority_uses,
                    "summary": "Structured mock specialist audit passed.",
                    "execution_error": "",
                    "criteria": {},
                }
            )
        if label == "final_proof_auditor":
            return json.dumps(
                {
                    "schema_version": 3,
                    "role": "final_proof_auditor",
                    "domain_verdict": "PASS",
                    "execution_status": "OK",
                    "failure_reasons": [],
                    "cross_audit_notes": [],
                    "authority_uses": [],
                    "execution_error": "",
                    "summary": "The demo induction is complete and uses only the allowed proved identity.",
                    "criteria": {
                        "forward_implication": True,
                        "converse_if_applicable": True,
                        "exhaustive_cases": True,
                        "parameter_ranges": True,
                        "boundary_cases": True,
                        "dependencies_valid": True,
                        "no_counterexample": True,
                        "auditors_pass": True,
                        "computational_evidence_separated": True,
                    },
                }
            )
        return "Mock response"


def _normalize_toml_model_config(raw: dict, path: Path) -> dict:
    if not isinstance(raw, dict):
        raise ProjectError(f"TOML model config must be an object: {path}")
    if raw.get("version", 1) != 1:
        raise ProjectError(f"Unsupported TOML model config version: {raw.get('version')}")
    models = raw.get("models")
    roles = raw.get("roles")
    if not isinstance(models, dict) or not models:
        raise ProjectError("TOML model config requires a non-empty [models] catalog")
    if not isinstance(roles, dict) or not roles:
        raise ProjectError("TOML model config requires a non-empty [roles] mapping")

    defaults = raw.get("role_defaults", {})
    if not isinstance(defaults, dict):
        raise ProjectError("TOML role_defaults must be a table")
    configured_tools = raw.get("tools", {})
    if configured_tools is not None and not isinstance(configured_tools, dict):
        raise ProjectError("TOML tools must be a table")
    default_tools = configured_tools.get("default", []) if configured_tools else []
    resolved_roles: dict[str, dict] = {}
    for role_name, role in roles.items():
        if not isinstance(role_name, str) or not isinstance(role, dict):
            raise ProjectError("TOML roles must map names to tables")
        model_id = role.get("model")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ProjectError(f"Role {role_name} requires a model name")
        model = models.get(model_id)
        if not isinstance(model, dict):
            raise ProjectError(f"Role {role_name} references unknown model {model_id!r}")
        if not isinstance(model.get("provider"), str) or not model["provider"].strip():
            raise ProjectError(f"Model {model_id} requires a provider")
        if "model" not in model and model["provider"] != "codex_cli":
            raise ProjectError(f"Model {model_id} requires a provider model field")
        merged = dict(defaults)
        merged.update(model)
        merged.update(role)
        role_tools = list(default_tools or [])
        role_tools.extend(configured_tools.get(role_name, []) if configured_tools else [])
        if role_tools:
            merged["tools"] = list(dict.fromkeys(role_tools))
        merged["model_id"] = model_id
        merged["model"] = model.get("model")
        resolved_roles[role_name] = merged

    runtime = raw.get("runtime", {})
    if not isinstance(runtime, dict):
        raise ProjectError("TOML runtime must be a table")
    budget_seconds = int(runtime.get("budget_seconds", 900))
    if budget_seconds <= 0:
        raise ProjectError("runtime.budget_seconds must be positive")
    config = {
        "schema_version": 1,
        "description": raw.get("description", "TOML model configuration"),
        "isolation": bool(runtime.get("isolation", True)),
        "history_budget": int(runtime.get("history_budget", 0)),
        "budget": {
            "mode": str(runtime.get("budget_mode", "time")),
            "limit": budget_seconds,
            "conclude_after": float(runtime.get("conclude_after", 0.99)),
        },
        "roles": resolved_roles,
    }
    routing = runtime.get("routing")
    if routing is not None:
        if not isinstance(routing, dict):
            raise ProjectError("runtime.routing must be a table")
        config["routing"] = dict(routing)
    if configured_tools:
        config["tools"] = dict(configured_tools)
    return config


def load_model_config(path: str | Path) -> dict:
    path = Path(path)
    if path.suffix.lower() != ".toml":
        raise ProjectError(f"Model config must be TOML: {path}")
    try:
        config = _normalize_toml_model_config(
            tomllib.loads(path.read_text(encoding="utf-8")), path
        )
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ProjectError(f"Unable to load model config {path}: {exc}") from exc
    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise ProjectError("Model config roles must be an object")
    tool_map = config.get("tools", {})
    if not isinstance(tool_map, dict):
        raise ProjectError("Model config tools must be an object")
    from ..tools import normalize_tool_names

    for role_name, tool_names in tool_map.items():
        if not isinstance(role_name, str):
            raise ProjectError("Model config tool role names must be strings")
        try:
            normalize_tool_names(tool_names)
        except ValueError as exc:
            raise ProjectError(f"Invalid provider tools for role {role_name}: {exc}") from exc
    tiers = config.get("tiers")
    if tiers is not None:
        if not isinstance(tiers, dict):
            raise ProjectError("Model config tiers must be an object")
        missing_tiers = {"routine", "research", "strategic"} - set(tiers)
        if missing_tiers:
            raise ProjectError("Model config missing tiers: " + ", ".join(sorted(missing_tiers)))
        for name, route in tiers.items():
            if name not in {"routine", "research", "strategic"}:
                raise ProjectError(f"Unknown model tier: {name}")
            if not isinstance(route, dict):
                raise ProjectError(f"Model tier {name} must be an object")
            if route.get("enabled", True) is not False:
                _validate_role(f"tier:{name}", route)
        for name, role in roles.items():
            if isinstance(role, str):
                if role not in tiers:
                    raise ProjectError(f"Role {name} references unknown model tier {role!r}")
            elif isinstance(role, dict):
                if role.get("provider"):
                    _validate_role(name, role)
                elif role.get("default_tier") not in tiers:
                    raise ProjectError(f"Role {name} requires a valid default_tier")
            else:
                raise ProjectError(f"Model role {name} must be a tier name or object")
        role_overrides = config.get("role_overrides", {})
        if not isinstance(role_overrides, dict):
            raise ProjectError("role_overrides must be an object")
        for name, override in role_overrides.items():
            if not isinstance(override, dict):
                raise ProjectError(f"Role override {name} must be an object")
        # Resolve every trust-critical role now so a typo fails before a run.
        for name in (
            "planner",
            "worker",
            *SPECIALIST_ROLES,
            "final_proof_auditor",
        ):
            resolve_role_config(config, name)
        return config
    if config.get("provider"):
        _validate_role("global", config)
        return config
    for name, role in roles.items():
        _validate_role(name, role)
    for name in ("planner", "worker", *SPECIALIST_ROLES, "final_proof_auditor"):
        resolve_role_config(config, name)
    return config


def _validate_role(name: str, role: dict) -> None:
    if not isinstance(role, dict):
        raise ProjectError(f"Model role {name} must be an object")
    provider = role.get("provider")
    if provider not in SUPPORTED_PROVIDERS:
        raise ProjectError(f"Unsupported provider type in model config: {provider}")
    if provider in {"gemini", "vertex_gemini"}:
        if not isinstance(role.get("model"), str) or not role["model"].strip():
            raise ProjectError(f"Gemini role {name} requires a non-empty model")
        timeout = float(role.get("timeout_seconds", 600))
        retries = int(role.get("max_retries", 2))
        retry_base = float(role.get("retry_base_seconds", 1))
        output = int(role.get("max_output_tokens", 8192))
        if timeout <= 0:
            raise ProjectError(f"Gemini role {name} timeout_seconds must be positive")
        if not 0 <= retries <= 10:
            raise ProjectError(f"Gemini role {name} max_retries must be between 0 and 10")
        if retry_base < 0:
            raise ProjectError(f"Gemini role {name} retry_base_seconds cannot be negative")
        if output < 1:
            raise ProjectError(f"Gemini role {name} max_output_tokens must be positive")
        if provider == "vertex_gemini" and not role.get("project"):
            raise ProjectError(f"Vertex Gemini role {name} requires project")
        return
    if provider == "openai":
        model = role.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ProjectError(f"OpenAI role {name} requires a non-empty model")
        api_key_env = role.get("api_key_env", "OPENAI_API_KEY")
        if not isinstance(api_key_env, str) or not api_key_env.strip():
            raise ProjectError(f"OpenAI role {name} api_key_env must be non-empty")
        effort = role.get("reasoning_effort")
        if effort not in OPENAI_REASONING_EFFORTS | {None}:
            raise ProjectError(f"OpenAI role {name} has invalid reasoning_effort: {effort}")
        _validate_common_provider_limits(name, role)
        return
    if provider == "openai_compatible":
        model = role.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ProjectError(f"OpenAI-compatible role {name} requires a non-empty model")
        api_key_env = role.get("api_key_env", "OPENAI_API_KEY")
        if not isinstance(api_key_env, str) or not api_key_env.strip():
            raise ProjectError(f"OpenAI-compatible role {name} api_key_env must be non-empty")
        base_url = role.get("base_url")
        base_url_env = role.get("base_url_env")
        if not isinstance(base_url, str) and not isinstance(base_url_env, str):
            raise ProjectError(f"OpenAI-compatible role {name} requires base_url or base_url_env")
        if isinstance(base_url, str) and not base_url.strip():
            raise ProjectError(f"OpenAI-compatible role {name} base_url must be non-empty")
        if isinstance(base_url_env, str) and not base_url_env.strip():
            raise ProjectError(f"OpenAI-compatible role {name} base_url_env must be non-empty")
        effort = role.get("reasoning_effort")
        if effort not in OPENAI_REASONING_EFFORTS | {None}:
            raise ProjectError(
                f"OpenAI-compatible role {name} has invalid reasoning_effort: {effort}"
            )
        _validate_common_provider_limits(name, role)
        return
    if provider == "codex_cli":
        model = role.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ProjectError(f"Codex CLI role {name} model must be null or non-empty")
        effort = role.get("reasoning_effort")
        if effort not in CODEX_REASONING_EFFORTS | {None}:
            raise ProjectError(f"Codex CLI reasoning_effort for role {name} is invalid: {effort}")
        if role.get("sandbox", "read-only") not in {"read-only", "workspace-write"}:
            raise ProjectError(f"Codex CLI role {name} has invalid sandbox")
        if not isinstance(role.get("allow_web_search", False), bool):
            raise ProjectError(f"Codex CLI role {name} allow_web_search must be boolean")
        _validate_common_provider_limits(name, role, require_output=False)
        return


def _validate_common_provider_limits(name: str, role: dict, *, require_output: bool = True) -> None:
    timeout = float(role.get("timeout_seconds", 600))
    retries = int(role.get("max_retries", 2))
    retry_base = float(role.get("retry_base_seconds", 1))
    output = int(role.get("max_output_tokens", 8192))
    if timeout <= 0:
        raise ProjectError(f"Provider role {name} timeout_seconds must be positive")
    if not 0 <= retries <= 10:
        raise ProjectError(f"Provider role {name} max_retries must be between 0 and 10")
    if retry_base < 0:
        raise ProjectError(f"Provider role {name} retry_base_seconds cannot be negative")
    if require_output and output < 1:
        raise ProjectError(f"Provider role {name} max_output_tokens must be positive")


def resolve_role_config(config: dict, role_name: str) -> dict:
    """Resolve one exact role from the active configuration."""
    if config.get("tiers") or (config.get("provider") and not config.get("roles")):
        from .routing import ModelRouter

        return ModelRouter(config).resolve(role_name, reserve=False).config
    roles = config.get("roles", {})
    if role_name in roles and isinstance(roles[role_name], dict):
        return roles[role_name]
    from .routing import role_config_names

    for alias in role_config_names(role_name)[1:]:
        if alias in roles and isinstance(roles[alias], dict):
            return roles[alias]
    raise ProjectError(f"Model config has no exact provider for role {role_name}")


def is_mock_config(config: dict) -> bool:
    if isinstance(config.get("tiers"), dict):
        enabled = [
            route
            for route in config["tiers"].values()
            if isinstance(route, dict) and route.get("enabled", True)
        ]
        return bool(enabled) and all(route.get("provider") == "mock" for route in enabled)
    if config.get("provider"):
        return config.get("provider") == "mock"
    return all(role.get("provider") == "mock" for role in config.get("roles", {}).values())


def create_client(
    role: dict,
    archive_dir: Path,
    *,
    role_name: str = "unknown",
    working_dir: Path | None = None,
    tool_event_sink=None,
):
    provider = role.get("provider")
    model = role.get("model", "")
    answer_reserve = int(role.get("answer_reserve", 4096))
    if provider == "mock":
        return MockLLMClient(model or "mock", archive_dir)
    if provider in {"gemini", "vertex_gemini"}:
        vertex = provider == "vertex_gemini"
        api_key_env = str(role.get("api_key_env", "GEMINI_API_KEY"))
        api_key = (role.get("api_key") or os.environ.get(api_key_env)) if not vertex else None
        if not vertex and not api_key:
            raise ProjectError(
                f"{api_key_env} is required by the configured Gemini role {role_name}"
            )
        return GeminiClient(
            model,
            archive_dir,
            api_key=api_key,
            base_url=role.get("base_url") or _env_value(role.get("base_url_env")),
            project=role.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=role.get("location", "us-central1"),
            access_token=role.get("access_token") or os.environ.get("GOOGLE_CLOUD_ACCESS_TOKEN"),
            vertex=vertex,
            timeout_seconds=float(role.get("timeout_seconds", 600)),
            max_retries=int(role.get("max_retries", 2)),
            retry_base_seconds=float(role.get("retry_base_seconds", 1)),
            max_output_tokens=int(role.get("max_output_tokens", 8192)),
            answer_reserve=answer_reserve,
            context_length=int(role.get("context_length", 1_000_000)),
            temperature=(
                float(role["temperature"]) if role.get("temperature") is not None else None
            ),
            tool_executor=make_tool_executor(
                role.get("tools"),
                workspace_root=role.get("workspace_root") or working_dir,
                max_output_chars=int(role.get("tool_output_chars", 20_000)),
                default_timeout_seconds=float(role.get("tool_timeout_seconds", 30)),
                tool_event_sink=tool_event_sink,
                actor=role_name,
            ),
            max_tool_rounds=int(role.get("max_tool_rounds", 8)),
        )
    if provider == "codex_cli":
        return CodexCLIClient(
            model or None,
            archive_dir,
            role_name=role_name,
            working_dir=working_dir or archive_dir,
            executable=role.get("executable"),
            reasoning_effort=role.get("reasoning_effort"),
            timeout_seconds=float(role.get("timeout_seconds", 600)),
            max_retries=int(role.get("max_retries", 1)),
            retry_base_seconds=float(role.get("retry_base_seconds", 1)),
            answer_reserve=answer_reserve,
            context_length=int(role.get("context_length", 200_000)),
            sandbox=role.get("sandbox", "read-only"),
            allow_web_search=bool(role.get("allow_web_search", False)),
        )
    if provider == "openai":
        api_key_env = str(role.get("api_key_env", "OPENAI_API_KEY"))
        api_key = role.get("api_key") or os.environ.get(api_key_env)
        if not api_key:
            raise ProjectError(
                f"{api_key_env} is required by the configured OpenAI role {role_name}"
            )
        return OpenAIResponsesClient(
            model,
            archive_dir,
            api_key=api_key,
            role_name=role_name,
            reasoning_effort=role.get("reasoning_effort"),
            timeout_seconds=float(role.get("timeout_seconds", 600)),
            max_retries=int(role.get("max_retries", 2)),
            retry_base_seconds=float(role.get("retry_base_seconds", 1)),
            max_output_tokens=int(role.get("max_output_tokens", 8192)),
            answer_reserve=answer_reserve,
            context_length=int(role.get("context_length", 200_000)),
            store=bool(role.get("store", False)),
            base_url=role.get("base_url") or _env_value(role.get("base_url_env")),
            api_key_env=api_key_env,
            tool_executor=make_tool_executor(
                role.get("tools"),
                workspace_root=role.get("workspace_root") or working_dir,
                max_output_chars=int(role.get("tool_output_chars", 20_000)),
                default_timeout_seconds=float(role.get("tool_timeout_seconds", 30)),
                tool_event_sink=tool_event_sink,
                actor=role_name,
            ),
            max_tool_rounds=int(role.get("max_tool_rounds", 8)),
        )
    if provider == "openai_compatible":
        api_key_env = str(role.get("api_key_env", "OPENAI_API_KEY"))
        api_key = role.get("api_key") or os.environ.get(api_key_env)
        base_url = role.get("base_url") or _env_value(role.get("base_url_env"))
        if not api_key:
            raise ProjectError(
                f"{api_key_env} is required by the configured OpenAI-compatible role {role_name}"
            )
        if not base_url:
            raise ProjectError(
                f"base_url or base_url_env is required by the configured OpenAI-compatible role {role_name}"
            )
        return OpenAICompatibleResponsesClient(
            model,
            archive_dir,
            api_key=api_key,
            api_key_env=api_key_env,
            base_url=str(base_url),
            role_name=role_name,
            reasoning_effort=role.get("reasoning_effort"),
            timeout_seconds=float(role.get("timeout_seconds", 600)),
            max_retries=int(role.get("max_retries", 2)),
            retry_base_seconds=float(role.get("retry_base_seconds", 1)),
            max_output_tokens=int(role.get("max_output_tokens", 8192)),
            answer_reserve=answer_reserve,
            context_length=int(role.get("context_length", 200_000)),
            store=bool(role.get("store", False)),
            tool_executor=make_tool_executor(
                role.get("tools"),
                workspace_root=role.get("workspace_root") or working_dir,
                max_output_chars=int(role.get("tool_output_chars", 20_000)),
                default_timeout_seconds=float(role.get("tool_timeout_seconds", 30)),
                tool_event_sink=tool_event_sink,
                actor=role_name,
            ),
            max_tool_rounds=int(role.get("max_tool_rounds", 8)),
        )
    raise ProjectError(f"Unsupported provider type in model config: {provider}")


def _env_value(name: object) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    return os.environ.get(name)
