#!/usr/bin/env python3
"""Render the First Proof long-horizon agent-vs-direct score chart."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ORDER = ["prob-001", "prob-003", "prob-004", "prob-006", "prob-009"]
LABELS = {
    "prob-001": "Computability",
    "prob-003": "Discrete probability",
    "prob-004": "Metric geometry",
    "prob-006": "Lattice theory",
    "prob-009": "Algebraic combinatorics",
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
    parser.add_argument("--review-summary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = json.loads(args.review_summary.read_text(encoding="utf-8"))
    problems = summary["problems"]
    rows = []
    for problem_id in ORDER:
        problem = problems[problem_id]
        rows.append(
            {
                "id": problem_id,
                "label": LABELS[problem_id],
                "agentScore": problem["tracks"]["agent"]["meanTotal"],
                "directScore": problem["tracks"]["direct"]["meanTotal"],
            }
        )

    overall = {
        "agentScore": summary["overall"]["agent"]["meanTotal"],
        "directScore": summary["overall"]["direct"]["meanTotal"],
        "agentLead": summary["overall"]["agent"]["meanTotal"]
        - summary["overall"]["direct"]["meanTotal"],
    }
    data = {
        "metricDefinition": "Mean score over two reversed-order blind referee passes, out of 100",
        "overall": overall,
        "problems": rows,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "first-proof-long-horizon-performance-data.json").write_text(
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

    x = np.arange(len(rows))
    width = 0.32
    agent_values = [row["agentScore"] for row in rows]
    direct_values = [row["directScore"] for row in rows]
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
    direct_bars = ax.bar(
        x + width / 2,
        direct_values,
        width,
        label="Gemini 3.7 Flash (direct)",
        color=GEMINI_GRAY,
        edgecolor=GEMINI_EDGE,
        linewidth=1.1,
        zorder=3,
    )

    ax.set_ylim(0, 112)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.set_yticklabels([f"{value}" for value in range(0, 101, 20)], fontsize=10)
    ax.set_ylabel("Mean referee score", fontsize=11, fontweight="semibold", labelpad=12)
    ax.set_xticks(x)
    ax.set_xticklabels([row["label"] for row in rows], fontsize=10, fontweight="semibold")
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    ax.tick_params(axis="both", length=0, pad=9)
    for spine in ax.spines.values():
        spine.set_visible(False)

    for bars, key in ((agent_bars, "agentScore"), (direct_bars, "directScore")):
        for bar, row in zip(bars, rows):
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height + 1.5,
                f"{height:.1f}",
                ha="center",
                va="bottom",
                fontsize=11,
                fontweight="bold",
                color=INK,
            )
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                max(height - 4.0, 3.0),
                f"{row[key]:.1f}/100",
                ha="center",
                va="top",
                fontsize=8.5,
                fontweight="semibold",
                color="white" if bars is agent_bars else "#344054",
            )

    fig.text(
        0.075,
        0.935,
        "First Proof Long-Horizon Quality by Problem",
        fontsize=22,
        fontweight="bold",
        color=INK,
    )
    fig.text(
        0.075,
        0.895,
        "5 research problems · Blind referee mean · dynamic agent vs direct Gemini",
        fontsize=11.5,
        color=MUTED,
    )
    overall_text = (
        f"Agent  {overall['agentScore']:.1f}/100"
        f"     Gemini  {overall['directScore']:.1f}/100"
        f"     Lead  +{overall['agentLead']:.1f} pts"
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
        "Source: First Proof Second Batch · Labels show mean blind-referee score / 100",
        fontsize=9.5,
        color=MUTED,
    )
    plt.subplots_adjust(left=0.075, right=0.97, top=0.77, bottom=0.17)
    fig.savefig(args.out / "first-proof-long-horizon-performance.png", dpi=220, bbox_inches="tight")
    fig.savefig(args.out / "first-proof-long-horizon-performance.svg", bbox_inches="tight")
    plt.close(fig)
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
