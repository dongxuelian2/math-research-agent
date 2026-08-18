"""Config-driven provider factory plus a deterministic no-cost smoke backend."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from openprover.llm import GLMClient, HFClient, LLMClient, MistralClient, OpenRouterClient

from .codex_cli_provider import (
    CODEX_REASONING_EFFORTS,
    CodexCLIClient,
)
from .openai_provider import OPENAI_REASONING_EFFORTS, OpenAIResponsesClient
from .project import ProjectError


SPECIALIST_ROLES = (
    "counterexample_hunter",
    "dependency_auditor",
    "exhaustiveness_auditor",
    "boundary_auditor",
)
SUPPORTED_PROVIDERS = {
    "mock",
    "claude_cli",
    "mistral",
    "glm",
    "openrouter",
    "local_openai_compatible",
    "openai",
    "codex_cli",
}


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

<!-- OPENPROVER_AUTHORITY_MANIFEST
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


class MockLLMClient:
    """Deterministic OpenProver-compatible client for tests and demo only."""

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

    def call(self, prompt: str, system_prompt: str, json_schema=None,
             label: str = "", web_search: bool = False,
             stream_callback=None, archive_path: Path | None = None,
             max_tokens: int | None = None, **kwargs) -> dict:
        if self._interrupted:
            raise RuntimeError("mock client interrupted")
        start = time.perf_counter()
        with self._lock:
            self.call_count += 1
            call_num = self.call_count
        outcome_match = re.search(
            r"MOCK_OUTCOME\s*:\s*([A-Z_]+)", prompt + "\n" + system_prompt
        )
        forced_outcome = outcome_match.group(1) if outcome_match else None
        if forced_outcome == "PROVIDER_FAILURE":
            raise RuntimeError("mock provider_failure")
        if forced_outcome == "USAGE_LIMIT_REACHED":
            raise RuntimeError("mock usage_limit_reached")
        result = self._result_for(label, prompt, system_prompt)
        if forced_outcome in {
            "CORRECT", "UNCERTAIN", "FLAWED", "CRITICALLY_FLAWED",
            "NO_PROGRESS",
        }:
            result = f"Mock forced outcome.\n\nVERDICT: {forced_outcome}"
        elif forced_outcome in {
            "EXACT_RESULT_FOUND", "PARTIAL_RESULT_FOUND",
            "NO_SUFFICIENT_RESULT_FOUND", "INSUFFICIENT_SEARCH",
        }:
            result = json.dumps({
                "literature_verdict": forced_outcome,
                "sources": [],
                "mock": True,
            })
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

    def _result_for(self, label: str, prompt: str, system_prompt: str) -> str:
        if label.startswith("literature_lead"):
            return json.dumps({
                "search_strategies": [
                    "exact_theorem", "equivalent_formulation", "method_search"
                ],
                "mock": True,
            })
        if label.startswith("literature_searcher"):
            return json.dumps({
                "sources": [{
                    "title": "Mock discovery source",
                    "stable_identifier": "mock:source:1",
                    "source_type": "original_paper",
                    "deep_read_required": True,
                }],
                "mock": True,
            })
        if label.startswith("literature_reader") or label.startswith("literature_deep_reader"):
            return json.dumps({
                "reader_verdict": "THEOREM_EXTRACTED",
                "theorems": [{"statement": "Mock theorem; never real authority."}],
                "mock": True,
            })
        if label.startswith("literature_authority_auditor"):
            return json.dumps({
                "verdict": "UNVERIFIED_REFERENCE",
                "mock": True,
            })
        if label.startswith("planner_step_1"):
            return '''I will ask three workers to pursue independent checks.

<OPENPROVER_ACTION>
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
</OPENPROVER_ACTION>'''
        if label.startswith("planner_step_"):
            proof = _mock_candidate_proof().replace('"""', "'''")
            return f'''The independent routes agree and the candidate is ready for the outer gate.

<OPENPROVER_ACTION>
action = "write_items"

[[items]]
slug = "candidate-proof"
content = """
{proof}
"""
</OPENPROVER_ACTION>

<OPENPROVER_ACTION>
action = "submit_proof"
proof_slug = "candidate-proof"
</OPENPROVER_ACTION>'''
        if label.startswith("worker_"):
            if label.endswith("_0"):
                return "Induction proves the identity, with base n=0 and step using (n+1)^2=n^2+2n+1. No computational evidence is needed."
            if label.endswith("_1"):
                return "The summand is k^2-(k-1)^2, so the finite sum telescopes from 0^2 to n^2. This is independent of the induction route."
            return "No counterexample occurs at n=0 or n=1. Indexing and the empty-sum convention are consistent; no division or gcd assumptions occur."
        if label.startswith("verifier_"):
            return "The worker's claim follows by direct algebra and respects the stated scope.\n\nVERDICT: CORRECT"
        if label.startswith("secondary_"):
            role = label.removeprefix("secondary_")
            return json.dumps({
                "role": role,
                "domain_verdict": "PASS",
                "execution_status": "OK",
                "findings": ["Independent bounded secondary check passed."],
                "failure_reasons": [],
                "cross_audit_notes": [],
                "authority_uses": [],
            })
        if label.startswith("certification_worker_verifier_") or label == "certification_secondary_reconstruction":
            return json.dumps({
                "role": label,
                "domain_verdict": "PASS",
                "execution_status": "OK",
                "findings": ["Bounded replay certification check passed."],
                "failure_reasons": [],
                "cross_audit_notes": [],
                "authority_uses": [],
            })
        if label == "discussion":
            return "# Discussion\n\nMocked demo completed two planner steps, three parallel workers, worker verification, and candidate submission. The outer project audit remains authoritative."
        if label.startswith("audit_"):
            role = label.removeprefix("audit_")
            authority_uses = []
            if role == "dependency_auditor":
                authority_uses = [{
                    "claim": "(n+1)^2=n^2+2n+1",
                    "claim_class": "PROJECT_THEOREM",
                    "authority_id": "demo-next-square",
                    "authority_type": "project_theorem",
                    "proof_location": "",
                }]
            return json.dumps({
                "role": role,
                "domain_verdict": "PASS",
                "execution_status": "OK",
                "findings": ["No defect found in the finite induction argument."],
                "failure_reasons": [],
                "cross_audit_notes": [],
                "computational_evidence": [],
                "authority_uses": authority_uses,
            })
        if label == "final_proof_auditor":
            return json.dumps({
                "role": "final_proof_auditor",
                "domain_verdict": "PASS",
                "execution_status": "OK",
                "failure_reasons": [],
                "cross_audit_notes": [],
                "authority_uses": [],
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
            })
        return "Mock response"


