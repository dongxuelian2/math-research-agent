# Final Finding Disposition

This matrix is the independent result for the candidate at
`3e2b6f481651d5a6b3b776c5d728cea6b93e2869`. It supersedes the repair handoff's
non-certifying `F-002/F-005/F-007 = YES` claims only for audit disposition; it
does not modify those historical records.

| Finding | Severity | Disposition | Reason |
|---|---:|---|---|
| F-001 stale `SessionClosure` | P0 | `CLOSED` | Exact map/root identity and stale replay controls remain green. |
| F-002 expired-lease authority | P0 | `CLOSED` | Late provider output is terminal metadata only; routed parsing raises, restart selection is empty, and effect preparation is blocked. |
| F-003 production EffectSlot bypass | P1 | `CLOSED` | Existing semantic-finalization and EffectSlot regressions remain green. |
| F-004 stranded `DISPATCHED` execution | P1 | `CLOSED` | Existing fault recovery yields unknown execution/dead-letter/manual review without same-attempt auto-redispatch. |
| F-005 strategic-thesis authorization bypass | P1 | `CLOSED` | The capability is issued only after durable exact-chain validation; the independent forgery matrix cannot mutate a map. |
| F-006 same-model critic independence | P1 | `CLOSED` | Same-model approval is rejected and the heterogeneous positive path remains green. |
| F-007 routed cross-plane bindings | P1 | `OPEN` | Partial bindings are accepted as wildcards and a no-backend semantic router bypasses the required-binding guard. |
| NF-003 partial current-domain binding | P1 | `OPEN` | `ResearchOrchestrator._validate_execution_binding()` returns `True` for root-only binding while richer current context exists. |
| NF-004 no-backend binding guard | P1 | `OPEN` | `RoutedLLMClient._execute_route()` enforces the flag only if a runtime backend exists. |

## F-002 closure basis

`F002-TERMINAL-REJECTION` verifies all of the following for an expired late
result:

- runtime metadata reports `accepted=False` and `authoritative=False`;
- the provider body is absent from the routed response;
- restart reconciliation does not select the result;
- explicit result acceptance raises;
- effect preparation raises and creates no EffectSlot;
- the routed consumer raises `RuntimeResultRejected`;
- the semantic apply callback is never invoked; and
- the retained attempt result is `STALE_FENCED` with `authoritative=0`.

The production chain supporting this is visible at
`runtime_backend.py:1417-1455`, `runtime_dispatch.py:162-210`, and
`routing.py:1058-1063`.

## F-005 closure basis

The independent matrix exercises ten named cases covering raw typed
`AUTHORIZED`, cloned/mutated authorization, altered scope, cross-map reuse,
restart replay, serialization/deserialization, stripped and nested envelopes,
alternate casing, wrong-thesis map targeting, and an invalid mutation target.
Every case was rejected without map mutation. The legitimate governed reframe
and end-to-end governance paths also passed in the existing targeted tests.

The production contract now requires `_TrustedGovernedReframe` at
`research_store.py:837-854`; the capability is issued only after the durable
exact-chain checks at `research_store.py:722-805` and `:1053-1063`.

## F-007 residual basis

The complete-binding restart test is positive but does not prove exact binding
completeness. `NF-003` and `NF-004` are independent public-path reproducers,
not test-only calls into private storage. Their full details and minimal repair
directions are in `FINAL_REAUTHORIZATION_REPORT.md` and
`CROSS_PLANE_ADVERSARIAL_RESULTS.md`.
