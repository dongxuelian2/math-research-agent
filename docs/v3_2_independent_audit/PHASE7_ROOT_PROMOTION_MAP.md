# Phase 7, Root Synthesis, and Promotion Map

## Scope and non-execution statement

This map is static code inspection at baseline HEAD 3229aced9fa9bcae41c5ddfea6b6291a6e68d725. No RootSynthesis, FinalConsolidation, truth promotion, or promotion recovery was executed.

The historical corpus is versioned evidence, not normative authority. Earlier V3_2_IMPLEMENTATION_MATRIX text says ROOT_SYNTHESIS and Final Consolidation were not started in its earlier addendum; later final_debug_handoff documents and the current code describe Phase 7 implementation. That phase-semantics/documentation drift is itself an audit target.

## Phase 7 objects and locations

Phase7Store in phase7.py owns project-local immutable subtrees:

- phase7/root_synthesis/<digest>.json
- phase7/root_synthesis_bodies/<digest>.md or body file
- phase7/final_consolidations/<digest>.json
- phase7/final_proofs/<digest>.<suffix> plus proof manifest
- phase7/consolidation_reaudits/<digest>.json
- phase7/promotion_closures/<digest>.json

All three primary records are strict identity objects:

| Object | Identity carried | Required next link |
|---|---|---|
| RootSynthesis | theorem, root/audited ClaimSnapshot, ResearchMap id/version/hash, SessionClosure id/hash, obligation/evidence refs, audit refs, candidate hash, gate hash, body hash | FinalConsolidation |
| FinalConsolidation | theorem, audited/resulting root identity, RootSynthesis id/hash, candidate/proof hash, gate hash, re-audit hash | TruthMutationIntent / PromotionClosure |
| PromotionClosure | audited/resulting ClaimSnapshot, RootSynthesis and FinalConsolidation ids/hashes, re-audit, TruthMutation id/receipt, resulting PROVED | complete run state |

Hashes use domain-separated content identities and loaders recompute filenames/body/proof/record linkage.

## Phase 7 start and state

The normal start is in ResearchOrchestrator._finalize after:

1. the AuditGate is passed;
2. canonical authority is refreshed and authority_promotion_decision allows promotion;
3. the research session is closed;
4. the SessionClosure is loaded and resolution/evidence has been validated;
5. current ClaimSnapshot comparison remains compatible;
6. the current ResearchMap is loaded and its frontier is closed.

The orchestrator persists Phase 7 state in state.json, including phase/status, root synthesis id/hash/file, final consolidation id/hash/file, final proof path/hash, re-audit hash, truth mutation id/intent hash/receipt hash, resulting ClaimSnapshot hash, and closure/promotion status. The state strings observed in the code include ROOT_SYNTHESIS, FINAL_CONSOLIDATION, TRUTH_PROMOTED, PROMOTION_CLOSED, COMPLETE, and PROVED.

If the run starts with a stale or missing snapshot/map, or with an already persisted completion state whose closure does not verify, the orchestrator blocks rather than silently continuing.

## RootSynthesis prerequisites

Phase7Store.synthesize_root performs or requires these checks:

- ClaimSnapshot is compatible for root synthesis;
- AuditGate passed and the supplied gate hash is the exact persisted/current gate identity;
- theorem id and root snapshot agree;
- ResearchMap id/version/hash and root snapshot agree;
- TacticalSession/SessionClosure exists, is completed, and is bound to the current map/root/obligation;
- closure validated evidence is nonempty, references retained raw artifacts, and has trusted verifier/provider provenance as required by research_evidence.py;
- all referenced obligations are present and closed/resolved at the exact map version;
- no map obligation is OPEN, BLOCKED, or other nonterminal frontier;
- candidate proof file exists and its reference/hash is captured;
- audit artifact references are retained and hashable;
- the synthesized body/record identity is deterministic and immutable.

The RootSynthesis object is a manifest of the references and hashes; it is not merely the candidate Markdown. The exact coverage semantics of the evidence refs and the independent-verifier semantics remain subjects for independent audit.

## FinalConsolidation prerequisites

Phase7Store.consolidate loads and revalidates RootSynthesis, then requires:

- same theorem/root/map identity;
- same passed gate and exact gate hash;
- candidate bytes unchanged from the root reference;
- final proof body written with content hash;
- a consolidation re-audit JSON with passed=true and exact identity/hash;
- immutable FinalConsolidation record linking all of the above.

This produces a final proof artifact and a re-audit record. It does not itself mutate ProjectStore theorem status.

## Promotion decision and truth mutation

The orchestrator then constructs TruthMutationIntent with:

- theorem id and expected current status;
- audited ClaimSnapshot hash;
- AuditGate hash/result;
- audit artifact refs;
- current root-only CrossPlaneExecutionBinding;
- root/final proof/consolidation references.

The truth path is:

