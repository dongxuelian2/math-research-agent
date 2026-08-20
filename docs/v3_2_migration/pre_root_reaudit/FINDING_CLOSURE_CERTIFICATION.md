# Finding Closure Certification

## Final finding matrix

| Finding | Severity | Verdict | Independent basis |
|---|---:|---|---|
| F-001 stale `SessionClosure` | P0 | `CLOSED` | Exact map identity and current-disposition gates; X2 and the three-obligation stale replay pass. |
| F-002 expired-lease authority | P0 | `OPEN` | `record_result` now fences expiry, but a fenced late payload is still parsed by the routed semantic consumer; see NF-001. |
| F-003 production EffectSlot bypass | P1 | `CLOSED` | Normal orchestrator semantic finalization enters `RuntimeEffectCoordinator`; direct domain calls are bounded adapters, admin/migration, or tests. |
| F-004 stranded `DISPATCHED` execution | P1 | `CLOSED` | Fault window produces `UNKNOWN_EXECUTION` plus `DEAD_LETTER` and manual review; no same-attempt automatic redispatch. |
| F-005 strategic-thesis authorization bypass | P1 | `OPEN` | Missing authorization is rejected, but a caller-forged typed `AUTHORIZED` object with failed gates is accepted by public `revise_map`. |
| F-006 same-model critic independence | P1 | `CLOSED` | Same model yields `same_model=True`, `policy_satisfied=False`, and authorization is rejected; different-model positive path passes. |
| F-007 routed cross-plane bindings | P1 | `OPEN` | Normal orchestrator routes persist bindings, but startup reconciliation accepts a stale persisted result without a domain validator and standalone semantic routers are unbound; see NF-002. |

## F-001 — closed

`ResearchStoreFacade.evaluate_session_closure` compares
`research_map_id`, version, hash, and root snapshot before evaluating evidence.
`can_resolve_obligation` rejects `SUPERSEDED`, `ABANDONED_WITH_REASON`,
`BLOCKED`, and incompatible resolved states.  The runtime adapter also binds
the closure and calls `apply_effect_once` with a current-map validator.

Evidence: `test_f001_stale_closure_isolated_and_explicit_transfer_revalidates`,
the frozen X2 run, `RX-COMPOUND-STALE`, and `NO-SCOPE-STALE-REPLAY`.  Each late
closure remains provenance; no old obligation or replacement obligation is
resolved and no extra map version is created.

## F-002 — open residual

The narrow lease invariant is repaired: the frozen X4 run and
`test_f002_expired_result_is_retained_but_cannot_be_accepted` observe
`STALE_FENCED`, `authoritative=False`, retained artifact provenance, and zero
effect slots.  However, `DurableProviderDispatcher.execute` returns the
provider body with `runtime.accepted=False` and `runtime.authoritative=False`.
`RoutedLLMClient` and `_pipeline_llm_handler` do not make that runtime status a
terminal rejection.  The independent `RX-LATE-PAYLOAD-AUTHORITY` probe parsed a
high-value successful payload despite both runtime flags being false.  The
pipeline then has code paths that schedule verification and close obligations.

This leaves the end-to-end P0 authority boundary open even though the SQLite
lease fence itself is correct.

## F-005 — open residual

The ordinary `GOV-THESIS-BYPASS` probe passes, and the authorized
ArchitecturePatch path is positive in the governance suite.  That is not
sufficient: `ResearchStoreFacade.revise_map` only checks that the supplied
object is a `PatchAuthorization` with status `AUTHORIZED`.  It does not bind
the object to a persisted patch/review/critic chain or require its scope and
truth gates.  `GOV-THESIS-FORGED-AUTH` changed the strategic thesis using
`scope_validation_passed=False`, `truth_boundary_intact=False`, and forged
identities.

## F-007 — open residual

The normal orchestrator sets a binding and validator on its shared router, and
the focused persistence test confirms the binding survives logical job,
attempt, result, and effect-slot storage.  The production recovery path is not
equivalent: the orchestrator calls `reconcile()` before loading current
research/governance state, and `RuntimeReconciler._accept_pending_results`
calls `accept_result` without a binding validator.  The restart probe accepted
a C1/v1 result after the project had moved to a new claim snapshot and map
version.  In addition, `formalization.py` and `certification.py` create runtime
routers without an execution binding; the provider-smoke diagnostic is the
intentional non-semantic exception.
