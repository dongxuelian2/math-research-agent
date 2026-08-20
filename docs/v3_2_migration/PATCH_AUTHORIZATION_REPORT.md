# PHASE 5 Patch Authorization Report

## Patch and classifier

`ArchitecturePatch` is a typed proposal bound to one root and source map.
Operation kinds deterministically classify the proposal as
`LOCAL_ADJUSTMENT` or `DESTRUCTIVE_PATCH`. Partition/parameterization changes,
family merge/split, major-route abandonment, strategic-thesis changes,
root-scope removal, and termination-architecture changes are destructive.

Each destructive patch names every affected obligation and carries explicit
`ScopeTransfer` records. A transfer maps old scope to retained/new obligations,
or records `RESOLVED`/`ABANDONED_WITH_REASON` with evidence and reason. Coverage
is complete only when transfer sources exactly equal affected obligations.

## Authorization gates

`GovernanceController.authorize_patch()` revalidates the current ClaimSnapshot,
source map version/hash, invalidated governance evidence, complete scope
transfer, exact independent critic approval, and all required supporting probe
results. Its durable status is `AUTHORIZED`, `REJECTED`,
`REVALIDATION_REQUIRED`, or `STALE`.

The generic `ResearchStoreFacade.revise_map()` rejects destructive/reframed
scope and `ARCHITECTURE_PATCH` revisions without a typed authorized receipt.
`apply_authorized_patch()` creates exactly one immutable next map version,
retains every old obligation with an explicit disposition, adds new
obligations, and writes an immutable `ArchitecturePatchApplication` containing
the source/target hashes plus review, probes, critic, authorization, and scope
transfer hashes. Old map versions and Truth Plane state remain unchanged.

## Known storage boundary

All artifacts use typed JSON, immutable writes, and atomic projections with
single-process semantics. Invalid evidence is supplied explicitly to the
authorization boundary; a cross-process invalidation journal belongs to the
unstarted PHASE 6 runtime.
