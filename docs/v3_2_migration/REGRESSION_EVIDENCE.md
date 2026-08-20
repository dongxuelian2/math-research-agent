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

## Provider preservation

- Historical Codex CLI and OpenAI adapter source was recovered from
  `d224491bc75c7499c8670f810e2831511cdf801c` and adapted to the current typed
  provider contract without replacing the current Gemini/Vertex/mock factory.
- Focused provider, structured-output, tool-loop, and heterogeneous-routing
  suite: `38 passed in 2.02s`.
- Post-restoration local-safe full suite: `170 passed in 6.04s`.
- `uv lock --check` resolves 49 packages; the OpenAI SDK is pinned to
  `>=2.53.0,<3` and currently locks to 2.54.0.
- Format check, lint, compileall, and `git diff --check` passed. No live
  provider credentials were used. Hosted CI remains `PENDING_PUSH`.

## Legacy checkpoint migration

- Classification, source immutability, idempotent provenance, provider
  provenance separation, unknown-schema default, hash-only authority, and real
  orchestrator-resume suite: `8 passed in 1.73s`.
- Unknown compatibility is `REVALIDATION_REQUIRED`; only a known semantic
  conflict becomes `INCOMPATIBLE`.
- Legacy current-trust labels are retained only as `LEGACY_VERIFIED` or
  `LEGACY_EVIDENCE`; neither the theorem registry nor current trust state is
  upgraded by migration.
- Production hash-only canonical resume re-resolves the actual source bytes and
  retains the original checkpoint byte-for-byte.
- Final local-safe suite and cross-platform inspection follow after the
  documentation freeze. Hosted CI remains `PENDING_PUSH`.

## Final pre-Truth-plane acceptance

- Local-safe repository suite: `178 passed in 6.62s`.
- The managed Windows host still excludes only
  `openprover/tests/test_interrupt_race.py`, whose import-time subprocess fails
  during collection with `WinError 1920`; the same limitation was recorded at
  the frozen baseline.
- `uv lock --check`: passed with 49 resolved packages.
- `ruff format --check openprover`: 101 files already formatted.
- `ruff check openprover`, compileall, and `git diff --check`: passed.
- Bash bootstrap syntax: passed via `bash -n`.
- `scripts/bootstrap.ps1` and `run_math_agent.ps1`: PowerShell parser passed.
- All paths referenced by the Ubuntu and Windows workflow jobs exist. No local
  standalone YAML parser is installed, so semantic hosted-workflow execution is
  not claimed.
- `HOSTED_CI = PENDING_PUSH` because no push was authorized or performed.

## PHASE 3 Truth Plane Foundation

- Audit-first ordering: `PHASE3_TRUTH_PATH_AUDIT.md` was committed before any
  PHASE 3 production code.
- Typed identity/snapshot/facade/mutation plus production integration suite:
  T1–T13 `24 passed in 3.06s` in deterministic local fixtures.
- Root, dependency, authority, and trust-policy races are injected between
  audit and promotion; every stale case retains an intent, writes blocked
  evidence, emits no receipt, and leaves the target non-PROVED.
- Unchanged production E2E reaches Planner, three Workers, Worker Verifiers,
  Candidate, specialist/final audits, exact-snapshot gate, mutation intent,
  compare-and-transition, receipt, and PROVED.
- One repeated E2E invocation encountered a transient Windows `WinError 5`
  while the pre-existing routing code replaced a temp JSON file. The same test
  immediately passed under a new isolated temp root; it is not counted as a
  code pass or as the known interrupt-test exclusion.
- Final repository-wide local-safe suite, excluding only the separately run
  import-time interrupt file: `200 passed in 8.90s`.
- Provider-focused Gemini/Vertex/Codex/OpenAI/routing suite:
  `38 passed in 1.95s`.
- Checkpoint migration suite: `8 passed in 1.84s`.
- Canonical authority suite including T8/T9: `11 passed in 2.27s`.
- The separately invoked `test_interrupt_race.py` remains
  `ENVIRONMENT_BLOCKED`: collection executes a POSIX process-group test at
  import time and this Windows host has no `os.killpg`. It produced one
  collection error and is not marked PASS, deleted, or permanently skipped.
- `uv lock --check`: passed with 49 packages.
- PowerShell parsers for `scripts/bootstrap.ps1` and `run_math_agent.ps1`: PASS.
- `bash -n scripts/bootstrap.sh scripts/run_benchmark.sh`: PASS.
- Actual PowerShell status launcher: exit 0 with the demo theorem JSON.
- `ruff format --check openprover`: 108 files already formatted.
- `ruff check openprover`: PASS.
- `python -m compileall -q openprover`: PASS.
- `git diff --check`: PASS; only expected LF→CRLF working-copy warnings.
- `HOSTED_CI = PENDING_PUSH`; no push was authorized or performed.

## PHASE 4 Research Plane Foundation

- Audit-first ordering: `PHASE4_RESEARCH_PATH_AUDIT.md` was committed as
  `7367184` before any PHASE 4 production source change.
- R1–R21 ResearchMap/Obligation/Directive/Closure/RouteFailure/production E2E
  suite: `21 passed in 4.72s` after the final formatter pass.
