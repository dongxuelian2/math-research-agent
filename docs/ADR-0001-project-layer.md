# ADR-0001: Compose a strict project layer around OpenProver

Status: accepted

## Context

OpenProver already supplies a mature Planner–Worker–Verifier loop, parallel workers, Whiteboard, Repository, on-disk state, recovery and provider archives. It is optimized around one theorem run, while the requested workflow needs a durable multi-document theorem graph and a stricter proof lifecycle.

## Decision

Keep upstream core intact and add a separately namespaced `openprover.math_research` package. It builds a local dependency context, calls the original `Prover` for candidate discovery, then applies project-level auditors and Archivist-only state changes. Store durable project metadata as UTF-8 JSON plus Markdown reports; do not introduce a database in phase one.

## Consequences

- Upstream updates remain reviewable because most new code is isolated.
- Existing OpenProver recovery/logging and worker mechanics remain in use.
- A candidate proof cannot bypass project audit state.
- Windows uses headless mode rather than a broad TUI rewrite.
- Cross-project theorem extraction remains a conservative human-reviewed migration process.

