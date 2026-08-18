"""Small package entrypoint directing users to the Gemini research CLI."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="openprover",
        description="Use the Gemini-native math-research command for research runs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="openprover package; use `python -m openprover.math_research --help`",
    )
    parser.parse_args(argv)
    parser.error(
        "Use `uv run python -m openprover.math_research` for the supported product CLI"
    )
