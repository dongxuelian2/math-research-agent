# Final Unattended Hardening Candidate Report

## Disposition

```text
LOCAL_ENGINEERING_CLOSURE = YES
READY_FOR_FINAL_INDEPENDENT_CERTIFICATION = YES
NF-003_REPAIRED = YES
NF-004_REPAIRED = YES
F-007_REPAIR_CANDIDATE = CLOSED
F-002 = CLOSED
F-005 = CLOSED
PRE_ROOT_SYNTHESIS_CERTIFIED = NO
PHASE_7_FORMALLY_AUTHORIZED = NO
FINAL_SYSTEM_CERTIFIED = NO
PUSHED = NO
HOSTED_CI = PENDING_PUSH
POSIX_INTERRUPT_HOST_RUN = NOT_EXECUTED
```

`CLOSED` above is a local repair-candidate disposition for F-007 and preserves
the historical audit record's prior `OPEN`/denied-reauthorization facts. It is
not a formal certification.

## Repository and commit boundary

```text
REPOSITORY = math-research-agent-integration
BRANCH = codex/v3-2-reconciliation
HARDENING_STARTING_HEAD = 759070a40b234987c9b08033997e525c894dc600
FINAL_HEAD = final local handoff commit containing this report; verify with git rev-parse HEAD
REMOTE_PUSH = NONE
```

The working tree was clean at the hardening start. No destructive Git command,
force push, history rewrite, or user-change overwrite was used.

## Production repairs

### NF-003 — current-domain binding completeness

`ResearchOrchestrator._validate_execution_binding()` now fails closed when the
current ResearchMap or any current obligation/directive/session dimension is
omitted. The normal semantic route validates this before call creation. The
map-scoped governance effects and truth-only root mutation use explicit narrow
validators so their intentional scope is visible and cannot turn a root-only
normal semantic binding into a wildcard.

### NF-004 — no-backend semantic guard

`RoutedLLMClient._execute_route()` now enforces `require_execution_binding` and
invokes the binding validator before the runtime-backend presence branch and
before provider acquisition. A no-backend semantic route with no trusted
binding fails closed.

## Probe and regression disposition

| Probe / gate | Result | Evidence |
|---|---|---|
| `F005-A1-A14` | PASS | All forged authority representations rejected without map mutation |
| `F007-RESTART-CONTROLS` | PASS | Missing-validator, stale-validator, and exact-valid controls pass |
| `NF-003-PARTIAL-BINDING` | PASS | Root-only binding rejected; no accepted semantic result |
| `NF-004-NO-BACKEND-GUARD` | PASS | No-backend semantic route rejected before response |
| `F002-TERMINAL-REJECTION` | PASS | Late result remains terminal/non-consumable |
| X1 repair runner | PASS | Runner label `CERTIFIED`; final NF-003 variant also PASS |
| X7 repair runner | PASS | Runner label `CERTIFIED`; complete/partial/no-backend variants pass |
| C1-C10 local composition | PASS | Former NF-003/NF-004 fail rows are repaired; underlying component probes and full suite pass |
| Phase 7 focused slice | PASS | 20 tests passed |
| Full local suite | PASS | 289 tests passed |
| Ruff lint | PASS | `ruff check openprover` |
| Ruff format | PASS | Global check: 144 files already formatted |
| Lockfile | PASS | 49 packages resolved by `uv lock --check` |
| Python compileall | PASS | Production and final adversarial probe sources |
| Git whitespace | PASS | `git diff --check` |
| Windows interrupt race | PASS | 3 tests passed |
| Bash syntax | PASS | 7 scripts parsed |
| PowerShell syntax | PASS | 2 scripts parsed |
| Hosted CI | PENDING | No push in this turn |
| POSIX interrupt host | NOT EXECUTED | WSL/Docker unavailable |
| Independent final audit | NOT RUN | Required before formal certification |

## C1-C10 candidate matrix

These are local candidate dispositions derived from the named adversarial
probes plus the existing F-002/F-005/runtime regression coverage. They are not
independent certification results.

| ID | Candidate result | Basis |
|---|---|---|
| C1 | PASS | F-005 forgery rejection + NF-003 completeness rejection |
| C2 | PASS | F-005 forgery rejection + NF-004 no-backend guard |
| C3 | PASS | F-002 terminal result + F-005 forgery matrix |
| C4 | PASS | F-002 terminal path + NF-004 guard |
| C5 | PASS | Complete stale binding and authority cloning controls |
| C6 | PASS | Provider-result recovery and F-002 terminality |
| C7 | PASS | Restart/late-result controls + NF-003 completeness |
| C8 | PASS | Wrong-thesis/cross-map authority rejection |
| C9 | PASS | Valid governance effect path and rejected-source fencing |
| C10 | PASS | Complete and current-generation binding validation |

## Certification boundary

Local engineering is closed for the targeted defects and Phase 7 lifecycle.
The next authorized step is an independent final audit, followed—if approved
by the owner—by push, hosted Linux/Windows CI, and a real POSIX interruption
run. Until those gates are attached, all three formal certification flags stay
`NO`.
