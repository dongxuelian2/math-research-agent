# OpenProver Research Harness v2 — Pre-change Architecture Audit

Audit date: 2026-08-09 (Asia/Shanghai)
Repository: `E:\tool\math\openprover`
Audited branch before isolation: `math-research-custom`
Upgrade branch: `codex/research-harness-v2`
HEAD before changes: `a005e216fd5f79e98305a63a55abfae8b14ce779`

## 1. Safety baseline

The repository was not clean before this upgrade. These pre-existing changes are preserved and are not to be reset, cleaned, or silently absorbed into phase commits:

- modified: `openprover/llm/_base.py`
- modified: `openprover/math_research/cli.py`
- modified: `openprover/prover.py`
- untracked: `configs/models.a1_replay.json`
- untracked: `configs/models.a1_repair_replay.json`
- untracked: `tests/test_encoding_and_scope_gate.py`

The tracked pre-existing diff is 160 insertions and 55 deletions. It contains partial UTF-8 fixes, Codex-related compatibility fixes, and a string-scanning scope submission blocker. The v2 implementation must build around those edits without treating them as a clean architectural foundation.

No `AGENTS.md` exists under `E:\tool\math`. The outer directory `E:\tool\math` is not a Git repository; only `E:\tool\math\openprover` is version-controlled. Consequently, `E:\tool\math\run_math_agent.ps1` is currently outside Git rollback protection.

Read-only invariant baselines:

| Protected tree | Files | Aggregate SHA-256 over sorted `(file hash, relative path)` rows |
|---|---:|---|
| first GA1-1 replay run | 1660 | `C093BD522B4B8BBCE577CF50E157524AE7DCFEB0FE246E2FA0594EBA4B79010B` |
| GA1-1 repair workspace | 977 | `3B62C201036C791BF94BF41CF02B06231BC644E222C2BAF1D79EA759CA525162` |
| `projects/main` | 262 | `100F03C435C22373F88A8DD24406E37D860BAA0ED7CBE661DD7DAFBEE4F16940` |

## 2. Current component map

The system is two nested harnesses:

1. Upstream OpenProver core (`openprover/prover.py`, `prompts.py`, `budget.py`, provider clients) owns Planner steps, Worker execution, Worker Verifier, Whiteboard/repository persistence, candidate `PROOF.md`, step archives, and low-level resume.
2. The local research layer (`openprover/math_research`) owns project theorem records, dependency-sliced context, the outer run state, specialist audits, the final gate, status transitions, usage summaries, and Archivist promotion.

The outer orchestrator currently follows:

`CREATED -> CONTEXT_READY -> CANDIDATE_READY -> AUDITS_READY -> COMPLETE`

This phase sequence is a run-local checkpoint mechanism, not a campaign state machine.

## 3. Required factual findings

### 3.1 Source of the four-hour budget

There are two sources:

- Upstream CLI default: `openprover/cli.py` resolves the absence of `--max-time`/`--max-tokens` to `parse_duration("4h")` (14,400 seconds). The README advertises the same default.
- Local research configs: the standard example configs and `configs/models.a1_replay.json` explicitly set `budget.mode = "time"`, `budget.limit = 14400`, and `conclude_after = 0.99`.

The outer orchestrator itself is configurable through the JSON config, but its fallback is only 900 seconds (`orchestrator.py`, `budget_cfg.get("limit", 900)`). It constructs the upstream `Budget` and passes it to `Prover`.

The important semantic defect is not the numeric default. At 80% the upstream intervention says an imperfect submission is better than no submission; at 95% it commands immediate submission. The run loop also exits at `should_conclude()` without exposing a typed time-exhaustion result to the outer harness.

### 3.2 Run COMPLETE/REJECTED transitions

Outer run phase and theorem lifecycle are separate but currently coupled in finalization:

