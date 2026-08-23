# Findings

## F-001 — stale SessionClosure can re-resolve a superseded obligation

- **Severity:** P0
- **Planes:** Research × Governance × Runtime recovery
- **Production path:** `ResearchOrchestrator._close_research_session` →
  `ResearchStoreFacade.resolve_session_closure`
- **Reproduction:** run the X2 probe in `run_cross_plane_probes.py`.
- **Expected:** a v1 closure arriving after a v3 reframe is rejected,
  revalidated against the exact current map, or accepted only through an
  explicit authorized transfer.
- **Actual:** v1 closure was accepted while current v3 marked O1
  `SUPERSEDED` in favor of O2; replay created v4 with O1 `RESOLVED` and O2
  still `OPEN`.
- **Evidence:** X2 output; `research_store.py:364-406`;
  `research_evidence.py:421-506`.
- **Root cause:** closure `research_map_version`/`research_map_hash` are not
  compared, and the resolution gate does not require the current disposition
  to be resolvable.
- **Minimum repair recommendation:** require exact current map version/hash
  plus disposition compatibility before `record_disposition`; route stale
  closures to explicit transfer/revalidation.
- **Blocks Phase 7:** yes.

## F-002 — expired lease is not a result-fencing condition

- **Severity:** P0
- **Planes:** Durable Runtime × Truth/Research/Governance authority
- **Production path:** `DurableProviderDispatcher.execute` →
  `SQLiteRuntimeBackend.record_result`
- **Reproduction:** run X4; the probe expires the lease before submitting the
  generation-1 result.
- **Expected:** artifact retained for provenance, result marked stale/fenced,
  and no winner/effect authority granted.
- **Actual:** `ingestion_state=INGESTED`, `authoritative=True`, and the
  attempt became `RESULT_RECORDED`.
- **Evidence:** X4 output; `runtime_backend.py:1177-1299`; existing D13 only
  covers a result after explicit generation-2 reassignment, not expiry before
  reconciliation.
- **Root cause:** `record_result` compares token/generation/state but never
  checks `lease_expires_at <= now`.
- **Minimum repair recommendation:** fence authoritative ingestion when the
  lease is expired; preserve the artifact/result as non-authoritative and
  reconcile before any acceptance/effect path.
- **Blocks Phase 7:** yes.

## F-003 — production semantic effects bypass EffectSlot ownership

- **Severity:** P1
- **Planes:** Runtime × Truth × Research × Governance
- **Production path:** orchestrator direct calls at `orchestrator.py:1706-1758`
  and `:1790-1810`.
- **Reproduction:** static production-path search for
  `RuntimeEffectCoordinator`/`apply_effect_once` plus inspection of the live
  close/finalize methods.
- **Expected:** accepted runtime result → unique EffectSlot → deterministic
  domain recovery → acknowledgement for each semantic effect.
- **Actual:** the orchestrator directly resolves Research, records Governance
  effects/sessions, and calls Truth mutation. The EffectSlot coordinator is
  only exercised by adapter tests.
- **Evidence:** `runtime_effects.py:12-174`; no production import/call outside
  that module and tests; `EXACTLY_ONCE_CERTIFICATION.md`.
- **Root cause:** runtime effect adapters were added but not wired into the
  production semantic finalization owner.
- **Minimum repair recommendation:** make production semantic effects enter a
  runtime-owned effect adapter with claim/map/governance bindings; keep domain
  idempotence as recovery, not as the runtime authority.
- **Blocks Phase 7:** yes.

## F-004 — `AFTER_PROVIDER_RESULT` leaves an unrecoverable DISPATCHED outbox

- **Severity:** P1
- **Planes:** Durable Runtime
- **Production path:** `DurableProviderDispatcher.execute` →
  `RuntimeReconciler.run`.
- **Reproduction:** inject `FaultPoint.AFTER_PROVIDER_RESULT`, expire the
  attempt, and run `backend.reconcile()`.
- **Expected:** unknown provider execution is adopted from a durable manifest,
  retried as a new attempt, or sent to explicit manual review.
- **Actual:** no result manifest exists; attempt becomes `ORPHANED`, outbox
  remains `DISPATCHED`, and reconciliation records only `MARK_ORPHANED` for the
  attempt.
