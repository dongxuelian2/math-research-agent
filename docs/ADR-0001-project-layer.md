# ADR-0001: Make the Gemini research layer the product boundary

Status: accepted

## Context

The proving engine supplies candidate search, parallel workers, repository
artifacts, recovery, and optional Lean execution. The product needs a durable
theorem graph, strict typed control messages, independent audits, and a visible
repair lifecycle.

## Decision

Use `openprover.math_research` as the sole product boundary. It builds the
dependency context, routes every model call through Gemini, calls the proving
engine through the public `research_policy` hook, validates provider responses
with Pydantic, and applies project-level audit/state transitions. Durable
metadata remains UTF-8 JSON plus Markdown reports.

## Consequences

- Candidate search and audit coordination are separate components.
- A candidate proof cannot bypass the typed audit gate.
- Failed routes always produce a repair successor and immutable evidence.
- The supported developer workflow is Bash plus `uv`.
