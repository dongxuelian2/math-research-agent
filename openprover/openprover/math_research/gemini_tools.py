"""Gemini-native declarations and the narrow local Lean execution bridge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


_LEAN_DECLARATIONS = {
    "lean_verify": {
        "name": "lean_verify",
        "description": "Compile Lean 4 source and return the compiler diagnostics.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Complete Lean 4 source code to verify.",
                }
            },
            "required": ["code"],
        },
    },
    "lean_store": {
        "name": "lean_store",
        "description": (
            "Compile and store a Lean 4 snippet without sorry, axiom, unsafe, "
            "set_option, or native_decide."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Lean 4 lemma or definition to compile and store.",
                }
            },
            "required": ["code"],
        },
    },
    "lean_search": {
        "name": "lean_search",
        "description": "Search the available Lean declarations by name or meaning.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A declaration name or mathematical description.",
                }
            },
            "required": ["query"],
        },
    },
}


def _names(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        return [value]
    if isinstance(value, list):
        return list(value)
    raise ValueError("Gemini tools must be a list, name, or tool object")


def build_tool_payload(value: Any) -> list[dict[str, Any]]:
    """Convert declarative role tool names to GenerateContent tool objects."""

    declarations: list[dict[str, Any]] = []
    payload: list[dict[str, Any]] = []
    for item in _names(value):
        if isinstance(item, dict):
            if "functionDeclarations" in item or "google_search" in item:
                payload.append(item)
            elif item.get("name"):
                declarations.append(dict(item))
            else:
                raise ValueError("Gemini tool object requires name or functionDeclarations")
            continue
        name = str(item).strip()
        if name == "google_search":
            payload.append({"google_search": {}})
        elif name in _LEAN_DECLARATIONS:
            declarations.append(dict(_LEAN_DECLARATIONS[name]))
        else:
            raise ValueError(f"Unknown Gemini tool: {name}")
    if declarations:
        payload.insert(0, {"functionDeclarations": declarations})
    return payload


def make_tool_executor(
    configured_tools: Any,
    *,
    worker_id: str,
    working_dir: Path | None = None,
    lean_project_dir: str | Path | None = None,
) -> Callable[[str, dict[str, Any]], dict[str, Any]] | None:
    """Build a callback for local tools without exposing core internals.

    The callback uses the public Lean tool facade. It is deliberately
    unavailable until a user opts in with LEAN_PROJECT_DIR (or a role
    setting), so a regular natural-language run cannot accidentally claim a
    compiler certificate.
    """

    names = {str(item) for item in _names(configured_tools) if not isinstance(item, dict)}
    if not names.intersection({"lean_verify", "lean_store", "lean_search"}):
        return None
    project_value = lean_project_dir or os.environ.get("LEAN_PROJECT_DIR")
    project_path = Path(project_value).expanduser() if project_value else None
    work_dir = None
    if project_path and project_path.is_dir():
        from ..lean import LeanWorkDir

        work_dir = LeanWorkDir(project_path)

    def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in names:
            return {
                "status": "ERROR",
                "error": f"Tool {name} is not enabled for this role",
            }
        if project_path is None or not project_path.is_dir():
            return {
                "status": "ERROR",
                "error": ("LEAN_PROJECT_DIR is not configured for the formalization lane"),
            }
        from ..lean import execute_worker_tool

        result, status = execute_worker_tool(
            name,
            dict(args),
            worker_id,
            work_dir,
            project_path,
            None,
        )
        return {"status": status.upper(), "output": result}

    return execute
