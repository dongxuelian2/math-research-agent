# Test and Validation Matrix

| Check | Result | Evidence / boundary |
|---|---|---|
| NF-003 partial current-domain binding | PASS | Final adversarial probe rejects root-only binding; provider is not reached in the new regression |
| NF-004 no-backend semantic binding guard | PASS | Final adversarial probe rejects before provider; new regression confirms zero provider calls |
| F-007 complete/restart binding controls | PASS | `F007-RESTART-CONTROLS` plus focused router/runtime tests |
| F-002 terminal rejection | PASS | `F002-TERMINAL-REJECTION`; full local suite remains green |
| F-005 authority forgery matrix | PASS | `F005-A1-A14`; no map mutation |
| X1 candidate rerun | PASS | Repair runner reports `CERTIFIED`; NF-003 partial variant also PASS; no formal certification inferred |
| X7 candidate rerun | PASS | Repair runner reports `CERTIFIED`; complete/partial/no-backend variants pass; no formal certification inferred |
| Phase 7 focused/recovery slice | PASS | `20 passed` across authority, blocker, and Phase 7 implementation tests |
| R19 production research-plane integration | PASS | Existing R19 test passed with Phase 7 artifacts enabled |
| Full local suite | PASS | `289 passed` with pytest cache provider disabled for deterministic local execution |
| Ruff lint | PASS | `uv run --project openprover --extra dev ruff check openprover` passed |
| Ruff global format gate | PASS | Global format run changed 11 files; final check reports `144 files already formatted` |
| Python compilation | PASS | Production and final adversarial probe sources compiled |
| `uv lock --check` | PASS | `uv lock --check --project openprover` passed; 49 packages resolved |
| `git diff --check` | PASS | No whitespace errors |
| Windows durable restart recovery | PASS | Focused test resumes a completed run and separately recovers from `TRUTH_PROMOTED`; no certification claim |
| Windows interrupt race | PASS | `3 passed` in `test_interrupt_race.py` |
| Bash syntax | PASS | 7 scripts parsed with `bash -n` |
| PowerShell parse | PASS | 2 scripts parsed by PowerShell AST parser |
| Hosted CI | PENDING | No push or workflow dispatch was authorized in this turn |
| POSIX interruption/certification | NOT EXECUTED | WSL and Docker are unavailable; MSYS Bash syntax is not a POSIX process-group run |
| Final independent audit | NOT RUN | Required before formal certification |
| Push | NO | Local commits only |

The single pytest warning is the existing Windows permission warning for the
repository `.pytest_cache` projection. It did not affect test outcomes.

The local candidate gates are green for NF-003/NF-004/F-007/F-002/F-005. Hosted
Linux CI, a real POSIX interruption host, and an independent final audit remain
external gates; formal certification flags therefore remain `NO`.
