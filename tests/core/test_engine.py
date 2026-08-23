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
