# Pre-Root Repair Regression Evidence

## Rerun command

```text
uv run --project openprover --extra test python docs/v3_2_migration/pre_root_repair/run_pre_root_repair_probes.py --output docs/v3_2_migration/pre_root_repair/REPAIR_PROBE_RESULTS.json
```

The runner is separate from and does not modify the frozen
`pre_root_audit/run_cross_plane_probes.py`. Its focused regression slice
completed with `104 passed` and one pre-existing pytest cache permission
warning. The complete JSON record is
[`REPAIR_PROBE_RESULTS.json`](REPAIR_PROBE_RESULTS.json).

## X1–X16 rerun

| Probe | Result | Evidence / limitation |
|---|---|---|
| X1 | `CERTIFIED` | C1 artifact retained; acceptance fenced it as `STALE_FENCED`, no EffectSlot. |
| X2 | `CERTIFIED` | Late closure returns `STALE_SESSION_CLOSURE`; explicit transfer is separately revalidated. |
| X3 | `CERTIFIED_WITH_LIMITATION` | Canonical body/hash promotion remains domain-owned; runtime retains provenance and the promotion guard revalidates. |
| X4 | `CERTIFIED` | Expired result is retained but `authoritative=false`. |
| X5 | `CERTIFIED` | Production SessionClosure/StructuralEffect slots and identity recovery are tested. |
| X6 | `CERTIFIED` | Production governance session/effect path is wrapped; review-clock semantics remain GovernanceController-owned. |
| X7 | `CERTIFIED` | Authorized patch identity is exact; direct patch invocation is an explicit governance operation. |
| X8 | `CERTIFIED` | Production TruthMutation slot recovers the exact receipt. |
| X9 | `CERTIFIED` | ResearchMap resolution and route-failure effects use durable source identity/recovery. |
| X10 | `CERTIFIED_WITH_LIMITATION` | Scope-loss/transfer tests pass; no OS crash was injected inside filesystem patch application. |
| X11 | `CERTIFIED_WITH_LIMITATION` | LogicalJob/effect winner is deterministic; provider delivery remains at-least-once. |
| X12 | `CERTIFIED` | `AFTER_PROVIDER_RESULT` yields `UNKNOWN_EXECUTION` and `DEAD_LETTER`, with manual-review journal. |
| X13 | `CERTIFIED_WITH_LIMITATION` | Cancellation/stale-result races pass; no exactly-once claim is made for external delivery. |
| X14 | `CERTIFIED_WITH_LIMITATION` | Due state survives restart/checkpoint; no crash injected between governance artifact and clock writes. |
| X15 | `CERTIFIED_WITH_LIMITATION` | Legacy import preserves no fabricated runtime history; missing bindings require revalidation. |
| X16 | `CERTIFIED` | Project-local databases remain isolated for colliding local IDs. |

No X1–X16 row is `PARTIAL` or `FAILED`. The direct blocking probes X1, X2,
X4, X12, GOV-THESIS-BYPASS, and GOV-SAME-MODEL-FALLBACK are all
`CERTIFIED`.

## Focused B1–B16 coverage

| Regression | Coverage |
|---|---|
| B1/B2 | `test_f001_stale_closure_isolated_and_explicit_transfer_revalidates` |
| B3/B4 | `test_f002_expired_result_is_retained_but_cannot_be_accepted` |
| B5/B6 | `test_production_semantic_effects_use_effect_slots_and_exact_bindings` |
| B7 | `test_f007_stale_binding_is_fenced_at_acceptance` |
| B8/B9/B10 | Same production EffectSlot test; crash recovery is `test_f003_domain_apply_before_ack_reconciles_by_effect_identity`. |
| B11/B12 | X12 fault probe plus durable reconciliation tests. |
| B13/B14 | GOV-THESIS-BYPASS plus governed reframe tests. |
| B15/B16 | Same-model receipt and destructive authorization negative tests. |

## Fault and stale matrix

- `AFTER_PROVIDER_RESULT`: `UNKNOWN_EXECUTION` + `DEAD_LETTER` + manual review.
- `AFTER_DOMAIN_APPLY_BEFORE_ACK`: one existing ResearchMap domain identity is
  recovered; no second map revision.
- Expired lease late result: artifact retained, `STALE_FENCED`, no winner/effect.
- C1 result after C2: artifact retained, stale semantic authority denied.
- v1 closure after v3 reframe: stale closure denied; only typed fresh transfer
  can resolve a target obligation.
- Same-model governance object: receipt policy false; destructive authorization
  cannot proceed.

## Quality and authority boundary

This is implementer evidence, not independent audit authorization. The repair
state is:

```text
PRE_ROOT_BLOCKERS_REPAIRED = YES
READY_FOR_INDEPENDENT_REAUDIT = YES
ROOT_SYNTHESIS_FULL = NOT_STARTED
FINAL_CONSOLIDATION_FULL = NOT_STARTED
PHASE_7_IMPLEMENTATION_STARTED = NO
PHASE_7_AUTHORIZED = NOT_GRANTED
```
