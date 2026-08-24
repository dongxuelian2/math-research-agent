"""Human-facing research progress events for interactive clients.

These events are deliberately separate from proof-control events.  A UI event
describes what the orchestrator is doing; it can never promote a theorem or
change a truth-plane status.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .timeline import ProjectTimeline


class ResearchUiEvent(BaseModel):
    """One stable, serializable activity item consumed by the TUI."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    event_type: Literal["research_ui_event"] = "research_ui_event"
    event_id: StrictStr
    timestamp: StrictStr
    project_id: StrictStr = ""
    theorem_id: StrictStr = ""
    run_id: StrictStr = ""
    parent_run_id: StrictStr = ""
    role: StrictStr = "system"
    action: StrictStr
    stage: StrictStr = ""
    title: StrictStr
    summary: StrictStr = ""
    status: Literal["STARTED", "PROGRESS", "COMPLETED", "FAILED"]
    elapsed_ms: int | None = None
    artifacts: list[StrictStr] = Field(default_factory=list)
    error: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_exception(exc: BaseException) -> dict[str, Any]:
    """Convert an exception into a short, actionable UI-safe error."""

    name = type(exc).__name__
    message = str(exc).strip() or name
    lowered = message.casefold()
    if "quota" in lowered or "rate" in lowered or "429" in lowered:
        kind, explanation, action, retryable = (
            "provider_quota",
            "模型服务暂时拒绝了请求或达到配额限制。",
            "稍后重试，或切换到可用模型配置。",
            True,
        )
    elif "auth" in lowered or "credential" in lowered or "api key" in lowered:
        kind, explanation, action, retryable = (
            "provider_auth",
            "模型服务认证失败。",
            "检查当前模型配置和凭据。",
            False,
        )
    elif "timeout" in lowered or "network" in lowered or "connection" in lowered:
        kind, explanation, action, retryable = (
            "provider_network",
            "模型服务或网络连接没有及时返回。",
            "检查网络后重试。",
            True,
        )
    elif isinstance(exc, (OSError, IOError)):
        kind, explanation, action, retryable = (
            "filesystem",
            "研究产物或项目文件无法读写。",
            "检查项目目录权限和磁盘空间。",
            False,
        )
    elif (
        "config" in lowered
        or "configuration" in lowered
        or "project" in lowered
        or "not found" in lowered
        or "permission" in lowered
    ):
        kind, explanation, action, retryable = (
            "project_configuration",
            "项目或模型配置不可用。",
            "检查项目状态、配置文件路径和访问权限。",
            False,
        )
    elif "schema" in lowered or "json" in lowered or "structured" in lowered:
        kind, explanation, action, retryable = (
            "structured_response",
            "模型返回内容没有通过结构化格式校验。",
            "查看诊断详情，必要时重试或调整模型。",
            True,
        )
    else:
        kind, explanation, action, retryable = (
            "runtime",
            "研究运行在当前阶段异常终止。",
            "打开诊断详情查看原因后再重试。",
            False,
        )
    return {
        "kind": kind,
        "message": explanation,
        "detail": message[:500],
        "exception": name,
        "retryable": retryable,
        "action": action,
    }