def load_model_config(path: str | Path) -> dict:
    path = Path(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Unable to load model config {path}: {exc}") from exc
    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise ProjectError("Model config roles must be an object")
    tiers = config.get("tiers")
    if tiers is not None:
        if not isinstance(tiers, dict):
            raise ProjectError("Model config tiers must be an object")
        missing_tiers = {"routine", "research", "strategic"} - set(tiers)
        if missing_tiers:
            raise ProjectError(
                "Model config missing tiers: " + ", ".join(sorted(missing_tiers))
            )
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
                    raise ProjectError(
                        f"Role {name} references unknown model tier {role!r}"
                    )
            elif isinstance(role, dict):
                if role.get("provider"):
                    _validate_role(name, role)
                elif role.get("default_tier") not in tiers:
                    raise ProjectError(
                        f"Role {name} requires a valid default_tier"
                    )
            else:
                raise ProjectError(f"Model role {name} must be a tier name or object")
        role_overrides = config.get("role_overrides", {})
        if not isinstance(role_overrides, dict):
            raise ProjectError("role_overrides must be an object")
        for name, override in role_overrides.items():
            if not isinstance(override, dict):
                raise ProjectError(f"Role override {name} must be an object")
        # Resolve the trust-critical routes now so a typo fails before a run.
        for name in ("planner", "worker", *SPECIALIST_ROLES, "final_proof_auditor"):
            resolve_role_config(config, name)
        return config
    if config.get("provider"):
        _validate_role("global", config)
        return config
    required = {"planner", "worker", "final_auditor"}
    missing = required - set(roles)
    if missing:
        raise ProjectError(f"Model config missing roles: {', '.join(sorted(missing))}")
    for name, role in roles.items():
        _validate_role(name, role)
    for name in SPECIALIST_ROLES:
        resolve_role_config(config, name)
    return config


def _validate_role(name: str, role: dict) -> None:
    if not isinstance(role, dict):
        raise ProjectError(f"Model role {name} must be an object")
    provider = role.get("provider")
    if provider not in SUPPORTED_PROVIDERS:
        raise ProjectError(f"Unsupported provider type in model config: {provider}")
    if provider == "codex_cli":
        model = role.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ProjectError(
                f"Codex CLI role {name} model must be null or a non-empty string"
            )
        if "api_key" in role:
            raise ProjectError(
                f"Codex CLI role {name} must not contain api_key; use `codex login`"
            )
        effort = role.get("reasoning_effort")
        if effort not in CODEX_REASONING_EFFORTS | {None}:
            allowed = ", ".join(sorted(CODEX_REASONING_EFFORTS))
            raise ProjectError(
                f"Invalid Codex CLI reasoning_effort for role {name}: {effort!r}; "
                f"expected one of {allowed}"
            )
        if model == "gpt-5.6-luna" and effort == "ultra":
            raise ProjectError(
                "Codex CLI 0.147.0 catalog does not advertise ultra for "
                f"gpt-5.6-luna ({name}); use max or lower"
            )
        executable = role.get("executable")
        if executable is not None and (
            not isinstance(executable, str) or not executable.strip()
        ):
            raise ProjectError(
                f"Codex CLI role {name} executable must be a non-empty string"
            )
        timeout = float(role.get("timeout_seconds", 600))
        retries = int(role.get("max_retries", 1))
        retry_base = float(role.get("retry_base_seconds", 1))
        sandbox = role.get("sandbox", "read-only")
        allow_web_search = role.get("allow_web_search", False)
        if timeout <= 0:
            raise ProjectError(
                f"Codex CLI role {name} timeout_seconds must be positive"
            )
        if not 0 <= retries <= 10:
            raise ProjectError(
                f"Codex CLI role {name} max_retries must be between 0 and 10"
            )
        if retry_base < 0:
            raise ProjectError(
                f"Codex CLI role {name} retry_base_seconds cannot be negative"
            )
        if sandbox not in {"read-only", "workspace-write"}:
            raise ProjectError(
                f"Codex CLI role {name} sandbox must be read-only or workspace-write"
            )
        if not isinstance(allow_web_search, bool):
            raise ProjectError(
                f"Codex CLI role {name} allow_web_search must be boolean"
            )
        return
    if not isinstance(role.get("model"), str) or not role["model"].strip():
        raise ProjectError(f"Model role {name} requires a non-empty model")
    if provider != "openai":
        return
    if "api_key" in role:
        raise ProjectError(
            f"Model role {name} must not contain api_key; use OPENAI_API_KEY"
        )
    effort = role.get("reasoning_effort")
    if effort not in OPENAI_REASONING_EFFORTS | {None}:
        allowed = ", ".join(sorted(OPENAI_REASONING_EFFORTS))
        raise ProjectError(
            f"Invalid OpenAI reasoning_effort for role {name}: {effort!r}; "
            f"expected one of {allowed}"
        )
    timeout = float(role.get("timeout_seconds", 600))
    retries = int(role.get("max_retries", 2))
    retry_base = float(role.get("retry_base_seconds", 1))
    output = int(role.get("max_output_tokens", 4096))
    if timeout <= 0:
        raise ProjectError(f"OpenAI role {name} timeout_seconds must be positive")
    if not 0 <= retries <= 10:
        raise ProjectError(f"OpenAI role {name} max_retries must be between 0 and 10")
    if retry_base < 0:
        raise ProjectError(f"OpenAI role {name} retry_base_seconds cannot be negative")
    if output < 1:
        raise ProjectError(f"OpenAI role {name} max_output_tokens must be positive")


def resolve_role_config(config: dict, role_name: str) -> dict:
    """Resolve actual agent roles while preserving legacy cheap_auditor configs."""
    if config.get("tiers") or (config.get("provider") and not config.get("roles")):
        from .routing import ModelRouter

        return ModelRouter(config).resolve(role_name, reserve=False).config
    roles = config.get("roles", {})
    aliases = [role_name]
    if role_name == "counterexample_hunter":
        aliases.append("counterexample")
    if role_name in SPECIALIST_ROLES:
        aliases.extend(["auditor", "cheap_auditor"])
    for alias in aliases:
        if alias in roles:
            return roles[alias]
    raise ProjectError(
        f"Model config has no provider for role {role_name}; configure the exact "
        "role, auditor, or legacy cheap_auditor"
    )


def is_mock_config(config: dict) -> bool:
    if isinstance(config.get("tiers"), dict):
        enabled = [
            route for route in config["tiers"].values()
            if isinstance(route, dict) and route.get("enabled", True)
        ]
        return bool(enabled) and all(route.get("provider") == "mock" for route in enabled)
    if config.get("provider"):
        return config.get("provider") == "mock"
    return all(
        role.get("provider") == "mock"
        for role in config.get("roles", {}).values()
    )


def create_client(role: dict, archive_dir: Path, *, role_name: str = "unknown",
                  working_dir: Path | None = None):
    provider = role.get("provider")
    model = role.get("model", "")
    effort = role.get("reasoning_effort")
    answer_reserve = int(role.get("answer_reserve", 4096))
    if provider == "mock":
        return MockLLMClient(model or "mock", archive_dir)
    if provider == "claude_cli":
        if not shutil.which("claude"):
            raise ProjectError("claude command not found; install/login to Claude Code or choose another provider")
        return LLMClient(model, archive_dir, effort=effort)
    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProjectError(
                f"OPENAI_API_KEY is required by the configured OpenAI role {role_name}"
            )
        return OpenAIResponsesClient(
            model,
            archive_dir,
            api_key=key,
            role_name=role_name,
            reasoning_effort=effort,
            timeout_seconds=float(role.get("timeout_seconds", 600)),
            max_retries=int(role.get("max_retries", 2)),
            retry_base_seconds=float(role.get("retry_base_seconds", 1)),
            max_output_tokens=int(role.get("max_output_tokens", 4096)),
            answer_reserve=answer_reserve,
            context_length=int(role.get("context_length", 200_000)),
            store=bool(role.get("store", False)),
        )
    if provider == "codex_cli":
        return CodexCLIClient(
            model or None,
            archive_dir,
            role_name=role_name,
            working_dir=(
                Path(working_dir)
                if working_dir is not None
                else Path(archive_dir).parent / "codex" / role_name
            ),
            executable=role.get("executable"),
            reasoning_effort=effort,
            timeout_seconds=float(role.get("timeout_seconds", 600)),
            max_retries=int(role.get("max_retries", 1)),
            retry_base_seconds=float(role.get("retry_base_seconds", 1)),
            answer_reserve=answer_reserve,
            context_length=int(role.get("context_length", 200_000)),
            sandbox=role.get("sandbox", "read-only"),
            allow_web_search=bool(role.get("allow_web_search", False)),
        )
    if effort is not None:
        raise ProjectError(
            f"reasoning_effort is configured for provider {provider}, but this OpenProver backend does not support it"
        )
    if provider == "mistral":
        if not os.environ.get("MISTRAL_API_KEY"):
            raise ProjectError("MISTRAL_API_KEY is required by the configured Mistral role")
        return MistralClient(model, archive_dir, answer_reserve=answer_reserve)
    if provider == "glm":
        key = os.environ.get("GLM_API_KEY")
        if not key:
            raise ProjectError("GLM_API_KEY is required by the configured GLM role")
        return GLMClient(
            model,
            archive_dir,
            api_key=key,
            base_url=role.get("base_url", "https://api.z.ai/api/coding/paas/v4"),
            answer_reserve=answer_reserve,
        )
    if provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ProjectError("OPENROUTER_API_KEY is required by the configured OpenRouter role")
        return OpenRouterClient(
            model,
            archive_dir,
            api_key=key,
            answer_reserve=answer_reserve,
        )
    if provider == "local_openai_compatible":
        return HFClient(
            model,
            archive_dir,
            base_url=role.get("base_url", "http://localhost:8000"),
            answer_reserve=answer_reserve,
            vllm=bool(role.get("vllm", True)),
        )
    raise ProjectError(f"Unsupported provider type in model config: {provider}")
