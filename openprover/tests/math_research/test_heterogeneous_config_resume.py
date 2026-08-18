from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprover.math_research.codex_cli_provider import CodexCLIClient
from openprover.math_research.pipelines import migrate_pipeline_state
from openprover.math_research.providers import load_model_config, resolve_role_config
from openprover.math_research.routing import migrate_routing_state
from openprover.math_research.project import ProjectError


def test_new_tier_config_and_sol_max_luna_max_validate(tmp_path):
    config = {
        "tiers": {
            "routine": {
                "provider": "codex_cli", "model": "gpt-5.6-luna",
                "reasoning_effort": "max", "allow_web_search": True,
            },
            "research": {
                "provider": "codex_cli", "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
            },
            "strategic": {
                "provider": "codex_cli", "model": "gpt-5.6-sol",
                "reasoning_effort": "max",
            },
        },
        "roles": {
            "planner": "strategic",
            "worker": "research",
            "final_proof_auditor": "strategic",
        },
    }
    path = tmp_path / "heterogeneous.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    loaded = load_model_config(path)
    assert resolve_role_config(loaded, "boundary")["model"] == "gpt-5.6-luna"
    assert resolve_role_config(loaded, "planner")["reasoning_effort"] == "max"

    config["tiers"]["routine"]["reasoning_effort"] = "ultra"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ProjectError, match="does not advertise ultra"):
        load_model_config(path)


def test_codex_search_flag_is_route_opt_in(tmp_path):
    executable = tmp_path / "codex-test.exe"
    executable.write_bytes(b"placeholder")
    client = CodexCLIClient(
        "gpt-5.6-luna",
        tmp_path / "archive",
        role_name="literature_searcher",
        working_dir=tmp_path / "work",
        executable=str(executable),
        reasoning_effort="max",
        allow_web_search=True,
        environment={"PATH": str(tmp_path)},
    )
    argv = client._argv(
        tmp_path, tmp_path / "final.txt", None, "max", web_search=True
    )
    assert "--search" in argv
    assert argv[argv.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="max"' in argv


def test_deterministic_checkpoint_migration_adds_every_queue_and_usage_bucket():
    routing = migrate_routing_state({"schema_version": 1, "legacy": "preserved"})
    assert routing["schema_version"] == 2
    assert routing["legacy"] == "preserved"
    assert set(routing["usage_by_tier"]) == {"routine", "research", "strategic"}
    assert "strategic_reservations" in routing

    pipeline = migrate_pipeline_state({"schema_version": 1, "legacy": "preserved"})
    assert pipeline["schema_version"] == 2
    assert pipeline["legacy"] == "preserved"
    assert set(pipeline["queues"]) == {
        "PROOF_QUEUE", "LITERATURE_QUEUE", "VERIFICATION_QUEUE", "BLOCKED_QUEUE"
    }
    assert set(pipeline["active"]) == {"proof", "literature", "verification"}
