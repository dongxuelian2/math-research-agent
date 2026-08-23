# Persistence and Recovery

## Persistence inventory

| State | Location / schema | Write path | Atomicity and authority notes |
|---|---|---|---|
| Project metadata and theorem truth | project.json, theorems/*.json, index.json | ProjectStore._write_json, save_project, update_theorem, _transition_locked | temp-file replace is used, but project.py does not fsync the file or parent directory; truth_transaction is an in-process RLock, not a cross-process transaction |
| Premises and dependency indexes | premises/*.json, premises/index.json, index.json | ProjectStore premise/theorem/index methods | rebuilt projections can be regenerated; theorem status remains ProjectStore authority |
| ClaimSnapshots | project/truth/claim_snapshots or truth/ content-addressed JSON | TruthStoreFacade capture/load | immutable/hash-addressed; current theorem/authority/trust/policy is revalidated on use |
| Truth mutation intents | truth/mutations/intents | TruthStoreFacade.compare_and_transition and RuntimeEffectCoordinator | prepared intent includes before/audited snapshot, gate, expected status and audit refs; recovery refuses mismatched intent/source |
| Truth mutation receipts | truth/mutations/receipts | TruthStoreFacade after ProjectStore CAS transition | immutable receipt binds intent hash, before/after snapshots, transition result; promotion closure requires it |
| ResearchMap versions/current projections | research/maps and research/current | ResearchStoreFacade | immutable map versions plus derived projections; map is frontier authority, not theorem truth |
| Obligations/directives/sessions | research/obligations, directives, tactical_sessions, session_closures | ResearchStoreFacade | strict immutable identities; raw artifacts are retained before closure; stale closure requires typed transfer/revalidation |
| Research evidence | research/evidence/closures/indexes and retained raw artifacts | ResearchStoreFacade.close_session/evaluate/resolve | validated evidence must reference retained raw bytes and exact map/root/session scope |
| Governance | research/governance/{review_clocks,architecture_reviews,structural_probes,architecture_patches,critics,patch_authorizations,applications,structural_effects} | GovernanceController | immutable versioned records plus current/control projections; current map/root is checked for every effect |
| Run state | runs/<run_id>/state.json and state projections | ResearchOrchestrator._checkpoint and component writers | broad orchestration projection; not the sole authority for truth/runtime results |
| Candidate/audit evidence | runs/<run_id>/CANDIDATE_PROOF.md, context, audits, archives, metrics, pipeline/routing/usage | CandidateEngine, AuditCoordinator, pipelines, router | raw/provenance retained; typed schemas and gate are the semantic boundary, not Markdown alone |
| Runtime control plane | project/runtime/control.sqlite3 | SQLiteRuntimeBackend | WAL, synchronous FULL by default, migrations, transition journal, CAS/version/generation fencing; primary authority for execution state |
| Runtime artifacts | project/runtime/artifacts/<kind>/<artifact_id> plus *.artifact.json | RuntimeArtifactStore | body/manifest write with fsync and replace before SQLite registration; hash/size/path are checked |
| Campaign | campaigns/<campaign_id>.json | CampaignStore | JSON atomic replacement but no cross-process transaction; successor lineage stores logical state, not live handles |
| Phase 7 | phase7/root_synthesis, root_synthesis_bodies, final_consolidations, final_proofs, consolidation_reaudits, promotion_closures | Phase7Store | immutable digest-named records and body/proof/re-audit linkage; loaders recompute and verify hashes |
| Demo/benchmark | project directories under benchmark output; tracked projects/demo fixtures | benchmark/demo | observed outcomes, not authority for the canonical repository |

## Runtime database and artifact saga

The durable execution sequence is intentionally split:

~~~text
1. SQLite create LogicalJob (idempotency + binding)
2. SQLite create AttemptIntent + READY outbox row atomically
3. claim outbox / lease / generation
4. transition attempt to RUNNING
5. invoke provider (at-least-once delivery)
6. write content-addressed body + fsync + manifest
7. register artifact in SQLite
8. record result with lease/generation/binding validator
9. accept one authoritative compatible result
10. prepare unique EffectSlot
11. apply domain effect
12. mark domain applied
13. acknowledge effect/outbox and complete job
~~~

A crash can leave any split state. RuntimeReconciler handles expired leases, stale outbox claims, unknown dispatched execution, orphan verified manifests, missing artifacts, pending result acceptance, and effect recovery. It does not infer provider completion time, fabricate attempts/leases, accept an existing result without a binding validator, or turn a missing artifact into semantic authority.

## Resume and checkpoint reconstruction

ResearchOrchestrator.__init__ reconstructs from:

- run directory and state.json;
- optional legacy checkpoint migration classification;
- project/runtime/control.sqlite3;
- canonical authority resolution and immutable cache;
- ClaimSnapshot and current truth;
- current ResearchMap plus obligations;
- Directive/TacticalSession and session closure when present;
- governance clock/control projection;
- pipeline and router projections.

Resume rejects stale/unknown snapshots, missing current maps, invalid canonical authority, incompatible legacy state, stale task bindings, and Phase 7 closure mismatches. CampaignStore.resume independently checks the stored snapshot, map projection, and governance clock; CampaignEngine creates a successor after rejection rather than reopening an immutable terminal run.

## Stale, late, rejected, and partial-result handling

- An expired lease result is retained as provenance but recorded STALE_FENCED and cannot become an accepted winner or EffectSlot input.
- A stale generation/owner result is fenced by CAS/generation checks.
- A provider result with missing/corrupt artifact is marked non-authoritative and can block the attempt/job.
- A dispatched attempt without durable result is classified UNKNOWN_EXECUTION/manual review rather than silently redelivered in the same semantic identity.
- Routed semantic consumers reject non-authoritative/non-accepted results and do not expose the provider body as a successful semantic response.
- A failed normal run writes a FailureMap and route failures, closes the research session, and records research/governance effects through the runtime effect coordinator.
- A Phase 7 run with durable root/consolidation/truth promotion can resume by loading immutable artifacts; a partial root or consolidation remains blocked until exact prerequisites revalidate.

## Phase 7 recovery

Phase7Store loaders verify:

- file naming digest and record hash;
- body hash and candidate proof bytes;
- exact root, map, ClaimSnapshot, SessionClosure, gate, and audit references;
- FinalConsolidation re-audit identity and passed status;
- PromotionClosure intent/receipt linkage and resulting PROVED state.

Orchestrator._verify_phase7_completion checks a persisted closure. _resume_phase7_after_truth_promotion loads root synthesis, final consolidation, intent, receipt, closes the promotion closure, marks state PROMOTION_CLOSED, and only then returns complete/proved. This pass did not invoke these operations.

## Atomicity and reconstruction risks

### SQLite versus JSON

SQLite has an explicit transaction/journal/CAS model. ProjectStore, CampaignStore, governance projections, and several run-state writers rely on JSON temp replacement, in-process locks, or projection writes. The code comments and facade documentation do not establish a cross-process transaction spanning JSON, SQLite, and domain stores.

### In-memory-only security/authority

- ResearchStoreFacade._TrustedGovernedReframe is an in-memory capability issued only with a private token after durable chain checks. It is deliberately not a serialized bearer token.
- Live provider clients, subprocess handles, async futures, cancellation handles, and thread/process objects are not restart state.
- ModelRouter and validators are reconstructed from current code/config/state; stored runtime results need a current binding validator to be admitted.
- StopController requests are durable, but acknowledgement and checkpoint state still depend on JSON updates.
- The normal ProjectStore truth lock is an in-process RLock; separate processes can race unless the surrounding runtime/effect path prevents them.
- Mock provider behavior is deterministic test infrastructure, not external-provider evidence.

### Reconstructed rather than persisted

- current map/directive/session objects may be reloaded from immutable records and projections;
- provider clients and model router are rebuilt;
- pipeline queues and scheduler state are serialized logically, while live tasks are discarded;
- governance clock current projection is rebuilt/synchronized from immutable versions;
- artifact registry projections can be repaired from verified manifests;
- legacy runtime state can be imported conservatively, but it cannot fabricate a current runtime ontology when required bodies/provenance are absent.

## Recovery evidence boundary

Existing tests cover many modeled fault points and the handoff records local recovery results. This pass did not rerun them and does not infer crash safety from their presence. Independent audit should inject process/filesystem interruption at the JSON/SQLite/artifact/EffectSlot boundaries, including concurrent processes, because those are distinct from the single-process unit-test boundaries.
