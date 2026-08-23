# Interruption Runtime Report

## Cross-platform process control

`ProcessTerminationBackend` is now the shared process-tree abstraction.

- Windows creates a new process group without a visible window, uses
  `taskkill /PID … /T /F`, then bounded terminate/kill fallback.
- POSIX creates a new session, signals the process group, then uses the same
  bounded fallback.

The invariant is that the process is no longer running and blocked readers
unblock. Windows is not incorrectly required to report a negative Unix signal
return code.

CodexCLIClient delegates creation and termination to this backend. A routed
interrupt first records durable `CANCEL_REQUESTED` for active attempts and then
terminates provider processes. Provider completion and cancellation reconcile
to one terminal state.

## Test repair

`test_interrupt_race.py` now:

- uses `sys.executable`;
- uses the shared platform creation/termination API;
- no longer executes tests during import;
- preserves the multi-worker soft-interrupt race invariant;
- performs a real process-tree interruption on the current Windows host;
- unit-executes the POSIX process-group branch.

No platform skip was added.

## Evidence

- `tests/test_interrupt_race.py`: 3 passed on Windows.
- interrupt plus Codex CLI provider slice: 22 passed before the POSIX branch was
  added; the final repository suite including all three interrupt tests is
  268 passed.
- `TEST_INTERRUPT_RACE = PASS`; the previous `ENVIRONMENT_BLOCKED` status is
  retired.