- A produced `PROOF.md` is copied to `CANDIDATE_PROOF.md`.
- The theorem transitions `IN_RESEARCH -> CANDIDATE_PROOF -> AUDITING`.
- Gate PASS causes Archivist to transition `AUDITING -> PROVED`, write a resolution report, and mark the theorem branch `CLOSED`.
- Gate failure causes `AUDITING -> REJECTED`, writes `FAILURE_REPORT.md`, then checkpoints the run as `phase=COMPLETE`, `status=REJECTED`.
- No candidate causes the theorem to become `PARTIAL` and the run to become `COMPLETE/PARTIAL`.

Thus mathematical rejection, auditor execution failure, provider exhaustion, and some incomplete-run outcomes can all terminate the run with final semantics that are too coarse.

### 3.3 Actual `submit_proof` code path

The path is:

1. Planner emits an `<OPENPROVER_ACTION>` TOML block parsed by `prompts.parse_planner_toml`.
2. `Prover._do_step()` records the parsed plan.
3. `Prover._execute_plans()` dispatches `action == "submit_proof"` to `_handle_submit_proof()`.
4. `_handle_submit_proof()` checks mode, resolves `proof_slug` from the run repository, runs the current Whiteboard string blocker, writes `openprover/PROOF.md`, and calls `_check_completion()`.
5. The outer `ResearchOrchestrator._run_openprover_candidate()` copies that file to run-level `CANDIDATE_PROOF.md`.

No structured dependency/scope authority gate is consulted on this path.

### 3.4 Current hard blocker implementation

The current uncommitted repair adds `Prover._scope_submission_blocker(whiteboard)`. It searches free text for a small set of regexes such as `SCOPE_GAP`, `UNRESOLVED_SCOPE`, and `REQUIRED_DEPENDENCY_EXPANSION`. Any of four closure strings, including `SCOPE_CLOSURE: PASS`, bypasses the blocker.

This is not a hard trust-kernel gate because:

- it sees only Whiteboard prose;
- it covers only a subset of required blocker types;
- it has no authority IDs or source hashes;
- a model-authored marker can clear it;
- it does not bind to dependency registry versions or the candidate;
- budget pressure can still induce submission attempts.

It is useful as a compatibility signal but cannot be the v2 gate.

### 3.5 Dependency slice generation

`ContextBuilder._dependency_closure()` recursively follows each theorem record's `dependencies`. `ProjectStore.resolve_dependency()` resolves an ID to either a theorem JSON file or a premise JSON file. Cycles are detected using an active recursion stack.

The resulting flat slice is divided into:

- allowed dependencies: theorem status exactly `PROVED`;
- blocked dependencies: every other theorem status;
- satisfied premises: active premise nodes with source provenance.

Source text is included for the target, direct dependencies, all premise nodes, and all recursive dependencies only when `expand=True`. Files are constrained beneath the project root by `safe_source_path()`.

There is no Foundation/Semantic/Project classification, no registry-version binding, and no claim-level authority report.

### 3.6 Source of theorem dependency metadata

Authoritative edges for the current code come from each `theorems/<id>.json` record's `dependencies` array. `index.json` is rebuilt as a derived summary and must not be treated as authority. `downstream_dependents` is also derived by `rebuild_index()`.

Premises live in `premises/<id>.json`, require a `node_type`, `active=true`, source file, and provenance list, but explicitly do not count as proved theorems.

Historical migration metadata (`primary_source`, index rows, confidence, package labels) is stored on theorem records and has previously been exposed to context. The current auditor prompt does not forbid using such metadata as mathematical authority.

### 3.7 Replay allowed/forbidden source enforcement

The GA1-1 replay manifests and leak audits are external runtime artifacts under `replay_controls`. The observed enforcement mechanism was manual materialization: only selected source files/sections were copied into the isolated replay project, and `ContextBuilder.safe_source_path()` prevented escaping that project root.

