import json
import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest
from pydantic import BaseModel

from openprover.math_research.codex_cli_provider import (
    BILLING_MODE,
    CodexCLIClient,
    CodexCLIProviderError,
    resolve_codex_command,
    resolve_codex_executable,
)
from openprover.math_research import cli
from openprover.math_research.providers import create_client, load_model_config
from openprover.math_research.project import ProjectError


def jsonl(*events):
    return "\n".join(json.dumps(event) for event in events) + "\n"


SUCCESS_JSONL = jsonl(
    {"type": "thread.started", "thread_id": "thread-test", "model": "resolved-model"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "CODEX_CLI_PROVIDER_OK"},
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 21,
            "cached_input_tokens": 5,
            "output_tokens": 8,
            "reasoning_output_tokens": 3,
        },
    },
)


class FakeProcess:
    next_pid = 4100

    def __init__(
        self,
        argv,
        *,
        stdout=SUCCESS_JSONL,
        stderr="",
        returncode=0,
        final_text="CODEX_CLI_PROVIDER_OK",
        timeout=False,
    ):
        self.argv = list(argv)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timeout = timeout
        self.communicate_calls = []
        self.terminated = False
        self.killed = False
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        if final_text is not None:
            output = Path(self.argv[self.argv.index("--output-last-message") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(final_text, encoding="utf-8")

    def communicate(self, input=None, timeout=None):
        self.communicate_calls.append({"input": input, "timeout": timeout})
        if self.timeout and len(self.communicate_calls) == 1:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode if self.terminated or self.killed else None

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakePopen:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.processes = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        process = FakeProcess(argv, **outcome)
        self.processes.append(process)
        return process


def make_executable(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "codex-test.exe"
    path.write_bytes(b"test executable placeholder")
    return path


def make_npm_cli(tmp_path):
    appdata = tmp_path / "appdata"
    prefix = appdata / "npm"
    shim = prefix / "codex.cmd"
    entrypoint = prefix / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = tmp_path / "node" / "node.exe"
    entrypoint.parent.mkdir(parents=True)
    node.parent.mkdir(parents=True)
    shim.write_text("@node node_modules\\@openai\\codex\\bin\\codex.js %*\n", encoding="utf-8")
    entrypoint.write_text("// test npm Codex entrypoint\n", encoding="utf-8")
    node.write_bytes(b"test node executable placeholder")
    environment = {
        "PATH": os.pathsep.join((str(prefix), str(node.parent))),
        "APPDATA": str(appdata),
        "USERPROFILE": str(tmp_path / "home"),
        "LOCALAPPDATA": str(tmp_path / "local"),
    }
    return shim, entrypoint, node, environment


def make_client(tmp_path, fake, **kwargs):
    executable = kwargs.pop("executable", make_executable(tmp_path))
    return CodexCLIClient(
        kwargs.pop("model", None),
        tmp_path / "archive",
        role_name=kwargs.pop("role_name", "auditor"),
        working_dir=tmp_path / "run" / "codex" / "auditor",
        executable=str(executable),
        reasoning_effort=kwargs.pop("reasoning_effort", "high"),
        timeout_seconds=kwargs.pop("timeout_seconds", 12),
        max_retries=kwargs.pop("max_retries", 0),
        retry_base_seconds=0,
        popen_factory=fake,
        sleep_fn=lambda _seconds: None,
        terminate_tree=lambda process: process.kill(),
        environment=kwargs.pop(
            "environment",
            {"PATH": "test", "OPENAI_API_KEY": "sk-super-secret-value"},
        ),
        **kwargs,
    )


def test_executable_detection_and_not_found(tmp_path):
    executable = make_executable(tmp_path)
    assert resolve_codex_executable(str(executable)) == str(executable.resolve())
    assert resolve_codex_executable(str(tmp_path / "missing.exe")) is None

    client = make_client(
        tmp_path,
        FakePopen([]),
        executable=tmp_path / "missing.exe",
    )
    with pytest.raises(CodexCLIProviderError) as caught:
        client.call("u", "s")
    assert caught.value.details["error_type"] == "codex_not_found"


@pytest.mark.skipif(os.name != "nt", reason="Windows npm shim discovery")
def test_path_npm_cli_wins_after_windowsapps_candidate(tmp_path):
    shim, entrypoint, node, environment = make_npm_cli(tmp_path)
    packaged_dir = tmp_path / "WindowsApps" / "OpenAI.Codex_test" / "app" / "resources"
    packaged_dir.mkdir(parents=True)
    (packaged_dir / "codex.exe").write_bytes(b"packaged desktop executable")
    environment["PATH"] = os.pathsep.join((str(packaged_dir), environment["PATH"]))

    resolution = resolve_codex_command(environment=environment)

    assert resolution.error_type is None
    assert resolution.command is not None
    assert resolution.command.executable == str(shim.resolve())
    assert resolution.command.source == "path"
    assert resolution.command.argv_prefix == (
        str(node.resolve()),
        str(entrypoint.resolve()),
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows npm shim discovery")
def test_npm_global_cli_fallback_when_not_on_path(tmp_path):
    shim, _entrypoint, node, environment = make_npm_cli(tmp_path)
    environment["PATH"] = str(node.parent)

    resolution = resolve_codex_command(environment=environment)

    assert resolution.command is not None
    assert resolution.command.executable == str(shim.resolve())
    assert resolution.command.source == "npm_global"


@pytest.mark.skipif(os.name != "nt", reason="WindowsApps path semantics")
def test_windowsapps_executable_is_rejected_before_process_start(tmp_path):
    packaged = tmp_path / "WindowsApps" / "OpenAI.Codex_test" / "app" / "resources" / "codex.exe"
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(b"packaged desktop executable")
    fake = FakePopen([])
    client = make_client(tmp_path, fake, executable=packaged)

    with pytest.raises(CodexCLIProviderError) as caught:
        client.call("u", "s")

    assert caught.value.details["error_type"] == ("windowsapps_packaged_executable_unsupported")
    assert fake.calls == []
    assert client.process_start_attempts == 0


def test_path_and_npm_cli_both_absent(tmp_path):
    environment = {
        "PATH": "",
        "APPDATA": str(tmp_path / "missing-appdata"),
        "USERPROFILE": str(tmp_path / "missing-home"),
        "LOCALAPPDATA": str(tmp_path / "missing-local"),
    }
    resolution = resolve_codex_command(environment=environment)
    assert resolution.command is None
    assert resolution.error_type == "codex_not_found"


def test_argv_stdin_unicode_jsonl_usage_and_redaction(tmp_path):
    fake = FakePopen([{}])
    client = make_client(tmp_path, fake, model=None, reasoning_effort="high")
    prompt = "证明：\n\\[\\sum_{k=1}^{n}(2k-1)=n^2\\]\n边界 n=0。"
    response = client.call(prompt, "你是严谨的审计员。", label="audit")

    argv, kwargs = fake.calls[0]
    assert argv[0].endswith("codex-test.exe")
    assert argv[1] == "exec"
    assert argv[-1] == "-"
    assert "--json" in argv
    assert "--output-last-message" in argv
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--cd") + 1].endswith("attempt-01")
    assert "--model" not in argv
    assert 'model_reasoning_effort="high"' in argv
    assert kwargs["shell"] is False
    assert kwargs["env"].get("OPENAI_API_KEY") is None
    assert kwargs["env"].get("CODEX_API_KEY") is None
    assert prompt not in " ".join(argv)

    stdin = fake.processes[0].communicate_calls[0]["input"]
    envelope = json.loads(stdin.split("\n", 1)[1])
    assert [message["role"] for message in envelope["messages"]] == ["system", "user"]
    assert envelope["messages"][1]["content"] == prompt
    assert response["result"] == "CODEX_CLI_PROVIDER_OK"
    assert response["provider"] == "codex_cli"
    assert response["model"] == "resolved-model"
    assert response["usage"] == {
        "input_tokens": 21,
        "output_tokens": 8,
        "reasoning_tokens": 3,
        "cached_tokens": 5,
        "total_tokens": 29,
        "cli_reported": True,
        "api_reported": False,
    }
    assert response["billing_mode"] == BILLING_MODE
    assert response["cost_usd"] is None
    assert response["raw"]["openai_api_key_forwarded"] is False
    assert client.request_count == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows npm shim launch")
def test_codex_cmd_uses_node_entrypoint_without_shell_injection(tmp_path):
    shim, entrypoint, node, environment = make_npm_cli(tmp_path)
    environment["OPENAI_API_KEY"] = "sk-super-secret-value"
    fake = FakePopen([{}])
    client = make_client(
        tmp_path,
        fake,
        executable=shim,
        environment=environment,
        reasoning_effort=None,
    )
    prompt = r"证明 $x^2\ge0$ & whoami | calc $(Get-ChildItem); 只返回结论。"

    response = client.call(prompt, "数学审计：保留 LaTeX。", label="shell-regression")

    argv, kwargs = fake.calls[0]
    assert argv[:3] == [str(node.resolve()), str(entrypoint.resolve()), "exec"]
    assert kwargs["shell"] is False
    assert prompt not in " ".join(argv)
    stdin = fake.processes[0].communicate_calls[0]["input"]
    envelope = json.loads(stdin.split("\n", 1)[1])
    assert envelope["messages"][1]["content"] == prompt
    assert kwargs["env"].get("OPENAI_API_KEY") is None
    assert response["result"] == "CODEX_CLI_PROVIDER_OK"


def test_output_schema_and_chat_role_serialization(tmp_path):
    fake = FakePopen([{}])
    client = make_client(tmp_path, fake)
    response = client.call(
        "return JSON",
        "system",
        json_schema={"type": "object"},
    )
    argv = fake.calls[0][0]
    schema_path = Path(argv[argv.index("--output-schema") + 1])
    assert json.loads(schema_path.read_text(encoding="utf-8")) == {"type": "object"}
    assert response["status"] == "completed"

    class StructuredContract(BaseModel):
        verdict: str

    fake = FakePopen([{}])
    client = make_client(tmp_path / "structured", fake)
    client.call("return JSON", "system", response_schema=StructuredContract)
    argv = fake.calls[0][0]
    schema_path = Path(argv[argv.index("--output-schema") + 1])
    assert (
        json.loads(schema_path.read_text(encoding="utf-8"))["properties"]["verdict"]["type"]
        == "string"
    )

    fake = FakePopen([{}])
    client = make_client(tmp_path / "chat", fake)
    client.chat(
        [
            {"role": "system", "content": "s"},
            {"role": "developer", "content": "d"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
    )
    stdin = fake.processes[0].communicate_calls[0]["input"]
    envelope = json.loads(stdin.split("\n", 1)[1])
    assert [item["role"] for item in envelope["messages"]] == [
        "system",
        "developer",
        "user",
        "assistant",
    ]


@pytest.mark.parametrize(
    ("stderr", "error_type", "retryable"),
    [
        ("Please run codex login; not logged in", "not_authenticated", False),
        ("model unknown-model not found", "invalid_model", False),
        (
            "unsupported model_reasoning_effort value enormous",
            "unsupported_reasoning_effort",
            False,
        ),
        ("429 rate limit reached", "rate_limited", True),
        ("ChatGPT usage limit reached", "usage_limit_reached", False),
        ("ordinary process failure", "process_failed", False),
    ],
)
def test_structured_process_failures(tmp_path, stderr, error_type, retryable):
    secret = "sk-super-secret-value"
    fake = FakePopen(
        [
            {
                "returncode": 1,
                "stderr": stderr + " " + secret,
                "stdout": "",
                "final_text": None,
            }
        ]
    )
    archive_path = tmp_path / "failure.md"
    client = make_client(tmp_path, fake, environment={"OPENAI_API_KEY": secret})
    with pytest.raises(CodexCLIProviderError) as caught:
        client.call("u", "s", archive_path=archive_path)
    details = caught.value.details
    assert details["error_type"] == error_type
    assert details["retryable"] is retryable
    assert secret not in str(caught.value)
    assert secret not in archive_path.read_text(encoding="utf-8")
    assert (
        secret not in archive_path.with_suffix(".raw.json").read_text(encoding="utf-8")
        if archive_path.with_suffix(".raw.json").exists()
        else True
    )


def test_timeout_is_structured_and_process_tree_is_killed(tmp_path):
    fake = FakePopen([{"timeout": True, "final_text": None}])
    client = make_client(tmp_path, fake, timeout_seconds=0.01)
    with pytest.raises(CodexCLIProviderError) as caught:
        client.call("u", "s")
    assert caught.value.details["error_type"] == "timeout"
    assert caught.value.retry_exhausted is True
    assert fake.processes[0].killed is True
    assert client.request_count == 1


def test_malformed_output_and_cancelled(tmp_path):
    malformed = FakePopen([{"stdout": "not-json\n", "final_text": "answer"}])
    client = make_client(tmp_path, malformed)
    with pytest.raises(CodexCLIProviderError) as caught:
        client.call("u", "s")
    assert caught.value.details["error_type"] == "malformed_output"

    cancelled = make_client(tmp_path / "cancel", FakePopen([]))
    cancelled.interrupt()
    with pytest.raises(CodexCLIProviderError) as caught:
        cancelled.call("u", "s")
    assert caught.value.details["error_type"] == "cancelled"


def test_only_explicit_transient_failure_retries(tmp_path):
    fake = FakePopen(
        [
            {"returncode": 1, "stderr": "503 service unavailable", "final_text": None},
            {},
        ]
    )
    client = make_client(tmp_path, fake, max_retries=1)
    response = client.call("u", "s")
    assert response["retry_count"] == 1
    assert client.request_count == 2
    assert client.total_retries == 1


def test_codex_factory_does_not_construct_openai_provider(tmp_path, monkeypatch):
    executable = make_executable(tmp_path)

    def fail_openai(*_args, **_kwargs):
        raise AssertionError("OpenAI provider must not be constructed")

    monkeypatch.setattr(
        "openprover.math_research.providers.OpenAIResponsesClient",
        fail_openai,
    )
    client = create_client(
        {
            "provider": "codex_cli",
            "model": None,
            "reasoning_effort": "low",
            "executable": str(executable),
        },
        tmp_path / "archive",
        role_name="worker",
        working_dir=tmp_path / "codex" / "worker",
    )
    assert isinstance(client, CodexCLIClient)


def test_mixed_provider_config_and_separate_reasoning_validation(tmp_path):
    codex = {"provider": "codex_cli", "model": None, "reasoning_effort": "minimal"}
    openai = {"provider": "openai", "model": "api-model", "reasoning_effort": "none"}
    config = {
        "roles": {
            "planner": dict(openai),
            "worker": dict(codex),
            "auditor": dict(codex),
            "final_auditor": dict(openai),
        }
    }
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    loaded = load_model_config(path)
    assert loaded["roles"]["worker"]["model"] is None

    config["roles"]["worker"]["reasoning_effort"] = "none"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ProjectError, match="Codex CLI reasoning_effort"):
        load_model_config(path)


def test_provider_smoke_uses_exactly_one_codex_process_and_no_api(tmp_path, monkeypatch):
    role = {"provider": "codex_cli", "model": None, "reasoning_effort": None}
    config = {
        "roles": {
            "planner": dict(role),
            "worker": dict(role),
            "auditor": dict(role),
            "final_auditor": dict(role),
        }
    }
    config_path = tmp_path / "codex.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    class SmokeClient:
        call_count = 0
        request_count = 0
        process_start_attempts = 0
        executable = "codex-test.exe"

        def call(self, **kwargs):
            self.call_count += 1
            self.request_count += 1
            self.process_start_attempts += 1
            assert kwargs["label"] == "codex_cli_provider_smoke"
            return {
                "result": "CODEX_CLI_PROVIDER_OK",
                "model": None,
                "duration_ms": 17,
                "retry_count": 0,
                "usage": None,
                "billing_mode": BILLING_MODE,
            }

        def cleanup(self):
            pass

    smoke_client = SmokeClient()

    def fake_create(role_config, archive_dir, **kwargs):
        assert role_config["provider"] == "codex_cli"
        assert role_config["max_retries"] == 0
        assert kwargs["role_name"] == "auditor"
        assert "codex" in str(kwargs["working_dir"])
        return smoke_client

    monkeypatch.setattr(cli, "create_client", fake_create)
    result = cli.dispatch(
        Namespace(
            command="provider-smoke",
            config=str(config_path),
            role="auditor",
            output=str(tmp_path / "logs"),
            expect="CODEX_CLI_PROVIDER_OK",
        )
    )
    assert result["passed"] is True
    assert result["codex_processes"] == 1
    assert result["api_requests"] == 0
    assert result["usage"] is None
    assert result["billing_mode"] == BILLING_MODE
