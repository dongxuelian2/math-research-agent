# No-Scope-Loss Certification

## Verdict

`NO_SCOPE_LOSS_ON_DIRECT_MAP_REVISION = CERTIFIED_WITH_LIMITATION`

`AUTHORIZED_SCOPE_TRANSFER = CERTIFIED_WITH_LIMITATION`

`NO_SCOPE_LOSS_UNDER_LATE_SESSION_REPLAY = FAILED`

`NO_SCOPE_LOSS_CERTIFIED = NO`

## Positive evidence

`ResearchStoreFacade.revise_map` rejects omitted prior obligation IDs with
`NO_SCOPE_LOSS` (`research_store.py:521-530`). The deterministic Research
tests cover a multi-obligation omission and explicit supersession. Governance
tests cover a four-ID transfer (`O1`, `O2` → `N1`, `N2`) with complete
`ScopeTransfer` records; the target contains all four obligations and marks
the sources `SUPERSEDED`.

The typed `ArchitecturePatch` path also checks patch source map/root identity,
complete transfer, and authorization before applying a destructive reframe
(`research_store.py:600-722`).

## Adversarial failure

The new X2 probe constructs:

```text
v1: O1 OPEN; S1/SessionClosure is bound to v1
v2: O2 added as replacement
v3: O1 SUPERSEDED -> O2
late S1 closure replay
```

Observed:

```text
closure.research_map_version = 1
current map before replay = v3, O1 = SUPERSEDED
resolve_session_closure() = RESOLUTION_ACCEPTED
current map after replay = v4, O1 = RESOLVED, O2 = OPEN
```

The closure’s `research_map_version` and `research_map_hash` are persisted in
the SessionClosure (`research_evidence.py:268-318`) but are not compared in
`evaluate_session_closure`/`can_resolve_obligation`; the gate checks the root
ClaimSnapshot and obligation hash only (`research_store.py:364-374`,
`research_evidence.py:421-506`). The current disposition is also not required
to be OPEN/BLOCKED before resolution.

This is a semantic scope-transfer violation: old evidence reactivates a
superseded obligation in a newer map. It is not repaired by the direct
no-omission invariant because the stale replay mutates a still-present ID to
the wrong current disposition.

## Required repair frontier

Before Phase 7, require exact closure-to-current-map version/hash compatibility
or an explicit typed transfer/revalidation path. Reject stale closures before
`record_disposition`, and reject `SUPERSEDED`/`ABANDONED_WITH_REASON` as
resolution targets unless a new authorized evidence path explicitly reopens or
replaces the obligation.
