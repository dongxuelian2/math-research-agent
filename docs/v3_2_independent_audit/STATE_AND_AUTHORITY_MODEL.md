# State and Authority Model

## Core rule

The implementation separates coordination state from authority-bearing state. A JSON record, runtime row, provider result, or test assertion is not automatically allowed to mutate semantic state. The authority check must be reconstructed from the actual current root, map, scope, gate, artifact, lease, and receipt.

The following is descriptive at baseline; it is not a certification.

## Authority-bearing objects

| Object | Creator | Owner | Serialized form | Validator | Consumers | Mutation rights | Stale/revocation rule | Restart rule |
|---|---|---|---|---|---|---|---|---|
| Theorem/project truth | ProjectStore.initialize/add/update/transition | ProjectStore | project.json, theorems/id.json, index.json | ProjectStore lifecycle and dependency checks; state_machine.validate_transition | CLI, TruthStore, orchestrator, retrieval | ProjectStore APIs; PROVED only Archivist + passing gate; Human can reopen PROVED | assertion/identity changes make prior ClaimSnapshot stale; lifecycle transition must match expected status | reload JSON; rebuild index; no durable cross-process transaction |
| AssertionIdentity | truth_identity.py capture/hash | TruthStore/ClaimSnapshot as value owner | embedded in ClaimSnapshot/intent/receipt | strict identity/hash comparison | snapshot comparison, Phase 7, truth mutation | immutable value; theorem mutation changes it | assertion change is hard stale | recompute/load from immutable snapshot |
| ClaimSnapshot | TruthStoreFacade.capture_claim_snapshot | TruthStoreFacade | truth/claim_snapshots content-addressed JSON | TruthStoreFacade._validate; compare_claim_snapshots | orchestrator, runtime binding, Phase 7, formalization | new snapshot only; no in-place edit | dependency/assumption/authority/trust/semantic changes require revalidation or block; unknown/unresolvable blocks | load by hash; validate current theorem/authority/policy |
| ResearchMap | ResearchStoreFacade.create/revise/rebase | ResearchStoreFacade | research/maps immutable version plus current projection | ResearchMap strict loader; ResearchStore root/map validation; governance checks | directives, sessions, pipelines, governance, Phase 7 | ordinary status/evidence updates; destructive thesis/scope changes only governed reframe | parent/version/hash/root mismatch is stale; rebase requires explicit classification; scope loss rejected | load current map/projection; stale checkpoint requires revalidation |
| ResearchObligation | ResearchStoreFacade.add/revise | ResearchStoreFacade | research/obligations and map refs/projections | strict identity, current map/root, disposition rules | pipelines, directives, resolution | scoped disposition/revision; not theorem truth | stale map/root or invalid disposition blocks; OPEN/BLOCKED remain frontier | reconstruct current obligation projections from map/store |
| Directive | ResearchStoreFacade.create_directive | ResearchStoreFacade | immutable directive JSON | exact map/root/obligation checks | worker context, router, TacticalSession | immutable; new directive for changed scope | map/root/obligation mismatch stale | reload exact hash/id; no inferred replacement |
| TacticalSession | ResearchStoreFacade.create_tactical_session | ResearchStoreFacade | immutable session and closure references | exact directive/map/obligation/root; closure loader | orchestrator, evidence, governance | close once; session closure is immutable | stale closure cannot be adopted directly; explicit typed scope transfer required | reload session; closure/evidence must be retained |
| EvidenceProjection | ResearchStore/session closure path | ResearchStoreFacade | typed evidence in SessionClosure JSON | can_resolve_obligation and strict closure loader | resolution, Phase 7 | append new retained evidence; closure identity immutable | failed audit, blocked authority, stale scope, missing trusted verifier prevents resolution | reload retained raw/evidence refs and provenance |
| CrossPlaneExecutionBinding | Orchestrator or narrow adapter | Runtime backend validates; owning domain constructs | canonical JSON + explicit columns on jobs/attempts/results/effects | orchestrator current-domain/map/root validators; runtime binding match | RoutedLLMClient, dispatcher, reconciler, effect coordinator | no caller mutation; new binding only | root/map/version/hash/obligation/directive/session/governance mismatch fences result/effect | persisted and revalidated after restart; existing results need validator |
| LogicalJob | Runtime dispatcher/router/effect coordinator | SQLiteRuntimeBackend | logical_jobs row, payload/binding/idempotency | create idempotency, binding consistency, result acceptance | dispatcher/reconciler/effects | state CAS; accepted winner once | idempotency mismatch conflict; no accepted result means no semantic effect | DB is source; reconcile pending/unknown states |
| AttemptIntent / Attempt | RuntimeBackend.create_attempt_intent | SQLiteRuntimeBackend | attempts row + outbox/journal | transition matrix, lease owner/generation/version | dispatcher/reconciler | lease/heartbeat/transition under CAS | expired lease -> orphaned; stale generation/owner cannot record authoritative result | orphan/retry/unknown classification; no live handle import |
| AttemptResult | dispatcher/provider completion | SQLiteRuntimeBackend + artifact store | attempt_results row + registered artifact/manifest | artifact hash, binding validator, lease/generation/state/expiry | accept_result, routed client, reconciler | retain result; semantic authority only if authoritative and compatible | expired/stale/missing artifact -> STALE_FENCED/MISSING_ARTIFACT and not winner | reconcile artifact/results; no provider call is inferred |
| EffectSlot | RuntimeEffectCoordinator | SQLiteRuntimeBackend | unique effect_slots row + domain receipt | exact logical job/effect/target/binding and apply/ack CAS | domain effect functions | apply once; domain_applied then ack | duplicate identity is idempotent; binding mismatch rejected | recover by slot/domain identity; pending saga reconciled |
| RuntimeArtifact / manifest | RuntimeArtifactStore | filesystem + artifact_registry | runtime/artifacts content-addressed body and *.artifact.json | byte hash/size/path containment + SQLite registration | dispatcher/reconciler/Phase7 | write once; no byte replacement | collision/corrupt/missing blocks authority; orphan can be registered only if verified | manifest scan repairs registration or manual-reviews |
| AuditGate | AuditCoordinator/state_machine | AuditCoordinator builds; ProjectStore consumes for PROVED | run/audits/gate.json and embedded fields | AuditGate.passed; specialist/final/dependency/policy checks | orchestrator, Phase7, ProjectStore | new gate per audit; no manual success setter in normal path | failure/inconclusive/errors/identity mismatch block | reload gate and compare bound ClaimSnapshot/hash |
| ArchitectureReviewClock | GovernanceController | GovernanceController | immutable version files + current projection | current map/root/previous hash and trigger logic | review/probe/patch/session effects | only review commit resets counters; effects advance | map/root lineage mismatch requires governance revalidation | load current projection and immutable version |
| Review/Probe/Patch/Critic/Authorization | GovernanceController and typed architecture models | GovernanceController/ResearchStore | research/governance subtrees | exact chain, map/root/hash/scope, critic independence receipt | governed reframe | only AUTHORIZED patch application | stale/invalidation/scope loss/critic failure -> reject/revalidation | reload all chain objects; private capability is not reconstructed |
| RootSynthesis | Phase7Store.synthesize_root | Phase7Store | immutable record + root synthesis body | current snapshot/map/session/gate/evidence/obligation and hashes | FinalConsolidation, PromotionClosure | write once/idempotent exact identity | tampering/hash mismatch or open frontier blocks | load and verify all hashes; orchestrator can resume after durable record |
| FinalConsolidation | Phase7Store.consolidate | Phase7Store | immutable record + final proof bytes + re-audit JSON | root/gate/candidate bytes/re-audit passed | PromotionClosure, truth mutation | write once/idempotent | candidate byte/gate/root mismatch blocks | load final and verify proof/re-audit identity |
| TruthMutationIntent | TruthStoreFacade/runtime effect | TruthStoreFacade | truth/mutations/intents immutable JSON | exact theorem/status/snapshot/gate/audit refs | runtime effect, recovery | prepared once; cannot be edited | source/current mismatch blocks; intent can be recovered only if exact | prepared intent is replayed/recovered under current comparison |
| TruthMutationReceipt | TruthStoreFacade after CAS transition | TruthStoreFacade | immutable receipt JSON | intent hash, before/after snapshot, theorem status/result | Phase7 closure, recovery, audit | one durable receipt per intent | missing/mismatched receipt prevents promotion closure | recover prepared mutation or observe existing receipt; no guess |
| PromotionClosure | Phase7Store.close_promotion | Phase7Store | immutable closure JSON | exact synthesis/consolidation/intent/receipt/current PROVED checks | run state and future resume | write once | any linkage/hash/status mismatch blocks | load closure and verify; resume closes only after durable truth promotion |
| RouteFailureRecord / FailureMap | ProjectStore/orchestrator/campaign | ProjectStore + run directory | failed_routes.json, FAILURE_MAP.json, campaign projections | strict fields, affected theorem/map/obligation | routing, campaign successor, governance review | append/record failure, never semantic truth | stale dependency/replay policy requires re-audit | reload; campaign successor carries logical failure/frontier state |

