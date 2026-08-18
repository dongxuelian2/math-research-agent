import json

from openprover.math_research.orchestrator import ResearchOrchestrator, build_run_preview
from openprover.math_research.project import ProjectStore


def test_openai_dry_run_is_read_only_and_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = ProjectStore.initialize(tmp_path / "project", "Dry Run")
    store.add_theorem("target", "Target", "Prove the target.")
    role = {
        "provider": "openai",
        "model": "configured-model",
        "reasoning_effort": "low",
        "timeout_seconds": 30,
        "max_retries": 2,
        "max_output_tokens": 100,
    }
    config = {
        "isolation": True,
        "roles": {
            "planner": dict(role),
            "worker": dict(role),
            "counterexample": dict(role),
            "auditor": dict(role),
            "final_auditor": dict(role),
        },
    }
    path = tmp_path / "models.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    preview = build_run_preview(
        store, "target", config_path=path, worker_count=1,
    )
    assert preview["request_sent"] is False
    assert preview["roles"]["planner"]["model"] == "configured-model"
    assert preview["roles"]["counterexample_hunter"]["provider"] == "openai"
    assert preview["credentials"]["planner"]["present"] is False
    assert store.load_theorem("target")["status"] == "OPEN"
    assert list((store.root / "runs").iterdir()) == []

    direct = ResearchOrchestrator(
        store,
        "target",
        config_path=path,
        worker_count=1,
        dry_run=True,
    ).run()
    assert direct["request_sent"] is False
    assert list((store.root / "runs").iterdir()) == []


def test_codex_cli_dry_run_shows_execution_plan_without_spawning(tmp_path, monkeypatch):
    store = ProjectStore.initialize(tmp_path / "project", "Codex Dry Run")
    store.add_theorem("target", "Target", "证明 $1+1=2$。")
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"placeholder")
    role = {
        "provider": "codex_cli",
        "model": None,
        "reasoning_effort": "high",
        "executable": str(executable),
        "timeout_seconds": 30,
        "max_retries": 1,
        "sandbox": "read-only",
    }
    config = {
        "isolation": True,
        "roles": {
            "planner": dict(role),
            "worker": dict(role),
            "auditor": dict(role),
            "final_auditor": dict(role),
        },
    }
    path = tmp_path / "models.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    def forbidden_popen(*_args, **_kwargs):
        raise AssertionError("dry-run must not spawn Codex")

    monkeypatch.setattr("subprocess.Popen", forbidden_popen)
    preview = build_run_preview(
        store, "target", config_path=path, worker_count=1,
    )
    planner = preview["roles"]["planner"]
    assert preview["request_sent"] is False
    assert planner["provider"] == "codex_cli"
    assert planner["requested_model"] is None
    assert planner["model_source"] == "codex_cli_default"
    assert planner["requested_reasoning_effort"] == "high"
    assert planner["resolved_executable"] == str(executable.resolve())
    assert planner["working_directory"].endswith("codex\\planner")
    assert planner["prompt_transport"] == "stdin"
    assert preview["context"]["character_count"] > 0
    assert preview["context"]["utf8_bytes"] >= preview["context"]["character_count"]
    assert preview["credentials"]["planner"] == {
        "source": "codex_cli_login",
        "status_check_performed": False,
        "openai_api_key_forwarded": False,
    }
    assert list((store.root / "runs").iterdir()) == []

    direct = ResearchOrchestrator(
        store,
        "target",
        config_path=path,
        worker_count=1,
        dry_run=True,
    ).run()
    assert direct["request_sent"] is False
    assert list((store.root / "runs").iterdir()) == []