- **Evidence:** probe output; `runtime_dispatch.py:122-131`;
  `runtime_reconciler.py:50-99`.
- **Root cause:** the outbox reconciler has no `DISPATCHED` unknown-execution
  policy.
- **Minimum repair recommendation:** add a durable unknown-execution state and
  idempotent recovery policy before redispatch; do not silently treat a
  provider-accepted call as never sent.
- **Blocks Phase 7:** yes.

## F-005 — strategic-thesis mutation bypasses ArchitecturePatch authorization

- **Severity:** P1
- **Planes:** Research × Architecture Governance
- **Production path:** any caller with `ResearchStoreFacade.revise_map` access;
  the API accepts `strategic_thesis` directly.
- **Reproduction:** `GOV-THESIS-BYPASS` probe calls
  `revise_map(..., revision_reason="HUMAN_STEERING", strategic_thesis=...)`
  without a PatchAuthorization.
- **Expected:** a strategic thesis change is classified as destructive and
  requires review, critic, scope transfer, and authorization.
- **Actual:** map v2 is created without authorization.
- **Evidence:** probe output; `research_store.py:481-580`;
  `architecture_patch.py` classifies `CHANGE_STRATEGIC_THESIS` as destructive.
- **Root cause:** `revise_map` gates only removed/reframed scope and the exact
  `ARCHITECTURE_PATCH` reason, not a changed strategic thesis.
- **Minimum repair recommendation:** enforce authorization whenever the
  strategic thesis differs, or make callers use only the typed governed reframe
  path.
- **Blocks Phase 7:** yes.

## F-006 — same-model fallback is recorded as independent

- **Severity:** P1
- **Planes:** Architecture Governance
- **Production path:** `ArchitectureCriticIndependenceReceipt.capture` feeds
  critic approval/authorization.
- **Reproduction:** `GOV-SAME-MODEL-FALLBACK` probe and existing focused test.
- **Expected:** when reviewer and critic share a model under this harness rule,
  `policy_satisfied=false`.
- **Actual:** `same_model=True` and `policy_satisfied=True` when actors and
  contexts differ.
- **Evidence:** `architecture_critic.py:64-105`; existing test assertions at
  `test_architecture_critic_and_authorization.py:212-214`.
- **Root cause:** the implemented policy is only different actor + fresh,
  non-shared context; same model/provider are recorded but do not invalidate
  satisfaction.
- **Minimum repair recommendation:** encode the harness rule in the receipt
  predicate and add a negative authorization test for same-model fallback.
- **Blocks Phase 7:** yes.

## F-007 — routed attempts omit cross-plane claim/map bindings

- **Severity:** P1
- **Planes:** Runtime × Truth × Research × Governance
- **Production path:** `RoutedLLMClient._execute_route` creates a LogicalJob at
  `routing.py:942-951` and invokes the durable dispatcher at `:957-975`.
- **Reproduction:** static inspection shows no `claim_snapshot_hash`,
  `research_map_version`, `governance_ref`, or `directive_context_refs` in the
  production job call; X1 shows runtime acceptance does not independently
  compare a current claim.
- **Expected:** each attempt/result/effect is bound to the exact root claim,
  map version/hash, directive, and governance context, with stale recovery
  fail-closed.
- **Actual:** the routed job carries role/obligation/branch payload only and
  defaults claim binding to null unless a direct caller supplies it.
- **Evidence:** `routing.py:919-975`; `runtime_backend.py:391-430`,
  `:472-548`; `STALE_STATE_CERTIFICATION.md`.
- **Root cause:** `RoutedLLMClient` has no cross-plane binding fields and the
  runtime accepts a result without checking current domain identity.
- **Minimum repair recommendation:** pass immutable bindings from the
  orchestrator into the router/LogicalJob/AttemptIntent and validate them at
  result acceptance and semantic-effect preparation.
- **Blocks Phase 7:** yes.

## P2/P3 findings

No separate P2/P3 production defect was required for the denial. The commit
count discrepancy is corrected as audit bookkeeping: the exact frozen range
contains 12 commits, not 13.
