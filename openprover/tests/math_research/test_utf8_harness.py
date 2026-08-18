import json
from pathlib import Path

from openprover.math_research.campaign import FailureItem, FailureMap


UNICODE_MATH = r"中文：−γ + β ± 1 → x ∈ S；\frac{a}{b}。"


def test_campaign_artifacts_round_trip_utf8_math(tmp_path):
    failure_map = FailureMap(
        run_id="utf8-run",
        target_id="unicode-target",
        items=[FailureItem(
            category="MATHEMATICAL_GAP",
            exact_rejected_claim=UNICODE_MATH,
            auditor="边界审计器",
            candidate_location="证明/§γ",
            authority_expected="SEM-β-01",
            blocking=True,
            repair_suggestion="保留 ± 两个分支。",
            affected_branch="高层→低层",
        )],
    )
    json_path, md_path = failure_map.write(tmp_path)
    assert UNICODE_MATH in md_path.read_text(encoding="utf-8")
    item = json.loads(json_path.read_text(encoding="utf-8"))["items"][0]
    assert item["exact_rejected_claim"] == UNICODE_MATH
    assert item["auditor"] == "边界审计器"


def test_windows_launcher_forces_utf8_environment():
    launcher = (
        Path(__file__).resolve().parents[2] / "scripts" / "run_math_agent.ps1"
    ).read_text(encoding="utf-8")
    assert "$env:PYTHONUTF8 = '1'" in launcher
    assert "$env:PYTHONIOENCODING = 'utf-8'" in launcher
    assert "[Console]::OutputEncoding" in launcher
