"""Small, provider-neutral tools for an agent working in one workspace.

The tool surface intentionally follows Pi's compact coding-agent shape.  The
executor is local and deterministic; providers only decide *when* to call a
tool.  File tools are rooted at ``workspace_root`` while shell commands run in
that directory with a bounded timeout and output size.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_TOOL_NAMES = (
    "read",
    "bash",
    "edit",
    "write",
    "grep",
    "find",
    "web_search",
)

_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "read": {
        "description": "Read a UTF-8 text file from the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    },
    "write": {
        "description": "Create or overwrite a UTF-8 text file in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path."},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "edit": {
        "description": "Replace one exact text span in a UTF-8 workspace file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    "bash": {
        "description": "Run a shell command in the workspace with a timeout.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120},
            },
            "required": ["command"],
        },
    },
    "grep": {
        "description": "Search UTF-8 text files in the workspace by regex or literal text.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Directory or file; defaults to ."},
                "literal": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["pattern"],
        },
    },
    "find": {
        "description": "Find workspace files by a glob pattern, such as **/*.py.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "required": ["pattern"],
        },
    },
    "web_search": {
        "description": "Search the public web and return titles, URLs, and snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
    },
}


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (tuple, list)):
        return [str(item) for item in value]
    raise ValueError("agent tools must be a name or list of names")


def normalize_tool_names(value: Any) -> list[str]:
    names: list[str] = []
    for name in _names(value):
        name = name.strip()
        if name and name not in names:
            if name not in _TOOL_DEFINITIONS:
                raise ValueError(f"Unknown agent tool: {name}")
            names.append(name)
    return names


def build_tool_payload(value: Any, *, provider: str) -> list[dict[str, Any]]:
    """Build the provider-native function-tool shape.

    OpenAI and OpenRouter Responses APIs use a flat function definition.  The
    nested ``function`` object belongs to Chat Completions and must not be sent
    to ``/responses``.
    """

    names = normalize_tool_names(value)
    if provider in {"gemini", "vertex_gemini"}:
        return [
            {
                "functionDeclarations": [
                    {
                        "name": name,
                        "description": _TOOL_DEFINITIONS[name]["description"],
                        "parameters": _TOOL_DEFINITIONS[name]["parameters"],
                    }
                    for name in names
                ]
            }
        ]
    if provider in {"openai", "openai_compatible", "openrouter"}:
        return [
            {
                "type": "function",
                "name": name,
                "description": _TOOL_DEFINITIONS[name]["description"],
                "parameters": _TOOL_DEFINITIONS[name]["parameters"],
            }
            for name in names
        ]
    raise ValueError(f"Provider {provider} does not support the common agent tool contract")


def tool_names(value: Any) -> tuple[str, ...]:
    return tuple(normalize_tool_names(value))


@dataclass(slots=True)
class AgentToolExecutor:
    workspace_root: Path
    max_output_chars: int = 20_000
    default_timeout_seconds: float = 30.0
    request_fn: Callable[..., Any] | None = None
    tool_event_sink: Callable[[dict[str, Any]], None] | None = None
    actor: str = "agent"

    def __post_init__(self) -> None:
        self.workspace_root = self.workspace_root.expanduser().resolve()
        self.request_fn = self.request_fn or urllib.request.urlopen

    def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in _TOOL_DEFINITIONS:
            return self._error(f"tool is not registered: {name}")
        call_id = uuid.uuid4().hex
        self._emit_tool_event(call_id, name, args, "STARTED")
        try:
            result = getattr(self, f"_{name}")(**dict(args or {}))
            result = result if isinstance(result, dict) else {"output": str(result)}
            self._emit_tool_event(call_id, name, args, "COMPLETED", result=result)
            return result
        except Exception as exc:  # Tool failures are returned to the model as data.
            result = self._error(str(exc))
            self._emit_tool_event(call_id, name, args, "FAILED", result=result)
            return result

    def _emit_tool_event(
        self,
        call_id: str,
        name: str,
        args: dict[str, Any],
        phase: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        if self.tool_event_sink is None:
            return
        try:
            self.tool_event_sink(
                {
                    "event_id": call_id,
                    "tool_name": name,
                    "phase": phase,
                    "actor": self.actor,
                    "args_summary": self._summarize_args(args),
                    "result_summary": self._summarize_result(result),
                }
            )
        except Exception:
            # UI telemetry must never turn a successful tool call into a model error.
            return

    @staticmethod
    def _summarize_args(args: dict[str, Any]) -> str:
        parts = []
        for key, value in (args or {}).items():
            text = " ".join(str(value).split())
            if len(text) > 180:
                text = text[:180] + "…"
            parts.append(f"{key}={text}")
        return ", ".join(parts)

    @staticmethod
    def _summarize_result(result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        status = str(result.get("status") or "")
        detail = result.get("error") or result.get("stdout") or result.get("content") or ""
        detail = " ".join(str(detail).split())
        if len(detail) > 240:
            detail = detail[:240] + "…"
        return f"{status} {detail}".strip()

    def _path(self, value: str) -> Path:
        candidate = (self.workspace_root / str(value)).resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("path escapes the workspace root") from exc
        return candidate

    def _read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
        target = self._path(path)
        if not target.is_file():
            raise FileNotFoundError(path)
        text = target.read_text(encoding="utf-8")
        lines = text.splitlines()
        if start_line is not None or end_line is not None:
            start = max(1, int(start_line or 1))
            end = max(start, int(end_line or len(lines)))
            text = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1))
        return {
            "status": "OK",
            "path": str(target.relative_to(self.workspace_root)),
            "content": self._clip(text),
        }

    def _write(self, path: str, content: str) -> dict:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return {
            "status": "OK",
            "path": str(target.relative_to(self.workspace_root)),
            "bytes": target.stat().st_size,
        }

    def _edit(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> dict:
        target = self._path(path)
        text = target.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            raise ValueError("old_text was not found")
        if count > 1 and not replace_all:
            raise ValueError(f"old_text matched {count} locations; set replace_all=true")
        updated = text.replace(old_text, new_text, -1 if replace_all else 1)
        target.write_text(updated, encoding="utf-8")
        return {
            "status": "OK",
            "path": str(target.relative_to(self.workspace_root)),
            "replacements": count if replace_all else 1,
        }

    def _bash(self, command: str, timeout_seconds: float | None = None) -> dict:
        timeout = min(120.0, max(1.0, float(timeout_seconds or self.default_timeout_seconds)))
        completed = subprocess.run(
            str(command),
            cwd=self.workspace_root,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "MRA_WORKSPACE_ROOT": str(self.workspace_root)},
        )
        return {
            "status": "OK" if completed.returncode == 0 else "ERROR",
            "exit_code": completed.returncode,
            "stdout": self._clip(completed.stdout),
            "stderr": self._clip(completed.stderr),
        }

    def _grep(
        self,
        pattern: str,
        path: str = ".",
        literal: bool = False,
        max_results: int = 100,
    ) -> dict:
        root = self._path(path)
        regex = re.compile(re.escape(pattern) if literal else pattern)
        results: list[dict[str, Any]] = []
        files = [root] if root.is_file() else self._iter_files(root)
        for file in files:
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if regex.search(line):
                    results.append(
                        {
                            "path": str(file.relative_to(self.workspace_root)),
                            "line": number,
                            "text": line[:500],
                        }
                    )
                    if len(results) >= int(max_results):
                        return {"status": "OK", "matches": results, "truncated": True}
        return {"status": "OK", "matches": results, "truncated": False}

    def _find(self, pattern: str, path: str = ".", max_results: int = 200) -> dict:
        root = self._path(path)
        if not root.is_dir():
            raise NotADirectoryError(path)
        matches = [
            str(item.relative_to(self.workspace_root))
            for item in root.glob(pattern)
            if item.is_file()
        ][: int(max_results)]
        return {"status": "OK", "matches": matches, "truncated": len(matches) >= int(max_results)}

    def _web_search(self, query: str, max_results: int = 5) -> dict:
        query = " ".join(str(query).split())
        if not query:
            raise ValueError("query must not be empty")
        limit = max(1, min(10, int(max_results)))
        brave_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if brave_key:
            url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
                {"q": query, "count": limit}
            )
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
            )
            with self.request_fn(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": item.get("description", ""),
                }
                for item in (payload.get("web", {}).get("results", []) or [])[:limit]
            ]
            return {"status": "OK", "provider": "brave", "query": query, "results": results}
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(url, headers={"User-Agent": "MathResearchAgent/1.0"})
        with self.request_fn(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        results = []
        for match in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.I | re.S
        ):
            title = re.sub(r"<[^>]+>", "", html.unescape(match.group(2))).strip()
            link = html.unescape(match.group(1))
            results.append({"title": title, "url": link, "snippet": ""})
            if len(results) >= limit:
                break
        return {"status": "OK", "provider": "duckduckgo", "query": query, "results": results}

    def _iter_files(self, root: Path):
        ignored = {".git", ".venv", "__pycache__", "node_modules", "target"}
        for item in root.rglob("*"):
            if item.is_file() and not ignored.intersection(item.parts):
                yield item

    def _clip(self, value: str) -> str:
        value = str(value or "")
        if len(value) <= self.max_output_chars:
            return value
        return value[: self.max_output_chars] + "\n...[output truncated]"

    @staticmethod
    def _error(message: str) -> dict:
        return {"status": "ERROR", "error": str(message)}


def make_tool_executor(
    configured_tools: Any,
    *,
    workspace_root: str | Path | None,
    max_output_chars: int = 20_000,
    default_timeout_seconds: float = 30.0,
    tool_event_sink: Callable[[dict[str, Any]], None] | None = None,
    actor: str = "agent",
) -> AgentToolExecutor | None:
    names = normalize_tool_names(configured_tools)
    if not names:
        return None
    if workspace_root is None:
        raise ValueError("workspace_root is required when agent tools are enabled")
    return AgentToolExecutor(
        Path(workspace_root),
        max_output_chars=max_output_chars,
        default_timeout_seconds=default_timeout_seconds,
        tool_event_sink=tool_event_sink,
        actor=actor,
    )


__all__ = [
    "AgentToolExecutor",
    "DEFAULT_TOOL_NAMES",
    "build_tool_payload",
    "make_tool_executor",
    "normalize_tool_names",
    "tool_names",
]
