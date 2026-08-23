# ResearchMap Report

## Result

`RESEARCH_MAP = PASS`

PHASE 4 introduces a strict, filesystem-backed `ResearchStoreFacade` with
immutable `ResearchMap` versions. A map is a revisable research interpretation,
not theorem authority. Its schema contains no theorem-promotion field and its
write path never calls a Truth Plane mutation API.

## Identity and storage

- Every map binds `root_theorem_id` and an exact
  `root_claim_snapshot_hash`.
- Every version has a monotonically increasing integer version, parent map
  hash, typed revision reason, full obligation projection, scope changes,
  route-memory changes, evidence references, and a domain-separated hash.
- Versions are stored append-only below
  `research/maps/<map-id>/versions/`; `current.json` is explicitly a rebuildable
  projection rather than authority.
- Unknown schema versions, object types, fields, and hash mismatches fail
  closed.

## No-scope-loss invariant

`ResearchStoreFacade.revise_map` compares the complete prior and proposed
obligation-id sets. Any missing prior id raises `NO_SCOPE_LOSS`. Disposed scope
remains enumerated with `RESOLVED`, `SUPERSEDED`, or
`ABANDONED_WITH_REASON`; it is not deleted from later map versions.

The multi-obligation production-facade E2E starts with O1/O2/O3, executes only
O1, and produces v2 with O1 `RESOLVED` and O2/O3 still `OPEN`.

## Root binding and rebase

Semantic ClaimSnapshot changes block map revision, Directive/session creation,
and evidence acceptance with `RESEARCH_MAP_ROOT_STALE` /
`REVALIDATION_REQUIRED`. The explicit rebase API records old/new snapshots,
the PHASE 3 comparison result, and a complete partition of carried,
revalidation-required, and invalid obligations. It creates new obligation
semantic revisions and a new map version; it never edits history in place.

The theorem lifecycle status alone remains orthogonal research metadata: a
status-only `TARGET_STATUS_CHANGED` comparison may continue the same exact
assertion/dependency/assumption/authority binding. The orchestrator records an
explicit rebase when establishing a session across its initial lifecycle
transition.

## Durability boundary

The implementation provides atomic per-file replacement and immutable bodies
for a single process/filesystem. It does not claim SQLite transactions,
cross-process compare-and-swap, outbox publication, leases, or recovery.

## Evidence

- R1, R2, R8, R9, R12, R16 in
  `test_research_map_and_obligations.py`.
- R20 in `test_phase4_research_plane_e2e.py`.
- PHASE 4 focused suite: `21 passed in 4.72s` after the final formatter pass.
