from math_research_agent.research.timeline import ProjectTimeline


def test_project_timeline_is_append_only_and_readable(tmp_path):
    timeline = ProjectTimeline(tmp_path)
    timeline.append(
        kind="UI_EVENT",
        action="plan_project",
        status="STARTED",
        project_id="demo",
        run_id="run-1",
        summary="planning",
    )
    timeline.append(
        kind="PIPELINE_EVENT",
        action="TASK_READY",
        status="PROGRESS",
        project_id="demo",
        run_id="run-1",
        theorem_id="lemma-a",
    )

    events = timeline.read()
    assert [event["kind"] for event in events] == ["UI_EVENT", "PIPELINE_EVENT"]
    assert events[-1]["theorem_id"] == "lemma-a"
    assert events[0]["timeline_schema_version"] == 1
