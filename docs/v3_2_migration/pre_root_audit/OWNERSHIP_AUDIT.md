# Ownership Audit

## Summary

The individual Phase 3--6 owners exist, but their production composition is
not ownership-consistent. SQLite owns most execution state, while live
semantic finalization still calls filesystem domain stores directly. The
EffectSlot coordinator is implemented and well-tested as an adapter seam, but
no production orchestrator path imports or invokes it.

## Truth Plane

| Question | Evidence | Verdict |
|---|---|---|
| Who can promote Truth? | `orchestrator.py:1760-1810` calls `TruthStoreFacade.compare_and_transition`; the facade performs snapshot/CAS, intent, prepared evidence, and receipt handling in `truth_store.py:242-406`. | `CERTIFIED_WITH_LIMITATION` |
| Direct production `PROVED` bypass? | Production promotion call is facade-owned. `cli.py`, `showcase_demo.py`, and public `ProjectStore` lifecycle primitives remain compatibility/demo surfaces; they are not the orchestrator promotion path. | `CERTIFIED_WITH_LIMITATION` |
| Runtime effect binding? | The final promotion call is not wrapped by `RuntimeEffectCoordinator.apply_truth_mutation`; the coordinator is referenced by tests and `runtime_effects.py`, not by the production orchestrator. | `PARTIAL` |

The facade’s local prepared-recovery and receipt tests pass, but the claim that
Truth promotion is a production EffectSlot saga is not supported by the live
call graph.

## Research Plane

| Question | Evidence | Verdict |
|---|---|---|
| Who creates/revises maps? | `ResearchStoreFacade.create_initial_map`, `revise_map`, `apply_governed_reframe`, and `record_disposition` are the typed domain APIs. | `CERTIFIED_WITH_LIMITATION` |
| Can planner/worker/router directly mutate scope? | No direct worker/planner/router call to `record_disposition` was found in the production route; they produce execution/evidence signals. | `CERTIFIED_WITH_LIMITATION` |
| Can a generic revision change the strategic thesis without authorization? | `research_store.py:501-510` only requires authorization for removed/reframed scope or `ARCHITECTURE_PATCH`; `strategic_thesis` is accepted at `:494` and written at `:567-568`. The new `GOV-THESIS-BYPASS` probe succeeds without a patch authorization. | `FAILED` |
| Does a late SessionClosure bind to its original map? | `resolve_session_closure` loads the current map and `can_resolve_obligation` checks obligation/root/evidence, but not `closure.research_map_version` or `closure.research_map_hash`. The X2 probe resolves an O1 that is already `SUPERSEDED` in v3. | `FAILED` |

## Architecture Governance

| Question | Evidence | Verdict |
|---|---|---|
| Who resets the review clock? | `GovernanceController.commit_review` is the only formal review reset (`governance.py:415-454`); exact replay is covered by focused tests. | `CERTIFIED_WITH_LIMITATION` |
| Who applies destructive patches? | `GovernanceController.apply_authorized_patch` validates authorization and exact target identity (`governance.py:712-766`); four-obligation transfer tests pass. | `CERTIFIED_WITH_LIMITATION` |
| Can a same-model critic be recorded as independent? | `ArchitectureCriticIndependenceReceipt.capture` defines satisfaction from actor/context/freshness only (`architecture_critic.py:64-105`). With same provider/model it returns `same_model=true` and `policy_satisfied=true`; the new probe and existing test assert that behavior. | `FAILED` |
| Is every live semantic governance effect slot-owned? | The orchestrator directly calls `record_effect` and `record_session` (`orchestrator.py:1719-1758`); it does not call the runtime effect coordinator. | `PARTIAL` |

## Durable Runtime

| Question | Evidence | Verdict |
|---|---|---|
| Who dispatches providers? | `DurableProviderDispatcher.execute` creates AttemptIntent/outbox before invoking a provider (`runtime_dispatch.py:39-135`). Production routers with a runtime backend use this path (`routing.py:932-975`). | `CERTIFIED_WITH_LIMITATION` |
| Who owns winner selection? | SQLite `accept_result` selects one authoritative successful result; D2/D9/D25 and the full suite pass. | `CERTIFIED_WITH_LIMITATION` |
| Does fencing include lease expiry? | `record_result` checks token, generation, and state (`runtime_backend.py:1217-1224`) but not `lease_expires_at`. X4 proves an expired generation-1 result is ingested as authoritative. | `FAILED` |
| Does reconciliation classify unknown DISPATCHED execution? | `runtime_reconciler.py:50-99` handles expired `CLAIMED`, `PENDING`, and `FAILED_RETRYABLE`, not `DISPATCHED`. The fault probe leaves `ORPHANED` attempt + `DISPATCHED` outbox with no outbox recovery action. | `FAILED` |
| Who owns semantic effects? | EffectSlot is implemented in `runtime_backend.py:1419-1679`, but production semantic finalization bypasses it. | `PARTIAL` |

## Static production-path conclusion

`SQLiteRuntimeBackend` is the current execution database for the normal
orchestrator/provider paths, and JSON state is generally a projection. The
authority claim fails at the semantic boundary: the live orchestrator invokes
domain writes without the durable runtime effect owner and without a single
runtime revalidation point for map/claim identity.
