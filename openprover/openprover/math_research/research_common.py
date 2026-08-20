"""Strict immutable-artifact primitives for the PHASE 4 Research Plane."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .project import ProjectError
from .truth_identity import canonical_json_bytes, domain_hash


RESEARCH_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def strict_fields(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if not isinstance(value, Mapping):
        raise ProjectError(f"{name} artifact root must be an object")
    actual = set(value)
    if actual != expected:
        raise ProjectError(
            f"{name} fields do not match schema {RESEARCH_SCHEMA_VERSION}; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )


def validate_envelope(value: Mapping[str, Any], *, object_type: str, name: str) -> None:
    if value.get("schema_version") != RESEARCH_SCHEMA_VERSION:
        raise ProjectError(f"{name} migration is required")
    if value.get("object_type") != object_type:
        raise ProjectError(f"Invalid {name}.object_type")


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectError(f"{field} is required")
    return value.strip()


def require_optional_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ProjectError(f"{field} must be a string")
    return value.strip()


def require_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProjectError(f"{field} must be a SHA-256 domain digest")
    return value


def require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ProjectError(f"{field} must be a safe stable id")
    return value


def string_tuple(value: Any, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectError(f"{field} must be a list")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or (not allow_empty and not item.strip()):
            raise ProjectError(f"{field} must contain strings")
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise ProjectError(f"{field} contains duplicates")
    return tuple(normalized)


def stable_value(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value))


def artifact_dict(value: Any) -> dict[str, Any]:
    return stable_value(asdict(value))


def content_id(prefix: str, domain: str, identity: Mapping[str, Any]) -> str:
    digest = domain_hash(domain, dict(identity)).removeprefix("sha256:")
    return f"{prefix}-{digest[:24]}"


def digest_part(value: str, field: str = "content hash") -> str:
    return require_hash(value, field).removeprefix("sha256:")


def read_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise ProjectError(f"{name} not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid {name} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ProjectError(f"{name} artifact root must be an object")
    return value


def write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ProjectError(f"Immutable research artifact collision: {path}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def write_projection_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
