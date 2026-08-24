import io
import json
from pathlib import Path

import pytest

from math_research_agent.research.ui_events import (
    ResearchUiEvent,
    UiEventEmitter,
    classify_exception,
)


def test_ui_event_is_strict_and_serializable():
    event = ResearchUiEvent(
        event_id="event-1",
        timestamp="2026-08-24T00:00:00Z",
        action="plan_project",
        title="正在分析研究目标",
        status="STARTED",
    )
    assert event.model_dump(mode="json")["event_type"] == "research_ui_event"
    with pytest.raises(ValueError):
        ResearchUiEvent(
            event_id="event-2",
            timestamp="2026-08-24T00:00:00Z",
            action="plan_project",
            title="bad",
            status="STARTED",
            unexpected=True,
        )
    with pytest.raises(ValueError):
        ResearchUiEvent(
            event_id="event-3",
            timestamp="2026-08-24T00:00:00Z",
            action="plan_project",
            title="bad status",
            status="HEARTBEAT",
        )


def test_emitter_updates_one_action_and_records_elapsed(tmp_path: Path):
    output = io.StringIO()
    emitter = UiEventEmitter(
        project_id="demo",
        project_root=tmp_path,
        stream=output,
    )
    event_id = emitter.start(
        action="plan_project",
        title="正在分析研究目标",
        role="planner",
        stage="PLANNING",
    )
    emitter.update(event_id, summary="已生成 2 个候选子命题。")
    emitter.finish(event_id, success=True, summary="规划完成。")
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [item["status"] for item in events] == ["STARTED", "PROGRESS", "COMPLETED"]
    assert len({item["event_id"] for item in events}) == 1
    assert events[-1]["elapsed_ms"] is not None
    persisted = tmp_path / "logs" / "ui-events.jsonl"
    assert persisted.is_file()
    assert len(persisted.read_text(encoding="utf-8").splitlines()) == 3


def test_error_classification_is_short_and_actionable():
    error = classify_exception(RuntimeError("429 quota exceeded"))
    assert error["kind"] == "provider_quota"
    assert error["retryable"] is True
    assert "traceback" not in error["message"].casefold()
    config_error = classify_exception(RuntimeError("project config not found"))
    assert config_error["kind"] == "project_configuration"
    assert config_error["retryable"] is False
