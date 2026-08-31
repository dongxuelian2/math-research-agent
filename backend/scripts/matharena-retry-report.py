#!/usr/bin/env python3
"""Build an oracle-scored audit ledger for MathArena agent retries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from matharena.parser import check_answers, extract_answer, parse_answer


ACCEPTED_STOPS = {"PROVED", "CANDIDATE_READY"}
ROUND_DIRECTORIES = {
    1: ("round-001", "round-001-wrong"),
    2: ("round-002", "round-002-late", "round-002-apex12"),
    3: (
        "round-003-early",
        "round-003-mid",
        "round-003-apex2-10",
        "round-003-shortlist22",
        "round-003-apex1",
        "round-003-apex12",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--retries", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def official_score(result: dict) -> tuple[bool, str | None]:
    gold = result["problem"]["goldAnswer"]
    list_answer = "," in gold
    output = result.get("trackResult", {}).get("output") or ""
    parsed, _ = extract_answer(
        output,
        strict_parsing=False,
        list_answer=list_answer,
        typed_delimiters=True,
    )
    expected, _ = parse_answer(
        gold,
        list_answer=list_answer,
        typed_delimiters=True,
    )
    try:
        correct = bool(check_answers(parsed, expected))
    except Exception:
        correct = False
    extracted = result.get("answer", {}).get("extracted")
    return correct, extracted


def attempt_record(path: Path, root: Path, round_number: int) -> dict:
    result = read_json(path)
    correct, extracted = official_score(result)
    track = result.get("trackResult", {})
    stop_reason = track.get("stopReason")
    gate_accepted = result.get("status") == "COMPLETED" and stop_reason in ACCEPTED_STOPS
    return {
        "id": result["problem"]["id"],
        "round": round_number,
        "resultPath": str(path.relative_to(root.parent)),
        "jobStatus": result.get("status"),
        "stopReason": stop_reason,
        "gateAccepted": gate_accepted,
        "officialCorrect": correct,
        "success": gate_accepted and correct,
        "extracted": extracted,
        "goldAnswer": result["problem"]["goldAnswer"],
        "durationMs": result.get("durationMs"),
    }


def main() -> None:
    args = parse_args()
    comparison_path = args.original / "comparison.jsonl"
    comparison = [json.loads(line) for line in comparison_path.read_text().splitlines() if line]
    targets = sorted(
        row["id"]
        for row in comparison
        if not row["agent"]["workflowAccepted"] or not row["agent"]["correct"]
    )

    attempts: list[dict] = []
    for problem_id in targets:
        original_path = args.original / "results" / problem_id / "agent.json"
        attempts.append(attempt_record(original_path, args.retries, 0))

    for round_number, directories in ROUND_DIRECTORIES.items():
        seen: set[str] = set()
        for directory in directories:
            result_root = args.retries / directory / "results"
            if not result_root.exists():
                continue
            for path in sorted(result_root.glob("*/agent.json")):
                record = attempt_record(path, args.retries, round_number)
                problem_id = record["id"]
                if problem_id not in targets:
                    continue
                if problem_id in seen:
                    raise RuntimeError(f"duplicate result in round {round_number}: {problem_id}")
                seen.add(problem_id)
                attempts.append(record)

    attempts.sort(key=lambda item: (item["id"], item["round"]))
    by_id = {problem_id: [] for problem_id in targets}
    for attempt in attempts:
        by_id[attempt["id"]].append(attempt)

    statuses = []
    recovered_by_round = {"1": 0, "2": 0, "3": 0}
    for problem_id, problem_attempts in by_id.items():
        successes = [item for item in problem_attempts if item["success"] and item["round"] > 0]
        if successes:
            first = min(successes, key=lambda item: item["round"])
            final_status = "RECOVERED"
            recovered_round = first["round"]
            recovered_by_round[str(recovered_round)] += 1
        else:
            final_status = "WALL"
            recovered_round = None
        statuses.append(
            {
                "id": problem_id,
                "status": final_status,
                "recoveredRound": recovered_round,
                "goldAnswer": problem_attempts[0]["goldAnswer"],
                "attempts": problem_attempts,
            }
        )

    recovered = sum(item["status"] == "RECOVERED" for item in statuses)
    wall = len(statuses) - recovered
    baseline_agent_success = sum(
        bool(row["agent"]["workflowAccepted"] and row["agent"]["correct"])
        for row in comparison
    )
    baseline_direct_correct = sum(bool(row["direct"]["correct"]) for row in comparison)
    best_of_agent_success = baseline_agent_success + recovered
    report = {
        "schemaVersion": 1,
        "scoring": {
            "oracle": "MathArena extract_answer/parse_answer/check_answers",
            "strictParsing": False,
            "successDefinition": "officialCorrect && gateAccepted",
            "goldAnswerHiddenFromAgent": True,
            "retryPolicy": "up to three retry rounds; stop on success, otherwise WALL",
        },
        "summary": {
            "benchmarkTotal": len(comparison),
            "baselineAgentSuccess": baseline_agent_success,
            "baselineDirectCorrect": baseline_direct_correct,
            "targetCount": len(statuses),
            "recovered": recovered,
            "wall": wall,
            "recoveredByRound": recovered_by_round,
            "bestOfAgentSuccess": best_of_agent_success,
            "bestOfAgentRate": best_of_agent_success / len(comparison),
        },
        "operationalIncidents": [
            {
                "path": "matharena-20260831-gate-retries/round-002-early",
                "count": 9,
                "classification": "configuration",
                "reason": "Vertex project/location missing; failed before model invocation",
                "countedAsRetry": False,
            }
        ],
        "problems": statuses,
    }

    args.retries.mkdir(parents=True, exist_ok=True)
    (args.retries / "status.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (args.retries / "attempts.jsonl").open("w", encoding="utf-8") as handle:
        for attempt in attempts:
            handle.write(json.dumps(attempt, ensure_ascii=False) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
