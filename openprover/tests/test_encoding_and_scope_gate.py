from pathlib import Path

from openprover.llm._base import archive
from openprover.prover import Prover


def test_archive_is_utf8_for_unicode_math_and_chinese(tmp_path: Path):
    path = tmp_path / "auditor-call.md"
    archive(
        "test-model",
        tmp_path / "archive",
        1,
        "dependency_auditor",
        "\u4e2d\u6587\u8bf4\u660e\uff1aa \u2212 b = 0\uff1b\n\u542b\u6570\u5b66\u7b26\u53f7 \u2200\u2203\u21d2\u3002",
        "\u5ba1\u8ba1\u7cfb\u7edf\u63d0\u793a",
        None,
        {"result": "通过 − PASS", "usage": {}},
        None,
        1,
        path,
        result_text="\u5019\u9009\u542b U+2212:\u2212",
    )
    assert "\u2212" in path.read_text(encoding="utf-8")
    assert "\u5ba1\u8ba1\u7cfb\u7edf\u63d0\u793a" in path.read_text(encoding="utf-8")
    assert "\u2212" in path.with_suffix(".raw.json").read_text(encoding="utf-8")


def test_unresolved_scope_blocker_rejects_submission():
    whiteboard = (
        "The h != 1 branch is unresolved.\n"
        "Status: BLOCKED — EXTERNAL DEPENDENCY EXPANSION REQUIRED."
    )
    blocker = Prover._scope_submission_blocker(whiteboard)
    assert blocker is not None
    assert "blocker" in blocker


def test_closed_scope_gap_allows_submission():
    whiteboard = (
        "Earlier scope gap was repaired from the authorized CD6 source.\n"
        "SCOPE_CLOSURE: PASS"
    )
    assert Prover._scope_submission_blocker(whiteboard) is None


def test_unrelated_whiteboard_does_not_block_submission():
    assert Prover._scope_submission_blocker("Low branch checked; no gaps.") is None