The manifest records allowed dependency IDs, materialized files and hashes, excluded files/sections, and `main_project_write_allowed=false`. However, the running code does not parse the replay manifest, verify its hashes, enforce source chronology, or reject a newly materialized forbidden source. The leak audit is documentary, not a code-level guard.

The first and repair workspaces therefore achieved isolation through careful construction, not through a reusable manifest policy engine.

### 3.8 How auditors return PASS/FAIL

Four specialist auditors run concurrently: Counterexample Hunter, Dependency Auditor, Exhaustiveness/Converse Auditor, and Boundary Auditor. Each is prompted to return JSON with `verdict`, `pass`, `findings`, `failure_reasons`, and `computational_evidence`.

The Final Proof Auditor receives the candidate, context, and specialist JSON, then returns `PASS/FAIL`, `pass`, and boolean gate criteria. The outer gate combines all specialist `pass` booleans, final criteria, blocked dependencies, cycles, and the final PASS.

There is no `INCONCLUSIVE`, no `execution_status`, no `cross_audit_notes`, and no strict schema validation beyond extracting a JSON object.

### 3.9 Infrastructure errors entering the gate

Any exception during a specialist call or JSON parse is converted to `verdict=FAIL`, `pass=false`. A Final Auditor exception is handled the same way. Those failure strings are appended to `AuditGate.failure_reasons`, and finalization normally transitions the theorem to `REJECTED`.

Therefore encoding errors, subprocess failures, timeouts, malformed JSON, and provider transport errors currently masquerade as mathematical audit failures.

### 3.10 Provider quota and rate-limit handling

The OpenAI Responses provider classifies connection/timeouts, 408/409/429, and 5xx as retryable and performs bounded exponential retries. It classifies `insufficient_quota` and billing hard limits as non-retryable `quota_exceeded`.

The Codex CLI provider performs bounded retries, classifies explicit rate limits as retryable, and classifies subscription usage-cap markers as non-retryable `usage_limit_reached`. It uses `Popen(..., text=True, encoding="utf-8", errors="replace")` and terminates the process tree on timeout.

The outer orchestrator has no typed catch that maps these errors to campaign checkpoint states. In the upstream core, string-based spending/rate-limit checks may set `_spending_limit_hit`, but the outer layer sees only whether a `PROOF.md` appeared; no proof normally becomes `PARTIAL`, not `BLOCKED_PROVIDER_QUOTA`.

### 3.11 WorkerCount application

CLI `--workers` is passed to `ResearchOrchestrator.worker_count`, then to `Prover(max_workers=...)`. `Prover._handle_spawn()` truncates Planner tasks to `self.max_workers` and runs them in a thread pool sized to the truncated task count. Worker Verifiers run in a separate parallel pool.

All workers share one configured Worker provider/role and receive Planner-authored descriptions. There is no required role field, heterogeneous role-specific prompt, obligation-aware assignment, or 4-to-6 escalation policy.

### 3.12 Campaign abstraction

`ProjectStore.initialize()` creates a `campaigns/` directory, and historical source material uses the word campaign, but no campaign record, campaign status, parent/successor link, repair cycle, or campaign resume API exists. The effective abstraction is a list of independent run directories plus mutation of one theorem record.

### 3.13 Why COMPLETE cannot be resumed

At the outer layer, `ResearchOrchestrator.run()` immediately returns if `state.phase == "COMPLETE"`. It never reopens the run. At the upstream CLI layer, any run directory with `PROOF.md` or `DISCUSSION.md` is treated as finished and opened in inspect mode, not resumed.

This preserves completed-run immutability, but there is no successor mechanism above it. The current repair practice manually created a new replay workspace/run and altered a copied theorem state.

### 3.14 Current UTF-8 path

The math-research layer mostly uses explicit UTF-8 for JSON/Markdown and `utf-8-sig` for source ingestion. The current uncommitted repairs add explicit UTF-8 to many `Prover` repository, Whiteboard, step, archive, proof, and discussion paths; `_base.archive()` also writes UTF-8. The Codex provider already uses UTF-8 subprocess pipes.

