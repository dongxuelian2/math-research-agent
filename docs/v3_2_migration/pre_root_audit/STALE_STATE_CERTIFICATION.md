# Stale-State Certification

| Boundary | Evidence | Verdict |
|---|---|---|
| Truth ClaimSnapshot start/resume/audit/promotion | Truth facade and canonical-authority tests pass; `compare_and_transition` performs current snapshot comparison and CAS. | `CERTIFIED_WITH_LIMITATION` |
| Truth prepared mutation recovery | Intent/prepared evidence and receipt replay tests pass. | `CERTIFIED_WITH_LIMITATION` |
| Research root rebase | `test_r12_stale_root_blocks_revision_until_explicit_rebase` passes; map root validation rejects a changed theorem snapshot. | `CERTIFIED_WITH_LIMITATION` |
| Research map/session binding | X2 demonstrates that map version/hash are stored but not enforced during closure resolution. | `FAILED` |
| Runtime claim binding | X1 demonstrates a C1-bound result can be accepted without a runtime comparison to current C2; routed production jobs also omit claim/map/directive bindings (`routing.py:942-975`). | `PARTIAL` |
| Lease/generation fencing | token/generation CAS tests pass after reassignment, but X4 shows expiry alone does not fence the still-running lease. | `FAILED` |
| Canonical authority recovery | artifact body/hash and promotion guard tests pass; runtime result ingestion is not itself authority-aware. | `CERTIFIED_WITH_LIMITATION` |
| Governance review/patch staleness | stale root, invalidated evidence, exact patch source, and replay tests pass. | `CERTIFIED_WITH_LIMITATION` |
| Governance independence | same-model fallback is recorded `policy_satisfied=true`, contrary to the harness rule. | `FAILED` |

## Key source observations

`record_result` sets `fenced` from lease token, generation, and attempt state
(`runtime_backend.py:1207-1224`). It never checks whether the stored
`lease_expires_at` is in the past. Expiry is only consulted by heartbeat/lease
orphaning (`runtime_backend.py:829-834`, `:1053-1070`), leaving a window in
which a late result can be authoritative before reconciliation runs.

`can_resolve_obligation` validates the current root hash and exact obligation
semantic hash, but it has no map-version/hash condition and no disposition
state condition. That is the direct cause of X2.

## Overall stale-state verdict

`STALE_STATE_CERTIFIED = NO`. The Truth domain guards are useful, but stale
ResearchMap and expired-runtime-lease boundaries are both cross-plane
authority failures. Phase 7 must not start from this state.
