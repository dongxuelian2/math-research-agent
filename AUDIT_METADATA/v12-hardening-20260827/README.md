# MRR v1.2 runtime-hardening audit (2026-08-27)

## Scope and baseline

- Repository: `dongxuelian2/math-research-agent`
- Frozen starting HEAD: `b1dc2cb817511613869fe46a88a1422f13b98b6b`
- Branch: `main`
- Starting dirty state: only the pre-existing untracked `AUDIT_METADATA/critical-layer-proof-as-test/` and `AUDIT_METADATA/lunamax-short-ab-test/` evidence directories.
- Baseline: `pnpm run test:proof` passed 84/84 before implementation.
- The mathematical corpus and the original Gemini/Luna forensic evidence were not modified.

## Engineering result

1. The bootstrap protocol now has one canonical, versioned, provider-compatible JSON Schema (`mrr-bootstrap-v2`). Prompt instructions, OpenAI/Codex `--output-schema`, OpenAI-compatible `response_format`, Google `responseJsonSchema`, strict parser semantics, and TypeScript expectations agree.
2. `dependencyHints` and structural fields are unambiguous. Dependency `confidence` is the `EXPLICIT|INFERRED` enum; the separate `confidenceScore` is `number|null`. CASE_SPLIT, REDUCTION, and FAILED_ROUTE have strict semantic validation.
3. Parse/schema failures fail closed and preserve exact raw model output plus session/provider/model metadata. The failure taxonomy is durable per range.
4. Bootstrap is incremental. Stable run/range identity includes project, config revision, corpus manifest/source hashes, selection, and schema version. Completed work is skipped, dead RUNNING work is reclaimed, and changed identities become STALE. All extracted history remains provisional.
5. Codex receives prompt input on stdin and the response schema through a temporary schema file. Long input no longer appears in argv. Abort, stdout/stderr, exit status, schema cleanup, and explicit reasoning effort are retained.
6. Project mutations use a process-wide per-project queue shared by all `ResearchStore` instances. State writes use a unique `wx` temp file, flush/close, rename commit boundary, and eight bounded retries for Windows EPERM/EBUSY/EACCES. There is no delete-then-rename window.
7. Proof attempts/tasks and bootstrap ranges carry executor/process ownership. A restarted runtime reclaims dead-owner RUNNING work without replaying completed tasks. Canonical mathematical effects keep stable exactly-once identities even though provider execution is at-least-once.
8. Actual guarantees and limitations are documented in `docs/runtime-durability.md`.

## Deterministic verification

- `pnpm run typecheck`: PASS.
- `pnpm run build`: PASS (backend TypeScript and proof-workbench checks).
- `pnpm run test:proof`: PASS, 95/95 on the final tree.
- Focused hardening suite: PASS, including canonical schema, a real Luna numeric-confidence fixture, exact raw failure evidence, >100 KB Codex stdin, bootstrap interruption/stale identity, Windows replace fault injection, cross-store serialization, 200 sequential Windows writes, synthetic orphan recovery, and copied real Gemini cycle-5 recovery.
- `git diff --check`: PASS (only line-ending notices).
- Windows stress reproduced no unexplained EPERM. The original production EPERM itself was not deterministically reproduced; transient/persistent EPERM/EBUSY paths were fault-injected.
- Copied real Gemini starting state matched cycle 5, 6 plans, 36 tasks, 23 completed, 12 retryable, 1 stale running. The stale task was reclaimed and the provider path was invoked; completed work and accepted effects were retained.

## Real Luna iteration 1: provider-schema rejection (preserved failure)

- Project: `v12-luna-micro-20260827`
- Run: `bootstrap-run-a412daef8ea4a4be4592fdc1`
- Model: `gpt-5.6-luna`, reasoning `max`, production Codex provider.
- Selection: 20 representative ranges, including `reports/global/proved_results_report_v3.md:2689-2803`, the prior ENAMETOOLONG range.
- Result: 20/20 calls rejected before model execution because Codex structured output does not permit JSON-Schema `allOf`. All were typed `PROVIDER_FAILURE` and deterministically fell back. This failed iteration was frozen before the v2 schema fix.

## Real Luna iteration 2: 20-range v2 micro

- Project: `v12-luna-micro-v2-20260827`
- Run: `bootstrap-run-ca3122b4f1f0777fa794d744`
- Status: `COMPLETED`; all 20 range records and all merge/review/import stages completed.
- Clean parsed model outputs: 14/20.
- Genuine failures/fallbacks: 3 TIMEOUT, 2 PROVIDER_FAILURE (`tls handshake eof`), 1 STRUCTURED_OUTPUT_PARSE_FAILURE (trailing malformed output).
- Known contract-mismatch `SCHEMA_VALIDATION_FAILURE`: 0.
- ENAMETOOLONG/local spawn failures: 0. The former failing range reached the provider; it later had a genuine upstream timeout.
- Retained raw response bytes: 251,970 from successful calls. The final post-run fix additionally persists exact raw text on parse/schema failure.
- Recovered report: 429 proposals, 747 accepted dependency proposals, 43 historical routes, and 4 reconstructed coverage records. Imported structures remain provisional and the root remained open.
- Verdict: `PASS_WITH_WARNINGS`, because the canonical contract and long-input transport worked, but 6/20 calls required honest fallback.

## Real hard interruption and resume

- Project: `v12-luna-interrupt-v2-20260827`
- Run: `bootstrap-run-dd881cbe968d809d19ab9261`
- The proof API listener PID 5272 and its Codex child PID 6744 were force-stopped; the request received 502 and the listener disappeared.
- A safe target-verification retry introduced a race: the actual kill point on disk was 2 COMPLETED, 1 RUNNING, 1 PENDING (not the earlier 1/1/2 observation).
- After restart, the same bootstrap run ID was resumed. The first two attempt IDs, durations, parsed results, and completion timestamps were unchanged. The interrupted third range changed attempt ID from `bootstrap-attempt-291bdc05610429bfc8cdd79b` to `bootstrap-attempt-91d83938da05f992ec95d3cb`; the fourth range then ran normally.
- Final status: 4/4 COMPLETED and merge/review/import COMPLETED. No duplicate bootstrap report or provisional import was created.
- This run exposed stale per-attempt failure carryover on a successful retry. The final tree clears all previous attempt payload when reacquiring and has a deterministic regression assertion. The final small cleanup and stage-observability patches were made after this frozen run.

## Layer gates not executed

The full-corpus Luna bootstrap was not started. The v2 micro completed and demonstrated the fixed contract, but its 6/20 genuine failures plus a post-run raw-evidence fix warranted another micro iteration before spending roughly a day on 356 sequential Luna Max ranges. Consequently the 3-cycle real Agent campaign, real historical-reuse observation, literature capability probe, and optional computation probe were not executed.

## Readiness summary

- Component-level schema, structured-output, incremental checkpoint, stale-input, Codex long-input, Windows state replace, single-process single-writer, fault recovery, proof-orphan recovery, completed-task non-replay, exact-effect, and truth-authority gates are ready.
- Real hard-kill resume passed with the caveat that the final attempt-cleanup patch is covered deterministically rather than by a second hard kill.
- Full long-horizon bootstrap and end-to-end production readiness are not established because layers 6-9 were not run.