- Repository-wide local-safe suite, excluding only the separately invoked
  import-time interrupt file: `221 passed in 12.61s` after the final formatter
  pass.
- Truth identity/store/mutation plus production Planner/Worker/Verifier/Audit
  E2E slice: `27 passed in 3.55s`.
- Provider-focused Gemini/Vertex/Codex/OpenAI/routing suite:
  `38 passed in 2.22s`.
- Checkpoint migration suite: `8 passed in 1.94s`.
- Canonical artifact authority suite: `11 passed in 2.60s`.
- R19 real production mock path reached Planner, at least three Workers,
  Worker Verifiers, Candidate, specialist/final audits, SessionClosure,
  `RESOLUTION_ACCEPTED`, ResearchMap v2, then the independent PHASE 3
  TruthMutation receipt.
- R20 retained O2/O3 as `OPEN` while only O1 became `RESOLVED`; root theorem
  truth remained `OPEN` because this fixture did not invoke TruthMutation.
- The separately invoked `openprover/tests/test_interrupt_race.py` remains
  `ENVIRONMENT_BLOCKED`. Collection executes `os.killpg` on Windows and raised
  `AttributeError: module 'os' has no attribute 'killpg'`; it produced one
  collection error and is not marked PASS, deleted, or permanently skipped.
- `uv lock --check --project openprover`: passed with 49 packages.
- `ruff format openprover` formatted 14 PHASE 4-touched files; subsequent
  `ruff format --check openprover` reported 120 files already formatted.
- `ruff check openprover`: PASS.
- `python -m compileall -q openprover`: PASS.
- `bash -n scripts/bootstrap.sh scripts/run_benchmark.sh`: PASS.
- PowerShell parsers for `scripts/bootstrap.ps1` and `run_math_agent.ps1`: PASS.
- Actual PowerShell status entrypoint: exit 0 with the `demo-odd-sum` theorem
  JSON.
- `git diff --check`: PASS; only expected LF→CRLF working-copy warnings.
- `HOSTED_CI = PENDING_PUSH`; no push was authorized or performed.

## PHASE 5 Architecture Governance

- Audit-first ordering: `PHASE5_GOVERNANCE_PATH_AUDIT.md` was committed as
  `4b6bc70` before any PHASE 5 production source change.
- StructuralEffect/review-clock, ArchitectureReview/probe,
  ArchitectureCritic/authorization, and positive/negative governance E2E:
  `22 passed in 4.95s`.
- The production governance E2E created ClaimSnapshot C1, ResearchMap v1 with
  O1/O2/O3, two real TacticalSessions and tactical effects, a mandatory review,
  bounded supporting probe, complete O1/O2/O3→N1/N2 transfers, independent
  critic approval, authorized application, and exactly ResearchMap v2. The
  theorem record and immutable v1 were unchanged.
- The negative E2E omitted O3 transfer, produced critic `SCOPE_LOSS`, rejected
  authorization, and retained v1 plus all three old obligations.
- G22 restored an exact due clock through a campaign checkpoint. G23 converted
  governance-less legacy campaign state to `GOVERNANCE_REVIEW_REQUIRED` with no
  fabricated ArchitectureReview.
- Repository-wide local-safe suite, excluding only the separately invoked
  import-time interrupt file: `244 passed in 15.38s`.
- PHASE 3 Truth Plane slice: `24 passed in 2.79s`.
- PHASE 4 Research Plane slice: `21 passed in 4.97s`.
- Provider-focused Gemini/Vertex/Codex/OpenAI/routing suite:
  `38 passed in 2.49s`.
- Checkpoint migration suite: `8 passed in 2.15s`.
- Canonical authority suite: `11 passed in 2.94s`.
- Typed Worker-event production plus explicit plan-over-capacity regression:
  `7 passed in 2.70s`.
- One parallel focused invocation encountered a transient Windows `WinError 5`
  while replacing a per-test clock projection. The complete local-safe suite
  had already passed, and the exact 22-test PHASE 5 slice immediately passed
  under a fresh isolated base temp. The failed invocation is not counted as a
  pass.
- The separately invoked `openprover/tests/test_interrupt_race.py` remains
  `ENVIRONMENT_BLOCKED`: collection executes `os.killpg` on Windows and raised
  `AttributeError: module 'os' has no attribute 'killpg'`. It produced one
  collection error and is not marked PASS, deleted, or permanently skipped.
- `uv lock --check --project openprover`: PASS with 49 resolved packages.
- `ruff format openprover` formatted one final test; the subsequent check
  reported 131 files already formatted. `ruff check openprover`: PASS.
- `python -m compileall -q openprover/openprover openprover/tests`: PASS.
- `bash -n scripts/bootstrap.sh scripts/run_benchmark.sh`: PASS.
- PowerShell parsers for `scripts/bootstrap.ps1` and `run_math_agent.ps1`: PASS.
- Actual PowerShell status entrypoint: exit 0 with the `demo-odd-sum` theorem
  JSON.
- `git diff --check`: PASS; only expected LF→CRLF working-copy warnings.
- `HOSTED_CI = PENDING_PUSH`; no push was authorized or performed.