~~~text
FinalConsolidation
  -> TruthMutationIntent
  -> RuntimeEffectCoordinator.register_semantic_result
  -> RuntimeEffectCoordinator.apply_truth_transition
  -> TruthStoreFacade.compare_and_transition
       -> current ClaimSnapshot comparison
       -> gate/audited snapshot exact check
       -> ProjectStore.compare_and_transition under truth_transaction
       -> resulting ClaimSnapshot
       -> TruthMutationReceipt
  -> Phase7Store.close_promotion
       -> exact intent/receipt/root/consolidation/re-audit linkage
       -> resulting theorem status PROVED
  -> state PROMOTION_CLOSED / COMPLETE
~~~

ProjectStore._transition_locked supplies an additional lifecycle gate: only Archivist can mark PROVED and only with a passing AuditGate; Human is the only actor allowed to reopen PROVED. TruthStoreFacade validates identity-critical fields and current status before the transition. The runtime effect path adds idempotency/effect-slot/recovery controls.

## Gates, receipts, manifests, and validators

| Layer | Gate / receipt / manifest | Static validator or owner |
|---|---|---|
| Source authority | CanonicalResolution + immutable body/cache | CanonicalArtifactResolver and authority_promotion_decision |
| Truth identity | ClaimSnapshot + comparison status | TruthStoreFacade._validate and compare_claim_snapshots |
| Research scope | ResearchMap version/root/obligation refs | ResearchStoreFacade and ResearchMap strict loaders |
| Tactical evidence | Directive, TacticalSession, SessionClosure, EvidenceProjection | ResearchStoreFacade, can_resolve_obligation |
| Audit | AuditGate with specialist/final/dependency/flag fields | AuditCoordinator and state_machine.AuditGate.passed |
| Runtime result | AttemptResult artifact/binding/lease/generation | SQLiteRuntimeBackend.record_result/accept_result |
| Semantic effect | EffectSlot + domain applied/ack rows | RuntimeEffectCoordinator and SQLiteRuntimeBackend.apply_effect_once |
| Root | RootSynthesis record/body/hash refs | Phase7Store.synthesize_root/load_root_synthesis |
| Final | FinalConsolidation, proof bytes, re-audit | Phase7Store.consolidate/load_final_consolidation |
| Truth mutation | TruthMutationIntent/Receipt | TruthStoreFacade.compare_and_transition/recovery |
| Closure | PromotionClosure | Phase7Store.close_promotion/verify_promotion_closure |

## Restart cases

### Before root synthesis

A checkpoint with candidate/audit evidence but no root is resumed through the normal orchestrator path. Current snapshot, map, session closure, gate, evidence refs, and open frontier are revalidated. Root construction may be attempted only when all prerequisites are still current. A stale ClaimSnapshot or map blocks/requires re-audit.

### After durable root synthesis

The root record/body can be loaded by digest. Consolidation still rechecks root/gate/candidate bytes; a root file or body tamper blocks. A root record does not by itself authorize truth promotion.

### After durable final consolidation

Final proof and re-audit are reloaded and verified. TruthMutationIntent is created or recovered. The current theorem/snapshot/gate must still match. FinalConsolidation alone does not make the theorem PROVED.

### After truth mutation but before closure

_resume_phase7_after_truth_promotion loads root synthesis, final consolidation, intent, and receipt. It calls close_promotion only after the receipt and resulting PROVED status validate, then marks state PROMOTION_CLOSED and complete. Missing/mismatched receipt or closure linkage blocks.

### Late/rejected result interaction

Phase 7 consumes accepted, validated evidence references, not arbitrary provider output. Runtime results fenced as STALE_FENCED, non-authoritative, missing-artifact, or binding-mismatched cannot become candidate/audit semantic inputs through the normal route. Independent testing should confirm no retained late artifact can be selected through a restart path after a Phase 7 artifact exists.

## Bypass inventory to attack

Static call-site review should enumerate every caller of:

- Phase7Store.synthesize_root, consolidate, close_promotion, verify_promotion_closure;
- TruthStoreFacade.compare_and_transition and ProjectStore.compare_and_transition;
- RuntimeEffectCoordinator.apply_truth_transition and apply_effect_once;
- ResearchStoreFacade.apply_governed_reframe/revise_map;
- RoutedLLMClient._execute_route and RuntimeBackend.accept_result;
- AuditGate construction and any code that writes gate.json/state fields.

The principal static concern is not an obvious direct bypass in the normal orchestrator sequence; it is whether a public or test-facing caller can supply a partial/forged artifact or invoke a narrower validator that is too permissive. The code has explicit adapters for normal execution, root-only truth, and map-scoped effects; an independent pass should prove that each adapter is used only for its intended scope.

## Phase-semantics note

There are at least three representations of progress:

1. Project theorem status in state_machine.py.
2. Orchestrator run phase/status in state.json.
3. Phase 7 record existence and closure status in the phase7 subtree.

The implementation attempts to bind them, but the historical matrix and later handoff use different “started/completed/authorized” vocabularies. The next audit should compare persisted fields and actual files after injected failure at every boundary rather than trusting one projection.
