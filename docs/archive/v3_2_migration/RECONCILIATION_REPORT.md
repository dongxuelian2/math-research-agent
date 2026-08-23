# Reconciliation Report

## Deterministic scope

Reconciliation runs at production runtime startup and through the `reconcile`
CLI. It checks expired attempt leases, stale/pending outbox records, filesystem
artifact manifests missing DB registration, registered results with missing or
corrupt bodies, result artifacts not yet ingested, and jobs awaiting accepted
result selection.

Actions use the frozen vocabulary:

`NO_ACTION`, `REDISPATCH`, `MARK_ORPHANED`, `INGEST_EXISTING_RESULT`,
`RETRY_NEW_ATTEMPT`, `WAIT`, `BLOCK_MISSING_ARTIFACT`,
`REPAIR_PROJECTION`, and `MANUAL_REVIEW_REQUIRED`.

Unknown state never means “retry everything.”

## Artifact/DB saga

Runtime artifacts use temp write, file fsync, atomic replace, best-effort
directory fsync, then a durable manifest. SQLite registration follows. If the
DB write fails, the manifest lets restart validate and register the orphan
without deleting evidence. If DB references a missing/corrupt body, result
authority is removed, the job becomes BLOCKED when necessary, and semantic
effect application fails closed.

When a provider artifact exists while an attempt is RUNNING/ORPHANED,
reconciliation verifies the digest, records the result idempotently, and
selects it before considering a new provider call.

## Checkpoint authority

Orchestrator resume opens/migrates SQLite and reconciles before Truth,
ResearchMap, and governance validation. `state.json`, campaign pipeline
snapshots, and routing JSON remain portable/compatibility projections; they do
not overwrite SQLite attempts, leases, outbox, accepted results, or effects.

## Evidence

D4, D6, D7, D12, D19–D22, and D25 cover pending dispatch, orphan adoption,
missing-body blocking, restart preservation, legacy adoption, isolation, and
crash/no-crash equivalence.
