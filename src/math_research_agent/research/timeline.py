"""Canonical, append-only project timeline.

The project has several durable projections (run state, pipeline state, audit
artifacts, and UI events).  This module provides the small shared event ledger
that lets clients reopen a project and reconstruct what happened without
guessing from the current status alone.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TIMELINE_SCHEMA_VERSION = 1
_TIMELINE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timeline_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / "timeline.jsonl"


class ProjectTimeline:
    """Append durable events and read the latest project history."""

    def __init__(self, project_root: str | Path, *, path: str | Path | None = None):
        self.project_root = Path(project_root).resolve()
        self.path = Path(path).resolve() if path else timeline_path(self.project_root)

    def append(
        self,
        *,
        kind: str,
        action: str,
        status: str,
        summary: str = "",
        project_id: str = "",
        run_id: str = "",
        parent_run_id: str = "",
        theorem_id: str = "",
        stage: str = "",
        role: str = "system",
        event_id: str | None = None,
        artifacts: Iterable[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "timeline_schema_version": TIMELINE_SCHEMA_VERSION,
            "event_id": event_id or f"timeline-{uuid.uuid4().hex}",
            "timestamp": _now(),
            "kind": str(kind),
            "action": str(action),
            "status": str(status),
            "summary": str(summary),
            "project_id": str(project_id),
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id),
            "theorem_id": str(theorem_id),
            "stage": str(stage),
            "role": str(role),
            "artifacts": [str(item) for item in (artifacts or [])],
            "payload": dict(payload or {}),
        }
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _TIMELINE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
        return event

    def read(self, *, limit: int = 500) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        values: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                values.append(value)
        return values


def append_timeline_event(project_root: str | Path, **values: Any) -> dict[str, Any]:
    """Convenience wrapper for components that do not need a long-lived writer."""

    return ProjectTimeline(project_root).append(**values)
