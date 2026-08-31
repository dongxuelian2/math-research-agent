#!/usr/bin/env python3
"""Render the four-category MathArena Agent vs Gemini comparison chart."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ORDER = ["aime_2026", "hmmt_feb_2026", "apex_2025", "apex_shortlist"]
LABELS = {
    "aime_2026": "AIME 2026",
    "hmmt_feb_2026": "HMMT Feb 2026",
    "apex_2025": "Apex 2025",
    "apex_shortlist": "Apex Shortlist",
}
AGENT_BLUE = "#2563EB"
AGENT_EDGE = "#1D4ED8"
GEMINI_GRAY = "#A7AFBC"
GEMINI_EDGE = "#7C8798"
INK = "#172033"
MUTED = "#667085"
GRID = "#E5EAF1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--retries", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = [
        json.loads(line)
        for line in (args.benchmark / "comparison.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    retry_report = json.loads((args.retries / "status.json").read_text(encoding="utf-8"))
    recovered = {
        item["id"] for item in retry_report["problems"] if item["status"] == "RECOVERED"
    }
    initial_agent_success = {
        row["id"]
        for row in rows
        if row["agent"]["correct"] and row["agent"]["workflowAccepted"]
    }
    final_agent_success = initial_agent_success | recovered

    categories = []
    for competition in ORDER:
        group = [row for row in rows if row["competition"] == competition]
        total = len(group)
        direct_correct = sum(bool(row["direct"]["correct"]) for row in group)
        agent_correct = sum(row["id"] in final_agent_success for row in group)
        categories.append(
            {
                "competition": competition,
                "label": LABELS[competition],
                "total": total,
                "geminiCorrect": direct_correct,
                "geminiAccuracy": direct_correct / total,
                "agentCorrect": agent_correct,
                "agentAccuracy": agent_correct / total,
            }
        )

    total = len(rows)
    gemini_total = sum(bool(row["direct"]["correct"]) for row in rows)
    agent_total = len(final_agent_success)
    data = {
        "metricDefinition": {
            "agent": "final success from the full multi-worker agent workflow",
            "gemini": "original Gemini 3.7 Flash direct response",
        },
        "overall": {
            "total": total,
            "agentCorrect": agent_total,
            "agentAccuracy": agent_total / total,
            "geminiCorrect": gemini_total,
            "geminiAccuracy": gemini_total / total,
            "agentLeadPercentagePoints": (agent_total - gemini_total) / total * 100,
        },
        "categories": categories,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "matharena-performance-data.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titleweight": "bold",
            "axes.edgecolor": "none",
            "axes.labelcolor": MUTED,
            "xtick.color": INK,
            "ytick.color": MUTED,
        }
    )
    fig, ax = plt.subplots(figsize=(14, 8), dpi=180)
    fig.patch.set_facecolor("#F7F9FC")
    ax.set_facecolor("white")

    x = np.arange(len(categories))
    width = 0.32
    agent_values = [item["agentAccuracy"] * 100 for item in categories]
    gemini_values = [item["geminiAccuracy"] * 100 for item in categories]
    agent_bars = ax.bar(
        x - width / 2,
        agent_values,
        width,
        label="Math Research Agent",
        color=AGENT_BLUE,
        edgecolor=AGENT_EDGE,
        linewidth=1.1,
        zorder=3,
    )
    gemini_bars = ax.bar(
        x + width / 2,
        gemini_values,
        width,
        label="Gemini 3.7 Flash (direct)",
        color=GEMINI_GRAY,
        edgecolor=GEMINI_EDGE,
        linewidth=1.1,
        zorder=3,
    )

    ax.set_ylim(0, 112)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.set_yticklabels([f"{value}%" for value in range(0, 101, 20)], fontsize=10)
    ax.set_ylabel("Accuracy", fontsize=11, fontweight="semibold", labelpad=12)
    ax.set_xticks(x)
    ax.set_xticklabels([item["label"] for item in categories], fontsize=11, fontweight="semibold")
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    ax.tick_params(axis="both", length=0, pad=9)
    for spine in ax.spines.values():
        spine.set_visible(False)

    for bars, key_correct, key_accuracy in (
        (agent_bars, "agentCorrect", "agentAccuracy"),
        (gemini_bars, "geminiCorrect", "geminiAccuracy"),
    ):
        for bar, item in zip(bars, categories):
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height + 1.5,
                f"{height:.1f}%",
                ha="center",
                va="bottom",
                fontsize=11,
                fontweight="bold",
                color=INK,
            )
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height - 4.0,
                f"{item[key_correct]}/{item['total']}",
                ha="center",
                va="top",
                fontsize=9.5,
                fontweight="semibold",
                color="white" if bars is agent_bars else "#344054",
            )

    fig.text(
        0.075,
        0.935,
        "MathArena Performance by Competition",
        fontsize=22,
        fontweight="bold",
        color=INK,
    )
    fig.text(
        0.075,
        0.895,
        "122 problems · Full agent workflow vs direct Gemini response",
        fontsize=11.5,
        color=MUTED,
    )
    overall_text = (
        f"Agent  {agent_total}/{total}  ({agent_total / total * 100:.2f}%)"
        f"     Gemini  {gemini_total}/{total}  ({gemini_total / total * 100:.2f}%)"
        f"     Lead  +{(agent_total - gemini_total) / total * 100:.2f} pp"
    )
    fig.text(
        0.075,
        0.842,
        overall_text,
        fontsize=11.5,
        fontweight="bold",
        color=AGENT_EDGE,
        bbox={"boxstyle": "round,pad=0.55", "facecolor": "#EFF6FF", "edgecolor": "#BFDBFE"},
    )
    legend = ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.05),
        ncol=2,
        frameon=False,
        fontsize=10.5,
        handlelength=1.2,
        columnspacing=2.2,
    )
    for text in legend.get_texts():
        text.set_color(INK)

    fig.text(
        0.075,
        0.035,
        "Source: MathArena benchmark run · Labels show correct / total",
        fontsize=9.5,
        color=MUTED,
    )
    plt.subplots_adjust(left=0.075, right=0.97, top=0.77, bottom=0.14)
    fig.savefig(args.out / "matharena-performance-comparison.png", dpi=220, bbox_inches="tight")
    fig.savefig(args.out / "matharena-performance-comparison.svg", bbox_inches="tight")
    plt.close(fig)
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
