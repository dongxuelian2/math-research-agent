"""Subscription-backed Codex CLI adapter for the math-research layer."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openprover.llm._base import archive


CODEX_REASONING_EFFORTS = frozenset(
    # ``minimal`` remains accepted for legacy configs.  The installed 0.147.0
    # GPT-5.6 catalog advertises low/medium/high/xhigh/max and Sol-only ultra.
    {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
)
BILLING_MODE = "chatgpt_codex_subscription"
_TRANSIENT_MARKERS = (
    "connection reset",
    "connection aborted",
    "temporarily unavailable",
    "service unavailable",
    "gateway timeout",
    "network error",
    "502",
    "503",
    "504",
)


@dataclass(frozen=True)
class CodexCLICommand:
    """A validated Codex CLI plus its shell-free process argv prefix."""

    executable: str
    argv_prefix: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class CodexCLIResolution:
    command: CodexCLICommand | None
    error_type: str | None = None
    explanation: str = ""
    rejected_candidate: str | None = None


def _is_windowsapps_executable(path: Path) -> bool:
    normalized = str(path).replace("/", "\\").casefold()
    return "\\windowsapps\\" in normalized


def _safe_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _path_candidates(name: str, environment: dict[str, str]) -> list[Path]:
    path_value = environment.get("PATH", "")
    requested = Path(name)
    if requested.is_absolute() or requested.parent != Path("."):
        return [Path(os.path.expandvars(name)).expanduser()]

    if os.name == "nt":
        suffixes = ("",) if requested.suffix else (".cmd", ".exe", ".com")
    else:
        suffixes = ("",)
    return [
        Path(entry) / f"{name}{suffix}"
        for entry in path_value.split(os.pathsep)
        if entry
        for suffix in suffixes
    ]


def _npm_entrypoint(shim: Path) -> Path | None:
    candidates = (
        shim.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        shim.parent.parent / "@openai" / "codex" / "bin" / "codex.js",
    )
    return next((candidate for candidate in candidates if _safe_file(candidate)), None)


def _node_executable(shim: Path, environment: dict[str, str]) -> Path | None:
    adjacent = shim.parent / ("node.exe" if os.name == "nt" else "node")
    if _safe_file(adjacent):
        return adjacent
    candidate = shutil.which("node", path=environment.get("PATH", ""))
    if candidate and _safe_file(Path(candidate)):
        return Path(candidate)
    return None


def _command_from_candidate(path: Path, source: str,
                            environment: dict[str, str]) -> CodexCLICommand | None:
    if _is_windowsapps_executable(path) or not _safe_file(path):
        return None
    resolved = path.resolve()
    if os.name == "nt" and resolved.suffix.casefold() == ".cmd":
        entrypoint = _npm_entrypoint(resolved)
        node = _node_executable(resolved, environment)
        if entrypoint is None or node is None:
            return None
        return CodexCLICommand(
            executable=str(resolved),
            argv_prefix=(str(node.resolve()), str(entrypoint.resolve())),
            source=source,
        )
    if os.name == "nt" and resolved.suffix.casefold() not in {".exe", ".com"}:
        return None
    return CodexCLICommand(
        executable=str(resolved), argv_prefix=(str(resolved),), source=source,
    )


def _npm_global_candidates(environment: dict[str, str]) -> list[Path]:
    prefixes: list[Path] = []
    configured_prefix = environment.get("npm_config_prefix") or environment.get(
        "NPM_CONFIG_PREFIX"
    )
    if configured_prefix:
        prefixes.append(Path(configured_prefix).expanduser())
    if os.name == "nt":
        appdata = environment.get("APPDATA")
        if appdata:
            prefixes.append(Path(appdata) / "npm")
        else:
            prefixes.append(Path.home() / "AppData" / "Roaming" / "npm")
        names = ("codex.cmd", "codex.exe")
    else:
        prefixes.append(Path.home() / ".local" / "bin")
        names = ("codex",)
    seen: set[str] = set()
    return [
        prefix / name
        for prefix in prefixes
        if not (str(prefix).casefold() in seen or seen.add(str(prefix).casefold()))
        for name in names
    ]


def _ordinary_candidates(environment: dict[str, str]) -> list[Path]:
    home = Path(environment.get("USERPROFILE") or Path.home())
    if os.name != "nt":
        return [home / ".local" / "bin" / "codex"]
    local = Path(environment.get("LOCALAPPDATA") or home / "AppData" / "Local")
    return [
        home / ".local" / "bin" / "codex.exe",
        home / "scoop" / "shims" / "codex.exe",
        local / "Microsoft" / "WinGet" / "Links" / "codex.exe",
    ]


def resolve_codex_command(
    configured: str | None = None,
    environment: dict[str, str] | None = None,
) -> CodexCLIResolution:
    """Resolve a safe CLI without ever selecting a WindowsApps packaged binary."""
    env = dict(environment if environment is not None else os.environ)
    unsupported: str | None = None

    def probe(candidates: list[Path], source: str) -> CodexCLICommand | None:
        nonlocal unsupported
        for candidate in candidates:
            if _is_windowsapps_executable(candidate):
                if _safe_file(candidate):
                    unsupported = str(candidate)
                continue
            command = _command_from_candidate(candidate, source, env)
            if command is not None:
                return command
        return None

    if configured:
        candidates = _path_candidates(configured, env)
        command = probe(candidates, "configured")
        if command is not None:
            return CodexCLIResolution(command)
        if unsupported:
            return CodexCLIResolution(
                None,
                "windowsapps_packaged_executable_unsupported",
                "The Codex Desktop/WindowsApps executable cannot be used as an "
                "automation backend; install the official npm Codex CLI.",
                unsupported,
            )
        return CodexCLIResolution(
            None, "codex_not_found",
            "The configured Codex CLI executable was not found or is not a valid "
            "official npm/native CLI entrypoint.",
        )

    command = probe(_path_candidates("codex", env), "path")
    if command is None and os.name == "nt":
        command = probe(_path_candidates("codex.cmd", env), "path")
    if command is not None:
        return CodexCLIResolution(command)
    command = probe(_npm_global_candidates(env), "npm_global")
    if command is not None:
        return CodexCLIResolution(command)
    command = probe(_ordinary_candidates(env), "ordinary")
    if command is not None:
        return CodexCLIResolution(command)
    if unsupported:
        return CodexCLIResolution(
            None,
            "windowsapps_packaged_executable_unsupported",
            "Only a Codex Desktop/WindowsApps packaged executable was found. Install "
            "the official CLI with `npm install -g @openai/codex`.",
            unsupported,
        )
    return CodexCLIResolution(
        None, "codex_not_found",
        "Codex CLI was not found in the configured path, PATH, npm global prefix, "
        "or ordinary per-user install locations.",
    )


def resolve_codex_executable(configured: str | None = None) -> str | None:
    """Compatibility helper returning only the validated executable/shim path."""
    resolution = resolve_codex_command(configured)
    return resolution.command.executable if resolution.command else None


def serialize_codex_prompt(messages: list[dict[str, Any]], *,
                           json_schema: dict | None = None,
                           tools: list[dict] | None = None) -> str:
    """Serialize chat-shaped roles into one stable, Unicode-safe CLI prompt."""
    normalized = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, str):
            text = content
        else:
            text = json.dumps(content, ensure_ascii=False, sort_keys=True)
        item: dict[str, Any] = {"role": role, "content": text}
        if message.get("tool_call_id"):
            item["tool_call_id"] = str(message["tool_call_id"])
        if message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
        normalized.append(item)
    envelope: dict[str, Any] = {
        "version": 1,
        "transport": "codex_exec_stdin",
        "instructions": (
            "Treat each message's role as authoritative and return only the assistant "
            "answer requested by the conversation."
        ),
        "messages": normalized,
    }
    if json_schema:
        envelope["response_json_schema"] = json_schema
    if tools:
        envelope["available_openprover_tools"] = tools
    return (
        "MATH_RESEARCH_CODEX_CLI_PROMPT_V1\n"
        + json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def _secret_values(environment: dict[str, str]) -> tuple[str, ...]:
    markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    values = []
    for key, value in environment.items():
        if value and len(value) >= 8 and any(marker in key.upper() for marker in markers):
            values.append(value)
    return tuple(values)


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    value = str(text or "")
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\b(sk-[A-Za-z0-9_-]{8,})\b", "[REDACTED]", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
    value = re.sub(
        r'(?i)(["\']?(?:access|refresh|id)[_-]?token["\']?\s*[:=]\s*)[^\s,;}]+',
        r"\1[REDACTED]",
        value,
    )
    return value


class CodexCLIProviderError(RuntimeError):
    """Structured, secret-safe failure from one Codex CLI logical call."""

    def __init__(self, *, error_type: str, role: str, model: str | None,
                 reasoning_effort: str | None, executable: str | None,
                 status: int | None, retry_count: int, retryable: bool,
                 retry_exhausted: bool, human_explanation: str,
                 safe_stderr: str = ""):
        self.retry_exhausted = retry_exhausted
        self.details = {
            "provider": "codex_cli",
            "error_type": error_type,
            "status": status,
            "role": role,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "executable": executable,
            "retry_count": retry_count,
            "retryable": retryable,
            "retry_exhausted": retry_exhausted,
            "billing_mode": BILLING_MODE,
            "cost_usd": None,
            "human_explanation": human_explanation,
            "stderr": safe_stderr[-2000:],
        }
        super().__init__(json.dumps(self.details, ensure_ascii=False, sort_keys=True))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.details)


def _classify_failure(message: str, returncode: int | None) -> tuple[str, bool, str]:
    lowered = message.casefold()
    if any(marker in lowered for marker in (
        "not logged in", "not authenticated", "login required", "run codex login",
        "authentication required", "please sign in",
    )):
        return "not_authenticated", False, (
            "Codex CLI has no usable saved login; run `codex login` interactively."
        )
    if any(marker in lowered for marker in (
        "usage limit", "usage cap", "credits exhausted",
        "you have no weighted tokens left",
    )):
        return "usage_limit_reached", False, (
            "The ChatGPT/Codex subscription usage limit rejected the run."
        )
    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
        return "rate_limited", True, "Codex CLI reported an explicit rate limit."
    if (
        "model_reasoning_effort" in lowered
        or "reasoning effort" in lowered
        or "reasoning_effort" in lowered
    ) and any(marker in lowered for marker in (
        "invalid", "unsupported", "unknown", "not supported", "unrecognized",
    )):
        return "unsupported_reasoning_effort", False, (
            "The installed Codex CLI/model rejected the requested reasoning effort."
        )
    if "model" in lowered and any(marker in lowered for marker in (
        "not found", "does not exist", "invalid", "unsupported", "not available",
        "unknown model",
    )):
        return "invalid_model", False, (
            "The installed Codex CLI or current ChatGPT workspace rejected the model."
        )
    if any(marker in lowered for marker in _TRANSIENT_MARKERS):
        return "process_failed", True, (
            "Codex CLI reported an explicit transient process or network failure."
        )
    return "process_failed", False, (
        f"Codex CLI exited unsuccessfully (exit code {returncode})."
    )


def _parse_jsonl(stdout: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        stripped = line.lstrip("\ufeff").strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"JSONL line {line_number} is not an object")
        events.append(event)
    if not events:
        raise ValueError("Codex CLI emitted no JSONL events")

    usage = None
    thread_id = None
    resolved_model = None
    event_error = ""
    for event in events:
        thread_id = event.get("thread_id") or thread_id
        resolved_model = event.get("model") or resolved_model
        item = event.get("item")
        if isinstance(item, dict):
            resolved_model = item.get("model") or resolved_model
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            reported = event["usage"]
            input_tokens = int(reported.get("input_tokens", 0) or 0)
            output_tokens = int(reported.get("output_tokens", 0) or 0)
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": int(
                    reported.get("reasoning_output_tokens",
                                 reported.get("reasoning_tokens", 0)) or 0
                ),
                "cached_tokens": int(
                    reported.get("cached_input_tokens",
                                 reported.get("cached_tokens", 0)) or 0
                ),
                "total_tokens": int(
                    reported.get("total_tokens", input_tokens + output_tokens) or 0
                ),
                "cli_reported": True,
                "api_reported": False,
            }
        if event.get("type") in {"turn.failed", "error"}:
            error = event.get("error") or event.get("message") or event
            event_error = json.dumps(error, ensure_ascii=False) if not isinstance(error, str) else error
    return {
        "event_count": len(events),
        "event_types": [str(event.get("type", "unknown")) for event in events],
        "thread_id": thread_id,
        "resolved_model": resolved_model,
        "usage": usage,
        "event_error": event_error,
    }


def _terminate_process_tree(process: Any) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt" and getattr(process, "pid", None):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                shell=False,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass


class CodexCLIClient:
    """OpenProver-compatible client backed by one `codex exec` per request."""

    vllm = False
    mistral = False

    def __init__(self, model: str | None, archive_dir: Path, *, role_name: str,
                 working_dir: Path, executable: str | None = None,
                 reasoning_effort: str | None = None,
                 timeout_seconds: float = 600.0, max_retries: int = 1,
                 retry_base_seconds: float = 1.0,
                 answer_reserve: int = 4096, context_length: int = 200_000,
                 sandbox: str = "read-only", popen_factory: Callable[..., Any] | None = None,
                 allow_web_search: bool = False,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 terminate_tree: Callable[[Any], None] = _terminate_process_tree,
                 environment: dict[str, str] | None = None):
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError("Codex CLI model must be null or a non-empty string")
        if reasoning_effort not in CODEX_REASONING_EFFORTS | {None}:
            allowed = ", ".join(sorted(CODEX_REASONING_EFFORTS))
            raise ValueError(f"Invalid Codex CLI reasoning_effort; expected one of: {allowed}")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("Codex CLI sandbox must be read-only or workspace-write")
        if not isinstance(allow_web_search, bool):
            raise ValueError("allow_web_search must be boolean")

        self.model = model
        self.requested_model = model
        self.archive_dir = Path(archive_dir)
        self.role_name = role_name
        self.working_dir = Path(working_dir)
        self._environment = dict(environment if environment is not None else os.environ)
        resolution = resolve_codex_command(executable, self._environment)
        self._command_prefix = (
            resolution.command.argv_prefix if resolution.command is not None else None
        )
        self.executable = (
            resolution.command.executable
            if resolution.command is not None
            else resolution.rejected_candidate
        )
        self.executable_source = (
            resolution.command.source if resolution.command is not None else None
        )
        self._resolution_error_type = resolution.error_type
        self._resolution_explanation = resolution.explanation
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.retry_base_seconds = float(retry_base_seconds)
        self.answer_reserve = int(answer_reserve)
        self.context_length = int(context_length)
        self.sandbox = sandbox
        self.allow_web_search = allow_web_search
        self.call_count = 0
        self.request_count = 0
        self.process_start_attempts = 0
        self.total_retries = 0
        self.total_cost = None
        self.total_usage: dict[str, int | bool] = {}
        self.billing_mode = BILLING_MODE
        self._popen = popen_factory or subprocess.Popen
        self._sleep = sleep_fn
        self._terminate_tree = terminate_tree
        self._secrets = _secret_values(self._environment)
        self._lock = threading.Lock()
        self._interrupted = threading.Event()
        self._processes: set[Any] = set()

    def interrupt(self):
        self._interrupted.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            self._terminate_tree(process)

    def soft_interrupt(self):
        self.interrupt()

    def clear_interrupt(self):
        self._interrupted.clear()

    def clear_soft_interrupt(self):
        self._interrupted.clear()

    def cleanup(self):
        self.interrupt()

    def call(self, prompt: str, system_prompt: str,
             json_schema: dict | None = None, label: str = "",
             web_search: bool = False, stream_callback=None,
             archive_path: Path | None = None, max_tokens: int | None = None,
             no_thinking: bool = False, **_kwargs) -> dict:
        if web_search and not self.allow_web_search:
            raise ValueError(
                "Codex CLI web search is disabled for this route; set allow_web_search=true"
            )
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        effort = "minimal" if no_thinking else self.reasoning_effort
        return self._execute(
            messages=messages,
            prompt_for_archive=prompt,
            system_prompt=system_prompt,
            json_schema=json_schema,
            tools=None,
            label=label,
            stream_callback=stream_callback,
            archive_path=archive_path,
            max_tokens=max_tokens,
            effort=effort,
            web_search=web_search,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int | None = None, label: str = "",
             stream_callback=None, archive_path: Path | None = None,
             **_kwargs) -> dict:
        return self._execute(
            messages=messages,
            prompt_for_archive=json.dumps(messages, ensure_ascii=False),
            system_prompt="",
            json_schema=None,
            tools=tools,
            label=label,
            stream_callback=stream_callback,
            archive_path=archive_path,
            max_tokens=max_tokens,
            effort=self.reasoning_effort,
            web_search=False,
        )

    def _error(self, *, error_type: str, status: int | None,
               retry_count: int, retryable: bool, explanation: str,
               stderr: str = "") -> CodexCLIProviderError:
        return CodexCLIProviderError(
            error_type=error_type,
            role=self.role_name,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            executable=self.executable,
            status=status,
            retry_count=retry_count,
            retryable=retryable,
            retry_exhausted=bool(retryable and retry_count >= self.max_retries),
            human_explanation=explanation,
            safe_stderr=_redact(stderr, self._secrets),
        )

    def _argv(self, call_dir: Path, final_path: Path, schema_path: Path | None,
              effort: str | None, *, web_search: bool = False) -> list[str]:
        assert self._command_prefix is not None
        argv = [
            *self._command_prefix,
            "exec",
            "--json",
            "--output-last-message",
            str(final_path),
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            self.sandbox,
            "--skip-git-repo-check",
            "--cd",
            str(call_dir),
            "--config",
            'approval_policy="never"',
        ]
        if self.model:
            argv.extend(["--model", self.model])
        if web_search:
            argv.append("--search")
        if effort:
            argv.extend(["--config", f'model_reasoning_effort="{effort}"'])
        if schema_path:
            argv.extend(["--output-schema", str(schema_path)])
        argv.append("-")
        return argv

    def _execute(self, *, messages: list[dict], prompt_for_archive: str,
                 system_prompt: str, json_schema: dict | None,
                 tools: list[dict] | None, label: str, stream_callback,
                 archive_path: Path | None, max_tokens: int | None,
                 effort: str | None, web_search: bool = False) -> dict:
        with self._lock:
            self.call_count += 1
            call_num = self.call_count
        if self._command_prefix is None:
            error = self._error(
                error_type=self._resolution_error_type or "codex_not_found",
                status=None, retry_count=0,
                retryable=False,
                explanation=(
                    self._resolution_explanation
                    or "Codex CLI executable was not found on PATH or at the configured path."
                ),
            )
            self._archive(call_num, label, prompt_for_archive, system_prompt,
                          json_schema, None, error, 0, archive_path)
            raise error
        if self._interrupted.is_set():
            raise self._error(
                error_type="cancelled", status=None, retry_count=0,
                retryable=False, explanation="Codex CLI call was cancelled before start.",
            )

        serialized_prompt = serialize_codex_prompt(
            messages, json_schema=json_schema, tools=tools,
        )
        started = time.perf_counter()
        retry_count = 0
        while True:
            attempt_dir = self.working_dir / f"call-{call_num:03d}" / f"attempt-{retry_count + 1:02d}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            final_path = attempt_dir / "final-message.txt"
            schema_path = None
            if json_schema:
                schema_path = attempt_dir / "output-schema.json"
                schema_path.write_text(
                    json.dumps(json_schema, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            argv = self._argv(
                attempt_dir, final_path, schema_path, effort,
                web_search=web_search,
            )
            child_env = dict(self._environment)
            child_env.pop("OPENAI_API_KEY", None)
            child_env.pop("CODEX_API_KEY", None)
            child_env.pop("OPENAI_BASE_URL", None)
            child_env["NO_COLOR"] = "1"
            creationflags = 0
            if os.name == "nt":
                creationflags = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            try:
                with self._lock:
                    self.process_start_attempts += 1
                process = self._popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(attempt_dir),
                    env=child_env,
                    shell=False,
                    creationflags=creationflags,
                    start_new_session=(os.name != "nt"),
                )
            except FileNotFoundError as exc:
                error = self._error(
                    error_type="codex_not_found", status=None,
                    retry_count=retry_count, retryable=False,
                    explanation="Codex CLI executable disappeared before the process could start.",
                    stderr=str(exc),
                )
                self._archive(call_num, label, prompt_for_archive, system_prompt,
                              json_schema, None, error,
                              int((time.perf_counter() - started) * 1000), archive_path)
                raise error from exc
            except OSError as exc:
                error = self._error(
                    error_type="process_failed", status=getattr(exc, "winerror", None),
                    retry_count=retry_count, retryable=False,
                    explanation="Windows could not start the resolved Codex CLI executable.",
                    stderr=str(exc),
                )
                self._archive(call_num, label, prompt_for_archive, system_prompt,
                              json_schema, None, error,
                              int((time.perf_counter() - started) * 1000), archive_path)
                raise error from exc
            except Exception as exc:
                error = self._error(
                    error_type="unknown_codex_error", status=None,
                    retry_count=retry_count, retryable=False,
                    explanation="An unexpected error occurred while starting Codex CLI.",
                    stderr=str(exc),
                )
                self._archive(call_num, label, prompt_for_archive, system_prompt,
                              json_schema, None, error,
                              int((time.perf_counter() - started) * 1000), archive_path)
                raise error from exc

            with self._lock:
                self.request_count += 1
                self._processes.add(process)
            try:
                stdout, stderr = process.communicate(
                    input=serialized_prompt,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                self._terminate_tree(process)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    stdout, stderr = "", str(exc)
                error = self._error(
                    error_type="timeout", status=None, retry_count=retry_count,
                    retryable=True,
                    explanation=f"Codex CLI exceeded the {self.timeout_seconds:g}-second timeout.",
                    stderr=stderr,
                )
            except Exception as exc:
                self._terminate_tree(process)
                stdout, stderr = "", str(exc)
                error = self._error(
                    error_type="unknown_codex_error", status=None,
                    retry_count=retry_count, retryable=False,
                    explanation="An unexpected error occurred while communicating with Codex CLI.",
                    stderr=stderr,
                )
            finally:
                with self._lock:
                    self._processes.discard(process)

            if self._interrupted.is_set():
                error = self._error(
                    error_type="cancelled", status=getattr(process, "returncode", None),
                    retry_count=retry_count, retryable=False,
                    explanation="Codex CLI call was cancelled and its process tree was terminated.",
                    stderr=stderr,
                )
            elif "error" not in locals() or not isinstance(error, CodexCLIProviderError):
                returncode = int(getattr(process, "returncode", 0) or 0)
                safe_stderr = _redact(stderr, self._secrets)
                if returncode != 0:
                    kind, retryable, explanation = _classify_failure(
                        safe_stderr + "\n" + _redact(stdout, self._secrets), returncode,
                    )
                    error = self._error(
                        error_type=kind, status=returncode, retry_count=retry_count,
                        retryable=retryable, explanation=explanation,
                        stderr=safe_stderr,
                    )
                else:
                    try:
                        metadata = _parse_jsonl(stdout)
                        if metadata["event_error"]:
                            kind, retryable, explanation = _classify_failure(
                                metadata["event_error"], returncode,
                            )
                            error = self._error(
                                error_type=kind, status=returncode,
                                retry_count=retry_count, retryable=retryable,
                                explanation=explanation,
                                stderr=metadata["event_error"],
                            )
                        else:
                            if not final_path.is_file():
                                raise ValueError("--output-last-message file was not created")
                            result_text = final_path.read_text(encoding="utf-8-sig").strip()
                            if not result_text:
                                raise ValueError("--output-last-message file was empty")
                    except (OSError, UnicodeError, ValueError) as exc:
                        error = self._error(
                            error_type="malformed_output", status=returncode,
                            retry_count=retry_count, retryable=False,
                            explanation="Codex CLI completed without valid JSONL/final-message output.",
                            stderr=str(exc),
                        )

            if "result_text" in locals() and not isinstance(locals().get("error"), CodexCLIProviderError):
                break
            if error.retry_exhausted or not error.details["retryable"]:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._archive(call_num, label, prompt_for_archive, system_prompt,
                              json_schema, None, error, elapsed_ms, archive_path)
                raise error
            delay = self.retry_base_seconds * (2 ** retry_count)
            retry_count += 1
            with self._lock:
                self.total_retries += 1
            if delay:
                self._sleep(delay)
            error = None
            result_text = None

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        usage = metadata["usage"]
        if usage:
            with self._lock:
                if not self.total_usage:
                    self.total_usage = {
                        key: (False if isinstance(value, bool) else 0)
                        for key, value in usage.items()
                    }
                for key, value in usage.items():
                    if isinstance(value, bool):
                        self.total_usage[key] = bool(self.total_usage.get(key, False) or value)
                    else:
                        self.total_usage[key] = int(self.total_usage.get(key, 0)) + int(value)
        raw = {
            "provider": "codex_cli",
            "status": "completed",
            "billing_mode": BILLING_MODE,
            "cost_usd": None,
            "usage": usage or {},
            "executable": self.executable,
            "executable_source": self.executable_source,
            "command_prefix": list(self._command_prefix),
            "argv": argv,
            "working_directory": str(attempt_dir),
            "prompt_transport": "stdin",
            "output_mode": "jsonl+output-last-message",
            "openai_api_key_forwarded": False,
            "thread_id": metadata["thread_id"],
            "event_count": metadata["event_count"],
            "event_types": metadata["event_types"],
            "requested_model": self.model,
            "resolved_model": metadata["resolved_model"],
            "requested_reasoning_effort": effort,
            "max_tokens_requested_but_not_enforced": max_tokens,
            "retry_count": retry_count,
        }
        self._archive(
            call_num, label, prompt_for_archive, system_prompt, json_schema,
            raw, None, elapsed_ms, archive_path, result_text=result_text,
        )
        if stream_callback:
            stream_callback(result_text, "text")
        return {
            "result": result_text,
            "thinking": "",
            "provider": "codex_cli",
            "model": metadata["resolved_model"] or self.model,
            "requested_model": self.model,
            "reasoning_effort": effort,
            "billing_mode": BILLING_MODE,
            "cost": None,
            "cost_usd": None,
            "duration_ms": elapsed_ms,
            "retry_count": retry_count,
            "status": "completed",
            "finish_reason": "stop",
            "usage": usage,
            "raw": raw,
        }

    def _archive(self, call_num, label, prompt, system_prompt, json_schema,
                 response, error, elapsed_ms, archive_path=None,
                 *, result_text=""):
        archive(
            self.model or "codex-cli-default",
            self.archive_dir,
            call_num,
            label,
            prompt,
            system_prompt,
            json_schema,
            response,
            error,
            elapsed_ms,
            archive_path,
            result_text=result_text,
        )
