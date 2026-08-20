# Exactly-Once Certification

## Verdict

`RESEARCH_EFFECT_EXACTLY_ONCE = PARTIAL`

`TRUTH_EFFECT_EXACTLY_ONCE = PARTIAL`

`GOVERNANCE_EFFECT_EXACTLY_ONCE = PARTIAL`

`RUNTIME_EFFECT_SLOT_CORE = CERTIFIED_WITH_LIMITATION`

The runtime primitive is strong in isolation, but the production semantic
effect paths are not routed through it. Therefore a Phase 3--6 system-level
exactly-once certificate would overstate the evidence.

## Runtime primitive evidence

`prepare_effect` derives one identity from
`(LogicalJob, effect_kind, semantic_target_type, semantic_target_id)` and
requires `source_result_id` to equal the accepted result. An existing slot
with another accepted source result is rejected (`runtime_backend.py:1419-1490`).
`apply_effect_once` then recovers a PREPARED slot before applying the domain
callback and advances `PREPARED → DOMAIN_APPLIED → ACKNOWLEDGED`
(`runtime_backend.py:1620-1679`).

Independent tests passed:

- D9: two successful attempts, one accepted result, one effect slot and one
  callback;
- D19: domain write before runtime acknowledgement is recovered without
  repeating the domain callback;
- D25: normal and `BEFORE_RESULT_DB_COMMIT` recovery reach equivalent final
  runtime/effect states;
- Truth, Research, and Governance adapter replay tests each preserve the
  domain identity in their own fixtures.

## Production ownership gap

The only production semantic finalization call graph observed is:

```text
orchestrator._close_research_session
  -> ResearchStoreFacade.resolve_session_closure
  -> GovernanceController.record_effect / record_session

orchestrator._finalize
  -> TruthStoreFacade.compare_and_transition
```

These calls are visible at `orchestrator.py:1706-1758` and
`:1790-1810`. There is no production import/call to
`RuntimeEffectCoordinator` or `SQLiteRuntimeBackend.apply_effect_once`; the
coordinator appears in `runtime_effects.py` and test modules. Domain methods
have useful local idempotence, but that is not the same as a runtime-owned
cross-store saga with one durable effect identity and reconciler action.

## Cross-store crash windows

| Effect | Durable order observed | Recovery identity | System verdict |
|---|---|---|---|
| Research closure | closure artifact → decision artifact → map revision → governance effect/session projections | SessionClosure id and resolution basis; no production EffectSlot | `PARTIAL` |
| Truth mutation | intent/prepared evidence → theorem CAS → receipt | mutation id and receipt; facade recovery exists | `PARTIAL` |
| Architecture review | immutable review file → review clock write | review id/hash; controller replay recognizes exact identity | `CERTIFIED_WITH_LIMITATION` |
| Architecture patch | authorization → map application → application artifact/control projection | authorization id and exact target map | `CERTIFIED_WITH_LIMITATION` |
| Provider dispatch | AttemptIntent/outbox → provider → artifact/manifest → result → accepted winner | logical job/attempt/artifact ids; unknown DISPATCHED execution is not handled | `FAILED` |

The missing production EffectSlot boundary and the `DISPATCHED` recovery gap
are sufficient to deny whole-system exactly-once certification even though the
unit-level coordinator tests pass.
