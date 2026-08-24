from __future__ import annotations

from pathlib import Path

from math_research_agent.core import Budget, KnowledgeRepository, ResearchEngine
from math_research_agent.core.protocol import parse_actions


class FakeClient:
    def __init__(self, responses: list[str]):
        self.responses = iter(responses)
        self.call_count = 0

    def call(self, prompt: str, system_prompt: str, *, archive_path: Path, **kwargs) -> dict:
        self.call_count += 1
        value = next(self.responses)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(value, encoding="utf-8")
        return {"result": value, "usage": {"output_tokens": len(value)}}

    def cleanup(self) -> None:
        pass


def test_repository_round_trip_and_index(tmp_path: Path):
    repository = KnowledgeRepository(tmp_path / "repo")
    repository.write_item("lemmas/base", "Summary: base lemma")
    assert repository.read_item("lemmas/base") == "Summary: base lemma\n"
    assert "[[lemmas/base]]" in repository.index()


def test_action_protocol_parses_multiple_blocks():
    actions = parse_actions(
        '<MRA_ACTION>\naction = "write_whiteboard"\nwhiteboard = "closed"\n</MRA_ACTION>\n'
        '<MRA_ACTION>\naction = "submit_proof"\nproof_slug = "candidate"\n</MRA_ACTION>'
    )
    assert [item["action"] for item in actions] == ["write_whiteboard", "submit_proof"]


def test_action_protocol_normalizes_legacy_json_worker_assignments():
    actions = parse_actions(
        'MRA_ACTION: {"action":"assign_worker","worker_id":"w1","role":"constructive","task":"prove A"}\n'
        'MRA_ACTION: {"action":"assign_worker","worker_id":"w2","role":"boundary","task":"check B"}\n'
    )

    assert len(actions) == 1
    assert actions[0]["action"] == "spawn"
    assert [task["worker_id"] for task in actions[0]["tasks"]] == ["w1", "w2"]


def test_engine_writes_candidate_without_upstream_engine(tmp_path: Path):
    planner = FakeClient(
        [
            '<MRA_ACTION>\naction = "spawn"\n\n[[tasks]]\nsummary = "check"\ndescription = "prove it"\n</MRA_ACTION>',
            '<MRA_ACTION>\naction = "write_items"\n\n[[items]]\nslug = "candidate"\ncontent = """A complete proof."""\n</MRA_ACTION>\n<MRA_ACTION>\naction = "submit_proof"\nproof_slug = "candidate"\n</MRA_ACTION>',
        ]
    )
    worker = FakeClient(["worker report", "verifier report VERDICT: CORRECT"])
    engine = ResearchEngine(
        work_dir=tmp_path / "engine",
        theorem_text="Show that 1 = 1.",
        planner=planner,
        worker=worker,
        budget=Budget(mode="calls", limit=10),
        max_workers=1,
        verifier=True,
    )
    candidate = engine.run()
    assert candidate is not None
    assert candidate.read_text(encoding="utf-8") == "A complete proof.\n"
    assert (tmp_path / "engine" / "steps" / "step_001" / "workers").is_dir()


def test_engine_retries_empty_planner_response(tmp_path: Path):
    planner = FakeClient(
        [
            "No executable action yet.",
            '<MRA_ACTION>\naction = "write_items"\n\n[[items]]\nslug = "candidate"\ncontent = "A proof."\n</MRA_ACTION>\n'
            '<MRA_ACTION>\naction = "submit_proof"\nproof_slug = "candidate"\n</MRA_ACTION>',
        ]
    )
    worker = FakeClient([])
    engine = ResearchEngine(
        work_dir=tmp_path / "engine",
        theorem_text="Show that 1 = 1.",
        planner=planner,
        worker=worker,
        budget=Budget(mode="calls", limit=5),
        max_workers=1,
        verifier=False,
    )

    candidate = engine.run()

    assert candidate is not None
    assert planner.call_count == 2


def test_engine_executes_legacy_assignment_batch_with_bounded_parallelism(tmp_path: Path):
    assignments = "\n".join(
        f'MRA_ACTION: {{"action":"assign_worker","worker_id":"w{index}","task":"check {index}"}}'
        for index in range(4)
    )
    planner = FakeClient(
        [
            assignments,
            '<MRA_ACTION>\naction = "write_items"\n\n[[items]]\nslug = "candidate"\ncontent = "A proof."\n</MRA_ACTION>\n'
            '<MRA_ACTION>\naction = "submit_proof"\nproof_slug = "candidate"\n</MRA_ACTION>',
        ]
    )
    worker = FakeClient(["worker report"] * 4 + ["verifier report"] * 4)
    engine = ResearchEngine(
        work_dir=tmp_path / "engine",
        theorem_text="Show that 1 = 1.",
        planner=planner,
        worker=worker,
        budget=Budget(mode="calls", limit=10),
        max_workers=3,
        verifier=True,
    )

    candidate = engine.run()

    assert candidate is not None
    assert len(list((tmp_path / "engine").glob("steps/*/workers/worker_*_call.md"))) == 4
    assert len(list((tmp_path / "engine").glob("steps/*/workers/verifier_*_call.md"))) == 4
