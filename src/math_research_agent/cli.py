"""Small package entrypoint directing users to the Gemini research CLI."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="math-research-agent",
        description="Use the mathematical research-agent command for proof runs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="math-research-agent; use `python -m math_research_agent.research --help`",
    )
    parser.parse_args(argv)
    parser.error("Use `uv run python -m math_research_agent.research --help` for the product CLI")