Remaining risks found in the wider Windows path include:

- upstream `openprover/cli.py` still has default-encoding `read_text`/`write_text` for run config and inputs;
- `logging.FileHandler` has no explicit encoding;
- Lean subprocess/file paths still contain default text/file encodings;
- outer `run_math_agent.ps1` does not set `PYTHONUTF8` or `PYTHONIOENCODING`;
- only result JSON stdout is reconfigured by the current CLI repair;
- other provider/backend paths have not been normalized end to end.

The existing regression test covers Chinese, `−`, and a few logical symbols, but not the full required set (`γ`, `β`, `±`, `→`, `∈`, LaTeX) or launcher/subprocess integration.

### 3.15 Dependency policy in current audit prompts

The Dependency Auditor is told to require every invoked mathematical result to be in the flat allowed `PROVED` dependency slice and to reject unnamed lemmas, partial results, circularity, and hidden uses. The Final Auditor receives the same flat context.

The prompt does not distinguish foundational mathematics, scoped semantics, project theorems, local proofs, or computational certificates. As a result, classical mathematics is either illicit or informally tolerated; project definitions can be replaced by metadata; and a fully proved local lemma can be mistaken for a missing external dependency.

## 4. GA1-1 evidence relevant to the architecture

The second repair run demonstrates the taxonomy problem directly:

- Exhaustiveness and Boundary passed.
- The remaining mathematical-admissibility blockers were `G_prim -> h=1` authority and classical Jacobi/reciprocity authority.
- Counterexample Hunter returned FAIL because it noticed dependency gaps, even though it found no counterexample.
- The candidate cited “authoritative package metadata,” which the current protocol did not classify separately from theorem authority.

The repair manifest also lists `H_SCOPE_CLOSURE.md` as a materialized scope bridge. The requested v2 design correctly replaces this repair-only metadata/steering device with a source-hashed semantic registry item grounded in GP3.

## 5. Architectural conclusions before modification

The existing mathematical-agent core is viable and should remain recognizable. The required changes belong around it:

1. Add a versioned three-layer authority kernel: Foundation Registry, Semantic Registry, and Project Theorem DAG.
2. Produce a structured dependency report with claim classes and exact authority IDs; reject package metadata as proof authority.
3. Replace free-text submission eligibility with a machine-readable pre-submit decision containing all hard blocker types.
4. Split auditor `domain_verdict` from `execution_status`, and prevent infrastructure failures from causing theorem rejection.
5. Preserve immutable completed runs while adding campaign records and successor runs.
6. Convert audit failures to typed failure maps and bounded repair cycles in opt-in long-horizon mode.
7. Map time, quota, infrastructure retry exhaustion, and human stop to checkpoint/block states rather than proof submission or theorem rejection.
8. Add role-aware scheduling and strategy fingerprints without rewriting Planner/Worker reasoning internals.
9. Turn replay manifests and leak audits into executable policy checks with inherited allow/deny lists and hashes.
10. Keep normal mode compatible; all long-horizon automation must be explicit opt-in.

## 6. Phase boundaries and rollback plan

- Phase 1 commit: authority registries, dependency/admissibility model, auditor verdict taxonomy, package-metadata ban, tests, and trust-kernel documentation.
- Phase 2 commit: campaign records, immutable successors, failure maps, repair cycles, hard pre-submit gate, typed quota/time checkpointing, replay policy inheritance, and tests.
- Phase 3 commit: overnight profile, dynamic 4-to-6 role scheduling, strategy fingerprints, graceful stop, secondary verification, and tests.

Each phase must pass its focused tests before commit. Pre-existing uncommitted changes must remain identifiable after each commit. Historical replay trees and `projects/main` must match their baseline aggregate hashes at final verification.
