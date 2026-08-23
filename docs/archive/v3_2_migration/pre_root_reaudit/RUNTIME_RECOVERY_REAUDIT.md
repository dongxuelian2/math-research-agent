# Runtime Recovery Re-Audit

## Positive recovery results

- The `AFTER_PROVIDER_RESULT` fault window is classified as
  `UNKNOWN_EXECUTION`; the outbox leaves `DISPATCHED` for `DEAD_LETTER` and a
  `MANUAL_REVIEW_REQUIRED` reconciliation action.  X12 is `CERTIFIED`.
- The exact expiry-only late result is retained as an artifact/result record,
  but is `STALE_FENCED`, non-authoritative, and cannot prepare an EffectSlot.
- Domain-apply-before-ACK recovery recognizes stable domain identity and ACKs
  the existing slot.  Research SessionClosure and TruthMutation receipt
  recovery are covered by focused tests and the 275-test local suite.
- Reconciliation is idempotent for the unknown-execution fault; it does not
  automatically redispatch the same physical attempt.

## Negative recovery result

`RuntimeReconciler._accept_pending_results` loops over jobs and calls
`accept_result(job_id, actor="reconciler")` without `binding_validator`.
`ResearchOrchestrator` invokes `runtime_backend.reconcile()` during startup
before current Research/Governance state is loaded.  The independent
`RX-RESTART-STALE-DOMAIN` probe persisted an authoritative C1/v1 result,
advanced the project to a new claim snapshot and map version, restarted the
backend, and observed the stale result selected as `accepted_result_id`.

This is a recovery authority gap, not a missing SQLite column: the binding is
persisted.  Recovery simply does not compare it to current domain identity.
The result is NF-002 and keeps F-007/X1 open.

## Late-result matrix after unknown execution

The durable record path still applies lease, token, generation, and binding
checks to a result that arrives after the unknown-execution classification.
There is no special unknown-execution bypass.  The remaining issue is the
separate consumer/recovery selection path described above.
