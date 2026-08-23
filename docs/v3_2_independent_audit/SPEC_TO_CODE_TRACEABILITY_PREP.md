# Specification-to-Code Traceability Preparation

## Availability result

AUTHORITATIVE_V3_2_SPEC_AVAILABLE=NO

No physical copy of the authoritative freeze specification was found in the canonical Agent repository or the retained workspace search scope. The exact external source required to complete normative traceability is:

- Harness_v3_2_合并架构与冻结规范.md

A path, immutable copy, or content hash of that owner-supplied document is required before any SPEC_REQUIREMENT field can be populated. No normative requirements are invented here.

## Candidate source register

| Source | Classification | Use in this preparation | Authority limitation |
|---|---|---|---|
| docs/v3_2_migration/V3_2_ARCHITECTURE_DECISIONS.md | IMPLEMENTATION_HANDOFF | records candidate plane/freeze decisions and intended boundaries | explicitly says implementation is not FROZEN; references a supplied spec that is not present |
| docs/v3_2_migration/V3_2_IMPLEMENTATION_MATRIX.md | IMPLEMENTATION_HANDOFF | maps candidate implementation slices and prior finding dispositions | matrix is implementation status/history, not normative requirements; earlier Phase 7 rows differ from later code |
| docs/v3_2_migration/V3_2_MIGRATION_REPORT.md | HISTORICAL_AUDIT | records phase/migration history and local acceptance limitations | says independent certification/hosted validation remain separate |
| docs/v3_2_migration/pre_root_audit/* | HISTORICAL_AUDIT / TEST_EVIDENCE | prior audit boundaries, ownership, stale-state, effect, and bypass observations | historical scope and prior candidate head; not final normative text |
| docs/v3_2_migration/pre_root_repair/* | CANDIDATE_SELF_CERTIFICATION / TEST_EVIDENCE | repair reports and named regressions for F-002/F-005/F-007 | implementer-authored repair evidence; labels such as CLOSED are not accepted as independent certification |
| docs/v3_2_migration/pre_root_reaudit/* | HISTORICAL_AUDIT / TEST_EVIDENCE | prior re-audit and new NF-003/NF-004 finding history | its final reauthorization explicitly denied Phase 7 and left F-007 open at the earlier candidate head |
| docs/v3_2_migration/pre_root_final_reauthorization/* | HISTORICAL_AUDIT | explicit final disposition and public reproducers | evidence is bound to 3e2b6f4, before the later 37826ff repair |
| docs/v3_2_migration/final_debug_handoff/* | CANDIDATE_SELF_CERTIFICATION | later candidate repair handoff, Phase 7 report, test matrix, and deferred gates | explicitly keeps formal certification flags NO and is not a normative specification |
| openprover/openprover/math_research/* | IMPLEMENTATION | actual production symbols and persistence contracts | code defines behavior, not what the missing spec normatively required |
| openprover/tests/* | TEST_EVIDENCE | observed regression/positive/negative behavior | tests are evidence at named seams, not normative requirements |
| README.md, docs/ARCHITECTURE.md, docs/ADR-0001-project-layer.md | IMPLEMENTATION_HANDOFF / UNKNOWN design context | broad product/project intent | not identified as the v3.2 authoritative freeze document |

Searches included exact and variant terms for Harness v3.2, 合并架构与冻结规范, v3_2, RootSynthesis, Coverage, and VerifierIndependenceReceipt. No exact authoritative file was found.

## Implementation concept map

Every row intentionally leaves the normative side pending.

| NORMATIVE_SPEC_REFERENCE | Implementation concept observed | PRODUCTION_MODULE -> SYMBOL | STATE / RECEIPT | TEST / CURRENT EVIDENCE | GAP for next pass |
|---|---|---|---|---|---|
| PENDING | Separate Truth, Research, Execution, Artifact, Governance planes | orchestrator.ResearchOrchestrator.__init__; truth_store.TruthStoreFacade; research_store.ResearchStoreFacade; runtime_backend.SQLiteRuntimeBackend; runtime_artifacts.RuntimeArtifactStore; governance.GovernanceController | state.json, truth snapshots/receipts, research maps, runtime/control.sqlite3, artifact manifests, governance versions | architecture decisions and phase reports; unit/integration coverage across all planes | obtain normative plane definitions and prove no omitted owner or cross-plane mutation |
| PENDING | Assertion/root identity | truth_identity; claim_snapshot.ClaimSnapshot and compare_claim_snapshots; truth_store validators | AssertionIdentity, ClaimSnapshot, AuthorityBinding, snapshot comparison | test_truth_identity, test_truth_store_facade, test_truth_mutation | map exact required identity dimensions and race semantics to the spec |
| PENDING | Research frontier and scope preservation | research_map.ResearchMap/Rebase; research_store.revise_map/apply_governed_reframe | immutable map version/hash, obligation refs, rebase record | test_research_map_and_obligations, governance tests | determine normative scope/coverage requirements and whether all open dimensions are represented |
| PENDING | Tactical execution envelope | directive.Directive/TacticalSession; research_evidence.SessionClosure | map/root/obligation/directive/session hashes, raw/typed evidence refs | test_directive_projection, test_session_closure, production wiring tests | identify required session lifecycle/closure receipt and independent verifier semantics |
| PENDING | Typed worker/verifier/audit evidence | schemas.WorkerEventSchema/AuditResultSchema; openprover_adapter; audit_coordinator | worker sidecars, audits/*.json, gate.json, provider provenance | test_worker_event_production_wiring, test_audit_protocol_v2 | obtain normative schema and coverage-accounting rules; check provider hash identity handling |
| PENDING | Coverage completeness | ResearchMap obligation refs, AuditGate booleans, Phase7Store obligation/evidence checks | open_obligation_ids, closed obligations, EvidenceProjection, audit refs | test_research_map_and_obligations, test_phase7_implementation | no clearly named Coverage object or normative coverage ledger was found; spec required |
| PENDING | Runtime identity/fencing | runtime_bindings.CrossPlaneExecutionBinding; runtime_backend; runtime_dispatch | job/attempt/result/effect binding columns, leases, generations, journal | test_durable_runtime, pre-root repair tests, final candidate repair tests | map exact required dimensions, generation semantics, and provider completion policy |
| PENDING | Governance authorization | governance.GovernanceController; architecture_patch; architecture_critic | review clock, review/probe/patch/critic/authorization/application records | test_architecture_critic_and_authorization, test_phase5_governance_e2e | confirm normative independence/authorization receipt requirements |
| PENDING | Root synthesis | phase7.RootSynthesis; Phase7Store.synthesize_root/load_root_synthesis | root record/body, exact gate/map/root/session/evidence refs | test_phase7_implementation; Phase 7 handoff | exact manifest completeness and root coverage requirements cannot be judged without spec |
| PENDING | Final consolidation/re-audit | phase7.FinalConsolidation; Phase7Store.consolidate | final proof, proof hash, consolidation re-audit | test_phase7_implementation | determine required independent final verifier and re-audit identity |
| PENDING | VerifierIndependenceReceipt | ArchitectureCriticIndependenceReceipt exists for architecture governance; no direct mathematical VerifierIndependenceReceipt symbol was found | ArchitectureCritic receipt only; audit coordinator stores provider provenance/role results | test_architecture_critic_and_authorization; audit tests | authoritative requirement and expected persistence/issuer/independence semantics unknown |
| PENDING | Truth promotion | truth_mutation; truth_store.compare_and_transition; ProjectStore._transition_locked; runtime_effects.apply_truth_transition | TruthMutationIntent/Receipt, before/after snapshots, gate hash, EffectSlot, PromotionClosure | test_truth_mutation, test_state_machine, test_phase7_implementation | map normative authorization order, process boundary, and reopen semantics |
| PENDING | Recovery | checkpoint_migration, runtime_reconciler, orchestrator resume, campaign resume | migration classification, reconciliation actions, successor projections | test_checkpoint_migration, test_durable_runtime, test_production_async_integration | spec-level recovery guarantees, crash points, and no-fabrication requirements pending |
| PENDING | Platform behavior | process_control, codex_cli_provider, scripts, CI | subprocess/process-tree and checkpoint state | test_interrupt_race; CI Windows/Ubuntu; handoff records Windows success/POSIX interrupt not executed | exact platform guarantees and hosted evidence pending |

## Completion rule

After the authoritative document is supplied, replace each PENDING row with:

SPEC_REQUIREMENT -> PRODUCTION_MODULE -> SYMBOL -> STATE/RECEIPT -> TEST -> CURRENT_EVIDENCE -> GAP

The supplied document must be pinned by path and hash; earlier handoff prose must remain a separate evidence class.
