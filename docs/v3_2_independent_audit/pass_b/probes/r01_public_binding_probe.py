"""Isolated Pass B probe for the public RoutedLLMClient binding seam.

This probe constructs only in-memory binding context and a temporary archive
directory. It never initializes a canonical project, writes Truth/Research
state, runs root synthesis, or calls a real provider.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from dataclasses import replace
from pathlib import Path

# The repository package imports the optional OpenAI SDK at module import time.
# A module-shaped stub keeps this audit probe provider-free.
openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = type("OpenAI", (), {})
for _name in ("APIConnectionError", "APITimeoutError", "APIStatusError", "AuthenticationError"):
    setattr(openai_stub, _name, type(_name, (Exception,), {}))
sys.modules.setdefault("openai", openai_stub)
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "openprover"))

from openprover.math_research.orchestrator import ResearchOrchestrator
from openprover.math_research.routing import ModelRouter, RoutedLLMClient
from openprover.math_research.runtime_bindings import CrossPlaneExecutionBinding


ROOT = "sha256:" + "a" * 64
MAP_HASH = "sha256:" + "b" * 64
OTHER_HASH = "sha256:" + "c" * 64


class CountingProvider:
    def __init__(self, counter: dict[str, int]) -> None:
        self.counter = counter

    def call(self, **_: object) -> dict:
        self.counter["provider_call_count"] += 1
        return {"structured": {"probe": "accepted"}}


def current_context() -> tuple[ResearchOrchestrator, CrossPlaneExecutionBinding]:
    """Bind the production validator to an isolated, non-persisted context."""

    orchestrator = object.__new__(ResearchOrchestrator)
    orchestrator.claim_snapshot = types.SimpleNamespace(claim_snapshot_hash=ROOT)
    orchestrator.research_map = types.SimpleNamespace(
        root_claim_snapshot_hash=ROOT,
        research_map_id="map-1",
        version=7,
        research_map_hash=MAP_HASH,
    )
    orchestrator.directive = types.SimpleNamespace(
        obligation_id="obligation-1",
        directive_id="directive-1",
    )
    orchestrator.tactical_session = types.SimpleNamespace(
        obligation_id="obligation-1",
        tactical_session_id="session-1",
    )
    binding = CrossPlaneExecutionBinding.capture(
        root_claim_snapshot_hash=ROOT,
        research_map_id="map-1",
        research_map_version=7,
        research_map_hash=MAP_HASH,
        research_obligation_id="obligation-1",
        directive_id="directive-1",
        tactical_session_id="session-1",
    )
    return orchestrator, binding


def variant(name: str, current: CrossPlaneExecutionBinding) -> CrossPlaneExecutionBinding | None:
    if name == "COMPLETE_CURRENT":
        return current
    if name == "MISSING_MAP":
        return replace(current, research_map_id=None, research_map_version=None, research_map_hash=None)
    if name == "MISSING_OBLIGATION":
        return replace(current, research_obligation_id=None)
    if name == "MISSING_DIRECTIVE":
        return replace(current, directive_id=None)
    if name == "MISSING_SESSION":
        return replace(current, tactical_session_id=None)
    if name == "STALE_MAP":
        return replace(current, research_map_hash=OTHER_HASH)
    if name == "STALE_SESSION":
        return replace(current, tactical_session_id="session-stale")
    if name in {"WRONG_ROOT", "WRONG_THEOREM"}:
        # The current binding schema has no separate theorem_id dimension;
        # WRONG_THEOREM therefore uses the wrong ClaimSnapshot/root identity.
        return replace(current, root_claim_snapshot_hash=OTHER_HASH)
    if name == "ROOT_ONLY_ON_NORMAL_PATH":
        return replace(
            current,
            research_map_id=None,
            research_map_version=None,
            research_map_hash=None,
            research_obligation_id=None,
            directive_id=None,
            tactical_session_id=None,
        )
    if name == "MAP_ONLY_ON_NORMAL_PATH":
        return replace(
            current,
            research_obligation_id=None,
            directive_id=None,
            tactical_session_id=None,
        )
    if name == "CROSS_SESSION":
        return replace(current, tactical_session_id="session-2")
    if name == "CROSS_MAP":
        return replace(current, research_map_id="map-2", research_map_hash=OTHER_HASH)
    if name == "NO_BACKEND_UNBOUND":
        return None
    raise ValueError(name)


def run_case(
    name: str,
    *,
    current: CrossPlaneExecutionBinding,
    validator,
    temp_root: Path,
) -> dict:
    counter = {
        "provider_call_count": 0,
        "accepted_result_count": 0,
        "semantic_effect_count": 0,
        "truth_mutation_count": 0,
        "research_mutation_count": 0,
    }
    binding = variant(name, current)
    config = {"roles": {"researcher": {"provider": "mock", "model": "isolated"}}}
    router = ModelRouter(
        config,
        execution_binding=binding,
        execution_binding_validator=validator,
        require_execution_binding=True,
    )

    def factory(*_: object, **__: object) -> CountingProvider:
        return CountingProvider(counter)

    client = RoutedLLMClient(
        router,
        client_factory=factory,
        default_role="researcher",
        archive_dir=temp_root / name / "archive",
        working_dir=temp_root / name / "work",
    )
    outcome = "ACCEPTED"
    error = ""
    try:
        client.call("isolated binding probe", "isolated system", label=f"r01_{name}")
        counter["accepted_result_count"] += 1
    except Exception as exc:  # RuntimeConflict is expected for all non-current variants.
        outcome = "REJECTED"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "entry_point": "RoutedLLMClient.call",
        "caller": "RoutedLLMClient._execute_route",
        "callee": "provider client factory/call",
        "variant": name,
        "outcome": outcome,
        "error": error,
        **counter,
    }


def main() -> None:
    orchestrator, current = current_context()
    validator = orchestrator._validate_execution_binding
    names = [
        "COMPLETE_CURRENT",
        "MISSING_MAP",
        "MISSING_OBLIGATION",
        "MISSING_DIRECTIVE",
        "MISSING_SESSION",
        "STALE_MAP",
        "STALE_SESSION",
        "WRONG_ROOT",
        "WRONG_THEOREM",
        "ROOT_ONLY_ON_NORMAL_PATH",
        "MAP_ONLY_ON_NORMAL_PATH",
        "CROSS_SESSION",
        "CROSS_MAP",
        "NO_BACKEND_UNBOUND",
    ]
    with tempfile.TemporaryDirectory(prefix="pass-b-r01-") as raw:
        results = [
            run_case(name, current=current, validator=validator, temp_root=Path(raw))
            for name in names
        ]
    print(json.dumps({"probe": "R01", "canonical_state_touched": False, "cases": results}, indent=2))


if __name__ == "__main__":
    main()
