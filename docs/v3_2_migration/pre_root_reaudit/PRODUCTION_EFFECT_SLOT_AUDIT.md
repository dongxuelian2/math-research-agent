# Production EffectSlot Ownership Audit

## Normal production call graph

The normal `ResearchOrchestrator` now creates a
`RuntimeEffectCoordinator` and routes semantic finalization through it:

```text
accepted routed result
  -> orchestrator binding/validator
  -> internal semantic result
  -> RuntimeEffectCoordinator
  -> SQLite apply_effect_once
  -> domain adapter
  -> durable domain identity/receipt
  -> EffectSlot ACK
```

Observed orchestrator entries include session closure, structural effect,
governance review/session, route-failure, and final TruthMutation calls in
`orchestrator.py`.  `runtime_effects.py` supplies bounded adapters for the
Research, Governance, and Truth stores and uses stable idempotency keys.

## Effect matrix

| Effect | Production EffectSlot path | Stable identity | Crash/replay evidence | Result |
|---|---|---|---|---|
| SessionClosure | `apply_research_session_closure` | closure id, decision hash, map identity | `test_f003_domain_apply_before_ack_reconciles_by_effect_identity`; production E2E | `CERTIFIED` |
| Research resolution/map revision | closure and route-failure adapters | obligation/map hash, resolution basis | stale replay and EffectSlot recovery tests | `CERTIFIED` |
| Structural/Governance effect | `apply_structural_effect`, governance session/review signal adapters | structural-effect id/hash and clock identity | production E2E and focused recovery tests | `CERTIFIED` |
| ArchitectureReview | `commit_architecture_review` adapter | review id/hash and clock receipt | adapter/review tests; no normal orchestrator caller found | `CERTIFIED_WITH_LIMITATION` |
| ArchitecturePatch | `apply_architecture_patch` adapter | patch/application/authorization identity | governance authorization and patch replay tests | `CERTIFIED_WITH_LIMITATION` |
| TruthMutation | final promotion calls `apply_truth_transition` | mutation id and TruthMutationReceipt | truth crash-after-transition recovery plus production E2E | `CERTIFIED` |

The ArchitectureReview/Patch limitation is a coverage/ownership boundary: the
adapters exist and are EffectSlot-owned, while the current repository has no
normal orchestrator call site that commits a review or applies a patch.  Direct
GovernanceController calls found in tests and explicit admin/migration flows
are not counted as normal orchestrator semantic bypasses.

## Direct-caller classification

| Direct call family | Observed callers | Classification |
|---|---|---|
| `resolve_session_closure`, `record_disposition`, `revise_map` inside `research_store.py` | domain implementation and runtime adapters | DOMAIN / ADAPTER |
| `compare_and_transition` inside `runtime_effects.py` and TruthStore | bounded Truth adapter; TruthStore implementation | ADAPTER / DOMAIN |
| `GovernanceController.commit_review` and `apply_authorized_patch` | runtime adapters, governance tests, explicit governance API | ADAPTER / ADMIN / TEST |
| `orchestrator._transition_truth` | lifecycle statuses such as `IN_RESEARCH`/`AUDITING`; final promotion is separate | LIFECYCLE, not final TruthMutation |
| `campaign.resume` governance signal | legacy checkpoint/admin resume | MIGRATION / ADMIN |

No normal orchestrator semantic finalization bypass was found.  This closes
F-003, with the stale recovery and late-payload authority issues tracked
separately as F-002/NF-001 and F-007/NF-002.