## Trust boundary diagram

~~~text
provider / model response
        |
        v
strict typed response + WorkerEvent footer
        |
        v
RoutedLLMClient binding validator
        |
        +--> no binding / stale binding --> reject before semantic use
        |
        v
SQLite LogicalJob / AttemptIntent / lease / outbox
        |
        v
artifact body + hash + manifest + AttemptResult
        |
        v
binding + lease/generation + artifact validator
        |
        +--> stale / expired / missing --> retained metadata, no authority
        |
        v
accepted result (one winner)
        |
        v
EffectSlot / RuntimeEffectCoordinator
        |
        +--> research/governance/truth domain-specific validator
        |
        v
SessionClosure / ResearchMap resolution / AuditGate / Phase 7
        |
        v
TruthMutationIntent + current ClaimSnapshot CAS
        |
        v
TruthMutationReceipt
        |
        v
PromotionClosure and PROVED lifecycle transition
~~~

## Direct answers

1. An LLM response can affect semantic state only after typed parsing, current CrossPlaneExecutionBinding validation, durable job/attempt/result acceptance, artifact and lease/generation checks, and a domain-specific RuntimeEffectCoordinator path. A provider response alone is not authorization.
2. Another map/session/generation is prevented by exact root/map/version/hash/obligation/directive/session/governance binding fields, current validators, SQLite CAS/fencing, and effect-slot identity. The current code has explicit public regressions for partial current-domain bindings and no-backend required-binding enforcement in commit 37826ff; independent re-audit is still required.
3. Governance effects require current map/root binding and are recorded through RuntimeEffectCoordinator plus GovernanceController. Destructive reframe additionally requires the durable review -> probe when required -> patch -> critic -> authorization chain. The issued _TrustedGovernedReframe capability itself is in-memory.
4. Root synthesis is authorized by Phase7Store.synthesize_root prerequisites: compatible current snapshot, exact passed gate/hash, current map/root/session, closed obligations, validated retained evidence, candidate/audit refs, and no open frontier.
5. Truth promotion requires FinalConsolidation, a TruthMutationIntent bound to the audited root/gate, current snapshot comparison, passing gate, ProjectStore lifecycle authorization, a durable TruthMutationReceipt, and then PromotionClosure.
6. Fail-closed checks include strict schema/envelopes, content hashes, current binding validators, artifact integrity, lease/generation fencing, gate failure/inconclusive states, missing canonical authority, stale map/root/session, invalid patch authorization, and missing durable receipt.
7. Caller-discipline assumptions remain around using the correct validator adapter, process/file atomicity outside SQLite, provider completion timing, and presentation/demo/test code not being mistaken for production paths. ResearchStoreFacade explicitly describes its ordinary filesystem operation as atomic within one process/filesystem, not a cross-process transaction.
8. ClaimSnapshots, ResearchMaps, runtime jobs/attempts/results/effect slots, manifests, governance chain objects, Phase 7 artifacts, intents, and receipts are reconstructible from durable files/SQLite. The trusted governed-reframe capability, live provider handles, async futures, and process objects are not reconstructed; restart must revalidate or create a successor rather than reuse them.
