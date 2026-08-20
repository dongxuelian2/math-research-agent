# F-001 — Stale SessionClosure Repair Report

## Finding and cause

The old path could load a v1 `SessionClosure` after a governed reframe had
superseded O1 and transferred scope to O2. It then resolved the historical O1
against a newer map. The missing guard was a comparison of the closure's
immutable map/root identity and the current obligation disposition before the
Evidence→Obligation gate.

## Repair

`ResearchStoreFacade.evaluate_session_closure` now requires exact
`research_map_id`, version, map hash, root ClaimSnapshot hash, obligation,
directive, and tactical-session identity. A mismatch returns
`STALE_SESSION_CLOSURE`. The evidence gate rejects `SUPERSEDED`, `RESOLVED`,
`ABANDONED_WITH_REASON`, incompatible `BLOCKED`, and missing obligations.

The only supported old-scope path is explicit typed transfer:
`ScopeTransfer` plus an `AUTHORIZED` `PatchAuthorization`, exact source/root
match, target-scope coverage, freshly reprojected evidence, and a new target
obligation hash. The old closure is never silently relabeled as O2 evidence.
An accepted transfer records one target resolution with a transfer basis;
replaying the old closure remains stale.

## Proof

- `test_f001_stale_closure_isolated_and_explicit_transfer_revalidates`
  exercises v1 O1 → v3 O1 `SUPERSEDED` → O2, confirms no O1 resolution and no
  unintended v4, then exercises the explicit authorized transfer and replays
  the stale closure twice.
- The repaired X2 probe reports `STALE_SESSION_CLOSURE` and no current map
  revision from the late closure.
- The production research E2E still reaches one accepted closure and one
  ResearchMap revision for a current, compatible session.

## Status

`F_001_STALE_SESSION_CLOSURE = CLOSED`

The stale closure is retained as immutable evidence and is blocked from
current semantic authority unless independently revalidated.
