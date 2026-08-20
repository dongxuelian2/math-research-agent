# Regression Evidence

## Provenance

- Starting integration HEAD: `deefce7eed20e8eaf8b96ff7ad63eadc8c7bcb6c`
- Local historical HEAD: `d224491bc75c7499c8670f810e2831511cdf801c`
- Remote HEAD at fetch: `deefce7eed20e8eaf8b96ff7ad63eadc8c7bcb6c`
- Branch: `codex/v3-2-reconciliation`
- Worktree: isolated from clean `E:\tool\math` main checkout

## Baseline

- Relevant remote suite with writable basetemp: `125 passed in 2.64s`.
- An initial unrestricted local collection failed because the sandbox cannot access the user temp directory and a Windows interrupt test executes a subprocess at import time. This was an environment failure, not treated as a test pass.

## Phase-1 evidence

- Focused Worker event production suite: `5 passed`.
- Relevant post-change suite: `129 passed in 3.88s` (before the fifth focused policy case was added); final suite is recorded below.
- PowerShell parser: `scripts/bootstrap.ps1 syntax OK`; `run_math_agent.ps1 syntax OK`.
- Windows bootstrap: uv locked sync completed; package imports returned `openprover import: OK`.
- Windows launcher: `run_math_agent.ps1 -Command status -Project demo -Target demo-odd-sum` exited 0 and returned the theorem JSON.
- Production E2E asserts at least three Worker event sidecars, three Verifier sidecars, candidate artifact, Audit Gate PASS, and isolated theorem promotion to PROVED.

## Required final checks

- Final relevant/local-safe suite: `131 passed in 3.34s`.
- The sandbox-incompatible import-time Windows interrupt subprocess remains excluded locally; the hosted Linux quality job still collects the complete suite.
- `ruff format openprover`: 6 changed files formatted; subsequent format check passed.
- `ruff check openprover`: passed.
- `python -m compileall -q openprover`: passed.
- `git diff --check`: passed (Git emitted only an LF→CRLF working-copy warning for the workflow file).
- Final git status is intentionally dirty with the documented source, test, CI, PowerShell, and report changes; no unrelated tracked file is modified.
- Hosted CI remains pending until an authorized push/PR.

## Canonical Artifact Authority P0

- A–H resolver/runtime/provenance suite plus production manifest→orchestrator
  coverage: `10 passed in 0.91s`.
- Existing replay, async-pipeline, and retrieval regression slice:
  `23 passed in 1.03s`.
- Post-P0 local-safe full suite: `141 passed in 4.27s`; as in the frozen
  baseline, the import-time Windows interrupt subprocess is excluded because
  the managed sandbox returns `WinError 1920` before pytest collection.
- `ruff format --check openprover`, `ruff check openprover`, compileall, and
  `git diff --check` passed after the P0 change.
- The production missing-body case checkpoints as
  `BLOCKED_AUTHORITY_SOURCE_UNAVAILABLE` before candidate construction.
- The production resume case restores persisted requirements without requiring
  the caller to re-supply the manifest and verifies the immutable body digest.
- Full-suite and final quality evidence will be refreshed after provider and
  checkpoint phases. Hosted CI remains `PENDING_PUSH`.
