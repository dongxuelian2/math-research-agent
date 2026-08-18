# Gemini Research Engine

This package contains the proving engine used by the repository's Gemini-native
research product. The supported public entrypoint is the math-research module;
the root [`README.md`](../README.md) documents the one-command Bash bootstrap.

```bash
uv sync --extra test
uv run python -m openprover.math_research demo --project ../projects/observatory-demo
uv run python -m openprover.math_research observatory --project ../projects/observatory-demo
```

The research layer owns Gemini routing, strict Pydantic response contracts,
audit gates, repair successors, provenance, and the Research Observatory. The
proving engine is reached through the public `research_policy` boundary and is
not extended by private orchestration hooks.
