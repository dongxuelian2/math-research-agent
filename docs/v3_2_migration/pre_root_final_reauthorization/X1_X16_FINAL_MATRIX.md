# Final X1-X16 Matrix

This matrix is the final independent disposition after the repair claims,
frozen probes, regression suite, and new adversarial entry-point probes.

| ID | Verdict | Evidence and limitation |
|---|---|---|
| X1 | `OPEN` | Root-only stale-claim control passes, but the richer current-domain partial-binding variant is accepted (`NF-003`). |
| X2 | `CERTIFIED` | Exact map identity and stale disposition controls remain green. |
| X3 | `CERTIFIED_WITH_LIMITATION` | Canonical authority is revalidated; no new root gap was found, but no OS crash was injected into filesystem patch application. |
| X4 | `CERTIFIED` | Expired generation-1 result is retained as `STALE_FENCED` and cannot win. |
| X5 | `CERTIFIED` | Duplicate SessionClosure and stable EffectSlot identity remain green. |
| X6 | `CERTIFIED` | Governance review/effect replay and clock ownership remain green. |
| X7 | `OPEN` | Normal complete bindings pass, but partial and no-backend semantic router paths fail closedness (`NF-003`, `NF-004`). |
| X8 | `CERTIFIED` | TruthMutation EffectSlot and receipt recovery remain green. |
| X9 | `CERTIFIED` | ResearchMap resolution and route-failure EffectSlots remain gated. |
| X10 | `CERTIFIED_WITH_LIMITATION` | Scope transfer tests pass; filesystem crash injection was not performed. |
| X11 | `CERTIFIED_WITH_LIMITATION` | Local winner/effect idempotence is green; external provider delivery remains at-least-once. |
| X12 | `CERTIFIED` | `AFTER_PROVIDER_RESULT` becomes unknown execution plus dead-letter/manual review. |
| X13 | `CERTIFIED_WITH_LIMITATION` | Local cancellation/stale-result race is covered; external delivery is not exactly-once. |
| X14 | `CERTIFIED_WITH_LIMITATION` | Review-due checkpoint survives; crash between governance artifact and clock writes was not injected. |
| X15 | `CERTIFIED_WITH_LIMITATION` | Legacy checkpoint migration requires revalidation where bindings are absent. |
| X16 | `CERTIFIED` | Project-local runtime and artifact isolation remain green. |

```text
CERTIFIED = 8
CERTIFIED_WITH_LIMITATION = 6
OPEN = 2
TOTAL = 16
```

The two open controls are not bookkeeping-only failures. They are the
cross-plane authority controls required to prevent a current root claim from
being paired with an incomplete map/session/governance checkpoint or with an
unbound semantic provider response.
