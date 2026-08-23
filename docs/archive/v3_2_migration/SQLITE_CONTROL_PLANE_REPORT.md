# SQLite/WAL Control Plane Report

## Decision

Phase 6 establishes `runtime/control.sqlite3` inside each project as the sole
current authority for external-execution control. The database owns LogicalJob,
AttemptIntent, attempt state, outbox, leases, result acceptance, EffectSlot, and
reconciliation projections. It does not own theorem truth, ResearchMap bodies,
governance artifacts, provider output bodies, or large logs.

`RuntimeBackend` in `runtime_model.py` is the semantic seam. The first production
implementation is `SQLiteRuntimeBackend`; production orchestrator,
formalization, certification, and provider-smoke calls reach providers through
`DurableProviderDispatcher`.

## Durability settings

Every connection enables:

- `journal_mode=WAL`;
- `foreign_keys=ON`;
- a 5000 ms default busy timeout;
- production default `synchronous=FULL` (accepted alternatives are only
  `FULL`, `NORMAL`, and `EXTRA`; `OFF` is rejected).

`runtime-check` verifies schema version, WAL, foreign keys, synchronous mode,
integrity, and object counts. `reconcile` runs deterministic recovery and emits
typed actions.

## Schema and migration

Schema 2 contains current projections for jobs, attempts, outbox, artifact
registry, results, effect slots, and reconciliation, plus the append-only
transition journal. Artifact bodies are never stored as BLOBs. Schema 1→2 is a
transactional forward migration that adds a migration history table; D1 creates
an actual schema-1 database and proves forward upgrade without dropping data.

Legacy checkpoint adoption writes one `MIGRATED_FROM_LEGACY_CHECKPOINT` record.
It deliberately creates no fictional job, attempt, lease, outbox, or journal
history.

## Isolation and concurrency

Database location is project-root-relative and is rejected if configured
outside that root. Identical local IDs in two project roots produce isolated
rows and leases. Critical writes use `BEGIN IMMEDIATE`, unique constraints,
version fields, expected states, generations, and conditional updates.

## Evidence

- D1 fresh schema/WAL/integrity/control-only and schema-1→2 migration: PASS.
- D10 concurrent lease CAS: PASS.
- D21 legacy adoption without fake history: PASS.
- D22 project isolation: PASS.
- Full local suite including the formerly blocked interrupt test: 268 passed.