class UiEventEmitter:
    """Emit NDJSON events and keep implementation details out of the TUI."""

    def __init__(
        self,
        *,
        project_id: str = "",
        project_root: str | Path | None = None,
        event_log_path: str | Path | None = None,
        timeline_path: str | Path | None = None,
        stream: TextIO | None = None,
        enabled: bool = True,
    ) -> None:
        self.project_id = project_id
        self.project_root = Path(project_root).resolve() if project_root else None
        if event_log_path is not None:
            self.event_log_path = Path(event_log_path)
        elif self.project_root is not None:
            self.event_log_path = self.project_root / "logs" / "ui-events.jsonl"
        else:
            self.event_log_path = None
        self.timeline = (
            ProjectTimeline(
                self.project_root or Path("."),
                path=timeline_path,
            )
            if self.project_root is not None or timeline_path is not None
            else None
        )
        self.stream = stream
        self.enabled = enabled and (
            stream is not None or self.event_log_path is not None or self.timeline is not None
        )
        self._lock = threading.RLock()
        self._active: dict[str, dict[str, Any]] = {}
        self._started_at: dict[str, float] = {}
        self._tool_events: dict[str, str] = {}
        self.parent_run_id = ""

    def emit(self, event: ResearchUiEvent) -> None:
        payload = event.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False) + "\n"
        if not self.enabled:
            return
        with self._lock:
            if self.stream is not None:
                try:
                    self.stream.write(encoded)
                    self.stream.flush()
                except OSError:
                    # A closed pipe must not turn a completed research run
                    # into a second, misleading failure.
                    pass
            if self.event_log_path is not None:
                try:
                    self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.event_log_path.open("a", encoding="utf-8") as handle:
                        handle.write(encoded)
                except OSError:
                    # The live stream remains useful even if optional local
                    # event persistence is unavailable.
                    pass
            if self.timeline is not None:
                self.timeline.append(
                    kind="UI_EVENT",
                    action=event.action,
                    status=event.status,
                    summary=event.summary,
                    project_id=event.project_id,
                    run_id=event.run_id,
                    parent_run_id=event.parent_run_id,
                    theorem_id=event.theorem_id,
                    stage=event.stage,
                    role=event.role,
                    event_id=event.event_id,
                    artifacts=event.artifacts,
                    payload={
                        "event_type": event.event_type,
                        "title": event.title,
                        "error": event.error,
                        "elapsed_ms": event.elapsed_ms,
                    },
                )

    def start(
        self,
        *,
        action: str,
        title: str,
        summary: str = "",
        role: str = "system",
        stage: str = "",
        theorem_id: str = "",
        run_id: str = "",
    ) -> str:
        event_id = uuid.uuid4().hex
        base = {
            "action": action,
            "title": title,
            "summary": summary,
            "role": role,
            "stage": stage,
            "theorem_id": theorem_id,
            "run_id": run_id,
            "parent_run_id": self.parent_run_id,
        }
        with self._lock:
            self._active[event_id] = base
            self._started_at[event_id] = time.monotonic()
            self.emit(
                self._event(
                    event_id=event_id,
                    action=action,
                    title=title,
                    summary=summary,
                    role=role,
                    stage=stage,
                    theorem_id=theorem_id,
                    run_id=run_id,
                    status="STARTED",
                )
            )
        return event_id

    def update(self, event_id: str, **changes: Any) -> None:
        with self._lock:
            if event_id not in self._active:
                return
            self.emit(self._event(event_id=event_id, status="PROGRESS", **changes))

    def finish(self, event_id: str, *, success: bool, **changes: Any) -> None:
        with self._lock:
            if event_id not in self._active:
                return
            started = self._started_at.get(event_id)
            if started is not None and "elapsed_ms" not in changes:
                changes["elapsed_ms"] = max(0, int((time.monotonic() - started) * 1000))
            self.emit(
                self._event(
                    event_id=event_id,
                    status="COMPLETED" if success else "FAILED",
                    **changes,
                )
            )
            self._active.pop(event_id, None)
            self._started_at.pop(event_id, None)

    def error(
        self,
        exc: BaseException,
        *,
        action: str = "runtime",
        title: str = "研究运行失败",
        stage: str = "",
        theorem_id: str = "",
        run_id: str = "",
        diagnostic_path: str | Path | None = None,
    ) -> str:
        diagnostic = self._write_traceback(exc, diagnostic_path)
        detail = classify_exception(exc)
        if diagnostic:
            detail["diagnostic"] = diagnostic
        event_id = uuid.uuid4().hex
        self.emit(
            self._event(
                event_id=event_id,
                action=action,
                title=title,
                summary=detail["message"],
                role="system",
                stage=stage,
                theorem_id=theorem_id,
                run_id=run_id,
                status="FAILED",
                error=detail,
            )
        )
        return event_id

    def tool_event(self, event: dict[str, Any]) -> None:
        """Project local tool execution into the same stream consumed by the TUI."""

        call_id = str(event.get("event_id") or "")
        tool_name = str(event.get("tool_name") or "tool")
        phase = str(event.get("phase") or "")
        summary = str(event.get("args_summary") or "")
        title = f"Tool · {tool_name}"
        if not call_id:
            return
        if phase == "STARTED":
            ui_event_id = self.start(
                action="tool_call",
                title=title,
                summary=summary,
                role="tool",
                stage="TOOL",
            )
            self._tool_events[call_id] = ui_event_id
            return
        ui_event_id = self._tool_events.pop(call_id, "")
        if not ui_event_id:
            return
        result = str(event.get("result_summary") or "")
        self.finish(
            ui_event_id,
            success=phase == "COMPLETED",
            title=title,
            summary=result or summary,
            role="tool",
            stage="TOOL",
        )

    def _event(self, *, event_id: str, status: str, **values: Any) -> ResearchUiEvent:
        base = self._active.get(event_id, {})
        base = {**base, **values}
        return ResearchUiEvent(
            event_id=event_id,
            timestamp=_now(),
            project_id=self.project_id,
            status=status,
            **base,
        )

    def _write_traceback(self, exc: BaseException, diagnostic_path: str | Path | None) -> str:
        if diagnostic_path is None and self.project_root is not None:
            diagnostic_path = self.project_root / "logs" / "ui-errors.log"
        if diagnostic_path is None:
            return ""
        path = Path(diagnostic_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{_now()}] {type(exc).__name__}: {exc}\n")
                traceback.print_exception(exc, file=handle)
        except OSError:
            return ""
        if self.project_root is not None:
            try:
                return path.resolve().relative_to(self.project_root).as_posix()
            except ValueError:
                pass
        return str(path)


def emit_cli_error(
    exc: BaseException,
    *,
    project: str | Path | None = None,
    stream: TextIO | None = None,
) -> None:
    """Emit one safe error event for a CLI invocation using ``--ui-events``."""

    root = Path(project).resolve() if project else None
    project_id = root.name if root else ""
    UiEventEmitter(
        project_id=project_id,
        project_root=root,
        stream=stream or sys.stdout,
    ).error(exc, action="runtime", title="研究运行失败", stage="CLI")
