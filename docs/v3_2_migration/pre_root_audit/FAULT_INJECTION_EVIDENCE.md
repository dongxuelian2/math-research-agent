# Fault-Injection Evidence

## Executed scenarios

All new scenarios were run by:

```text
uv run --project openprover python docs/v3_2_migration/pre_root_audit/run_cross_plane_probes.py
```

The runner creates temporary SQLite/WAL projects and deletes no repository
data. Its observed states were:

| Scenario | Injected boundary | Observed durable state | Expected | Result |
|---|---|---|---|---|
| X1 | late result carrying C1 while the current claim is conceptually C2 | accepted result id populated; runtime claim hash remains C1; result authoritative | retain artifact but require current-claim revalidation before authority | `PARTIAL`: runtime has no comparison |
| X2 | old SessionClosure after O1 was transferred/superseded in a newer map | closure v1; current map v3 says O1 `SUPERSEDED`; replay returns `RESOLUTION_ACCEPTED` and writes v4 with O1 `RESOLVED` | reject/revalidate/explicit transfer | `FAIL` |
| GOV-THESIS-BYPASS | direct `revise_map(... strategic_thesis=...)` with `HUMAN_STEERING` and no authorization | map v2 created | require authorized ArchitecturePatch | `FAIL` |
| X4 | lease expiry manually set before result ingestion | `INGESTED`, `authoritative=True`, attempt `RESULT_RECORDED` | stale artifact retained but fenced | `FAIL` |
| AFTER_PROVIDER_RESULT | dispatcher fault immediately after provider returned, before artifact write | before reconcile: `RUNNING` + `DISPATCHED`, zero manifests; after expiry/reconcile: `ORPHANED` + `DISPATCHED`, one `MARK_ORPHANED` attempt action, no outbox action | classify unknown execution and redispatch/adopt/manual review | `FAIL` |
| GOV-SAME-MODEL-FALLBACK | reviewer and critic share provider/model but have different actors/context | `same_model=True`, `policy_satisfied=True` | explicit `policy_satisfied=False` | `FAIL` |

## Existing fault evidence independently rerun

The full suite re-executed these frozen cases:

- `AFTER_INTENT_COMMIT`: provider was not called; PENDING outbox remains
  redispatchable (`test_durable_runtime.py::test_d4...`).
- `AFTER_ARTIFACT_WRITE`: filesystem artifact/manifest survives and is
  registered by reconciliation (`test_durable_runtime.py::test_d19...`).
- `AFTER_DOMAIN_APPLY_BEFORE_ACK`: domain receipt is recovered and the apply
  callback is not repeated (`test_durable_runtime.py::test_d19...`).
- `BEFORE_RESULT_DB_COMMIT`: artifact manifest is ingested on reconciliation
  and normal/crash final states are equivalent (`test_durable_runtime.py::test_d25...`).

## Fault points not covered by the frozen tests

`BEFORE_DISPATCH`, `BEFORE_EFFECT_SLOT_COMMIT`, and
`AFTER_EFFECT_SLOT_BEFORE_DOMAIN_APPLY` were not present in the existing test
calls. The new provider-result probe covers the previously untested
`AFTER_PROVIDER_RESULT` boundary. This matters because the reconciler handles
`CLAIMED`, `PENDING`, and `FAILED_RETRYABLE` outbox states but has no
`DISPATCHED` unknown-execution branch (`runtime_reconciler.py:50-99`).

## Interpretation

The modeled happy-path split cases are recoverable. The unmodeled provider
unknown-execution state and expired-but-not-yet-reconciled lease are not
fail-closed. The audit therefore does not certify crash safety across the full
provider-to-semantic-effect window.
