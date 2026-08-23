# Hosted CI, Static, and POSIX Evidence

## Local gate results

| Command or gate | Result | Notes |
|---|---|---|
| `uv run --project openprover --extra test pytest -q` | `PASS` | 283 passed, 1 warning. |
| Repair focused runner | `PASS` | 104 passed, 1 warning; its aggregate remains non-certifying. |
| Targeted F-002/F-005/F-007 positives | `PASS` | Governance E2E and authorized reframe passed. |
| Blocker/regression slices | `PASS` | 26 and 24 tests passed in the recorded slices. |
| Windows interrupt race | `PASS` | Actual Windows subprocess/process-tree checks and multiworker race passed. |
| `uv run --project openprover --extra dev ruff check openprover` | `PASS` | All checks passed. |
| `uv run --project openprover --extra dev ruff format --check openprover` | `FAIL` | 11 files would be reformatted; 131 already formatted. |
| `python -m compileall -q openprover ...` | `PASS` | Production and audit probe sources compile. |
| `uv lock --check --project openprover` | `PASS` | Lock resolution check passed. |
| Bash syntax scan | `PASS` | 7 scripts, with inaccessible pytest-cache paths excluded. |
| PowerShell parse scan | `PASS` | 2 scripts, with inaccessible pytest-cache paths excluded. |
| `git diff --check` | `PASS` | No whitespace errors in the evidence changes. |

## Hosted CI

The checked-in workflow at `.github/workflows/ci.yml` runs the Linux quality
job with locked dependencies, Ruff format/check, the full suite, compileall,
and the deterministic showcase. It also runs a Windows bootstrap/launcher job.

```text
HOSTED_CI = PENDING
REASON = no push or workflow dispatch was authorized for this read-only audit
```

No hosted result is inferred from local Windows execution. No push was made.

## POSIX interruption

The Windows interrupt-race script exercised actual Windows subprocess behavior
and the unit/mock POSIX branch, but this host cannot certify a real POSIX
process group and signal run.

```text
POSIX_INTERRUPT_HOST_RUN = NOT EXECUTED
POSIX_GATE = REQUIRES_HOSTED_CI
```

## Required completion evidence

The next authorized reauthorization must attach the hosted workflow URL and
commit SHA, include the Linux quality result, include the Windows bootstrap
result, and run the interrupt scenario on a POSIX host. It must also repeat the
two failed adversarial probes after the F-007 repair.
