# Cross-Plane Re-Audit Matrix

## X1-X16 re-audit disposition

This is the independent disposition after the new recovery and consumer
attacks, not the repair handoff's aggregate count.

| ID | Verdict | Evidence/limitation |
|---|---|---|
| X1 | `OPEN` | Direct validator probe passes, but restart reconciliation selects stale C1/v1 authority (NF-002). |
| X2 | `CERTIFIED` | Exact map identity and disposition checks; stale replay does not mutate the map. |
| X3 | `CERTIFIED_WITH_LIMITATION` | Canonical authority remains domain-owned and revalidated; no new root gap found. |
| X4 | `CERTIFIED` | Expiry-only generation-1 result is retained and `STALE_FENCED`. |
| X5 | `CERTIFIED` | Duplicate SessionClosure and stable EffectSlot identity pass. |
| X6 | `CERTIFIED` | Governance review/effect replay and clock ownership pass. |
| X7 | `OPEN` | Normal routes persist bindings, but recovery and standalone semantic routers have binding gaps. |
| X8 | `CERTIFIED` | TruthMutation EffectSlot and receipt recovery pass. |
| X9 | `CERTIFIED` | ResearchMap resolution/route-failure EffectSlots and stale gates pass. |
| X10 | `CERTIFIED_WITH_LIMITATION` | Scope transfer tests pass; no OS crash was injected inside filesystem patch application. |
| X11 | `CERTIFIED_WITH_LIMITATION` | Local winner/effect idempotence is certified; external provider delivery remains at-least-once. |
| X12 | `CERTIFIED` | `AFTER_PROVIDER_RESULT` becomes unknown execution plus dead letter/manual review. |
| X13 | `CERTIFIED_WITH_LIMITATION` | Cancellation/stale-result race covered locally; external delivery is not exactly-once. |
| X14 | `CERTIFIED_WITH_LIMITATION` | Review-due checkpoint survives; no crash was injected between governance artifact and clock writes. |
| X15 | `CERTIFIED_WITH_LIMITATION` | Legacy checkpoint migration requires revalidation where bindings are absent. |
| X16 | `CERTIFIED` | Project-local runtime/artifact isolation passes. |

Independent exact count: `CERTIFIED=8`,
`CERTIFIED_WITH_LIMITATION=6`, `OPEN=2`, total `16`.

The repair handoff's bookkeeping is also independently reconciled: its X1-X16
mapping is `CERTIFIED=10` and `CERTIFIED_WITH_LIMITATION=6`; its two additional
governance probes (`GOV-THESIS-BYPASS`, `GOV-SAME-MODEL-FALLBACK`) make the
reported aggregate `12 + 6 = 18`.  Additional probes run in this re-audit are
five more IDs: `RX-COMPOUND-STALE`, `RX-RESTART-STALE-DOMAIN`,
`GOV-THESIS-FORGED-AUTH`, `RX-LATE-PAYLOAD-AUTHORITY`, and
`NO-SCOPE-STALE-REPLAY`.  Thus the re-audit additional-probe count is `7`
when both repair-handoff governance probes and new audit probes are included.

## Binding audit

| Path | Binding result |
|---|---|
| Normal `ResearchOrchestrator` -> `RoutedLLMClient` -> dispatcher | Bound after research initialization; persisted through job/attempt/result/effect slot. |
| `formalization.py` standalone runtime router | No `execution_binding` or validator; open semantic bypass. |
| `certification.py` standalone runtime router/client | No `execution_binding` or validator; open semantic bypass. |
| `literature.py` normal orchestrator construction | Uses the shared bound router; standalone callers are not the normal campaign path. |
| `provider-smoke` | Unbound by design and excluded as transport diagnostic. |
| Restart reconciliation | Binding columns survive, but current-domain validation is omitted; open NF-002. |

`PRODUCTION_SEMANTIC_JOB_BINDING_BYPASSES` is therefore not `NONE`.

## No-scope result

`NO-SCOPE-STALE-REPLAY = PASS` and
`NO_SCOPE_LOSS_UNDER_STALE_REPLAY = CERTIFIED`.  The three source obligations
remain superseded, two targets remain open, and duplicate/late closures do not
create another map revision.
