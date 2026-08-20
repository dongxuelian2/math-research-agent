# F-004 — Dispatched Unknown-Execution Repair Report

## Finding and cause

When `FaultPoint.AFTER_PROVIDER_RESULT` interrupted dispatch, the provider
could have accepted the request while no result manifest or acknowledgement was
durable. The old reconciler orphaned the attempt but left the outbox in
`DISPATCHED` indefinitely.

## Repair

The reconciler now treats `DISPATCHED` plus an orphaned/unknown attempt and no
durable result as an explicit unknown-execution state. The attempt becomes
`UNKNOWN_EXECUTION`; the outbox moves to `DEAD_LETTER`; reconciliation journals
`UNKNOWN_EXECUTION` and `MANUAL_REVIEW_REQUIRED`. The policy is intentionally
manual review here because the provider payload is not durably available to
safe retry/adoption. No semantic domain effect is inferred.

This preserves at-least-once external execution semantics: a later retry, if
authorized by a future operator policy, must be a new Attempt while A1 remains
unknown provenance. Reconciliation is idempotent and cannot strand the logical
job in `DISPATCHED`.

## Proof

- The frozen fault-injection scenario is rerun by the repair probe without
  changing the frozen runner.
- X12 reports `attempt=UNKNOWN_EXECUTION; outbox=DEAD_LETTER; actions=3`.
- Durable runtime tests continue to cover pre-dispatch crash, artifact/result
  split, lease orphaning, and deterministic recovered final states.

## Status

`F_004_DISPATCHED_UNKNOWN_RECOVERY = CLOSED`
