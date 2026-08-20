# Stale and Late Authority Re-Audit

## Authority matrix

| Scenario | Direct runtime result | Production/recovery result | Verdict |
|---|---|---|---|
| Fresh claim/map, valid lease | accepted result can become the winner | normal bound route carries the binding | `CERTIFIED` |
| Stale claim, explicit binding validator | `accept_result` fences; artifact retained | repair X1 probe is certified | `CERTIFIED_WITH_LIMITATION` |
| Stale claim after restart/reconciliation | no validator is supplied | C1 result selected after current claim changed | `FAILED` — NF-002 |
| Stale map identity | EffectSlot map validator rejects direct adapter application | pending-result reconciliation bypasses that validator | `FAILED` — NF-002 |
| Expired lease, no generation change | `STALE_FENCED`, non-authoritative | X4 and `RX-COMPOUND-STALE` pass | `CERTIFIED` at SQLite boundary |
| Old generation after replacement | old token/generation result is fenced; fresh generation wins | `test_durable_runtime.py` replacement race | `CERTIFIED` |
| Same result replay | stable result/effect identity; no duplicate slot | durable runtime and EffectSlot recovery tests | `CERTIFIED` |

The two failures are not contradicted by the positive direct acceptance probe:
the validator works when supplied, but production recovery can omit it.

## F-001 evidence

The closure path compares exact map id/version/hash/root before semantic
resolution and checks current obligation disposition.  The independent
three-obligation probe exercised:

```text
v1: O1, O2, O3
governed reframe: O1 -> N1, O2 -> N1, O3 -> N2
late O1 closure, late O2 closure, duplicate O3 closure
```

All five replay calls returned `STALE_SESSION_CLOSURE`; O1/O2/O3 remained
`SUPERSEDED`, N1/N2 remained `OPEN`, and the map stayed at version 2.

## F-002/NF-001 evidence

The exact expired-lease invariant is repaired, but the semantic consumer
boundary is not.  `RX-LATE-PAYLOAD-AUTHORITY` returned a high-value parsed
payload while the runtime explicitly reported both `accepted=False` and
`authoritative=False`.  The provider response must be treated as quarantined
at that boundary, not merely annotated.
