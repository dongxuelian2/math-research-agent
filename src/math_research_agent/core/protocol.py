"""Strict-enough action protocol for the project-owned research engine."""

from __future__ import annotations

import re
import tomllib


ACTION_BLOCK = re.compile(r"<MRA_ACTION>\s*(.*?)\s*</MRA_ACTION>", re.DOTALL)


class ProtocolError(ValueError):
    """Raised when a planner emits an incomplete action document."""


def response_text(response: dict) -> str:
    result = response.get("result", "")
    return str(result if result else response.get("thinking", ""))


def parse_actions(text: str) -> list[dict]:
    actions = []
    for block in ACTION_BLOCK.findall(text):
        try:
            value = tomllib.loads(block)
        except tomllib.TOMLDecodeError:
            value = _parse_lenient_toml(block)
        if value is None:
            raise ProtocolError("invalid planner action: unable to parse block")
        action = str(value.get("action", "")).strip()
        if not action:
            raise ProtocolError("planner action has no action field")
        for key in ("tasks", "items"):
            raw = value.get(key)
            if isinstance(raw, list):
                value[key] = [
                    item if isinstance(item, dict) else {"description": str(item)} for item in raw
                ]
        value["action"] = action
        actions.append(value)
    return actions


def _parse_lenient_toml(text: str) -> dict | None:
    """Parse the small action subset while preserving LaTeX backslashes."""
    result: dict = {}
    tables: dict[str, list[dict]] = {"tasks": [], "items": []}
    current: dict | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("#"):
            index += 1
            continue
        if line in {"[[tasks]]", "[[items]]"}:
            name = line[2:-2]
            current = {}
            tables[name].append(current)
            index += 1
            continue
        match = re.match(r"(\w+)\s*=\s*(.*)", line)
        if not match:
            index += 1
            continue
        key, raw = match.groups()
        target = current if current is not None else result
        if raw.startswith('"""'):
            parts = [raw[3:]]
            index += 1
            while index < len(lines):
                if '"""' in lines[index]:
                    parts.append(lines[index].split('"""', 1)[0])
                    break
                parts.append(lines[index])
                index += 1
            target[key] = "\n".join(parts).strip()
        elif raw.startswith('"') and raw.endswith('"'):
            target[key] = raw[1:-1]
        elif raw in {"true", "false"}:
            target[key] = raw == "true"
        elif raw.startswith("["):
            target[key] = re.findall(r'"([^"]*)"', raw)
        else:
            target[key] = raw
        index += 1
    for name, entries in tables.items():
        if entries:
            result[name] = entries
    return result or None
