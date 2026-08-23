# Test Surface Map

## Reading rule

This is an inventory of existing test evidence at baseline, not a rerun and not a certification. No tests were executed during this audit pass. The recorded 289-test result in final_debug_handoff is candidate evidence; earlier packages contain different counts and even explicit failed/open finding matrices.

## Finding-focused coverage

| Concern | Production symbols exercised | Test files / named tests | Positive and negative evidence | Dependency / platform profile | Gap |
|---|---|---|---|---|---|
| F-002 expired lease / late result | runtime_backend.record_result, accept_result, runtime_dispatch.DurableProviderDispatcher.execute, routing.RoutedLLMClient._execute_route, runtime_reconciler | test_pre_root_blocker_repairs: test_f002_expired_result_is_retained_but_cannot_be_accepted; test_pre_root_authority_repairs: test_f002_routed_client_cannot_parse_late_rejected_payload, test_f002_rejected_provider_payload_is_terminal_and_non_consumable; test_durable_runtime | Positive non-expired dispatch and acceptance; negative expired/late/terminal rejection and no effect slot | Temporary SQLite/filesystem, mock provider, injected sleeps/faults | Provider completion timestamp is intentionally unknown; multi-process late result and filesystem crash around result/artifact registration remain unproven |
| F-005 forged strategic-thesis authority | research_store.revise_map, apply_governed_reframe, GovernanceController.authorize_patch/apply_authorized_patch, ArchitectureCritic | test_pre_root_authority_repairs: test_f005_forged_authorization_cannot_mutate_destructive_map; test_architecture_critic_and_authorization; test_phase5_governance_e2e | Positive authorized governed reframe; negative raw/cloned/mutated/cross-map/wrong-scope authorization | Temporary JSON stores; typed models; no external service | Independent verifier identity/receipt semantics for all governance actors require re-audit; cross-process capability/replay not exercised |
| F-007 routed cross-plane bindings | runtime_bindings.CrossPlaneExecutionBinding, RuntimeBackend binding columns, RoutedLLMClient, RuntimeEffectCoordinator | test_pre_root_blocker_repairs: test_f007_stale_binding_is_fenced_at_acceptance, test_f007_binding_survives_job_attempt_result_and_effect_persistence; test_pre_root_authority_repairs restart/configured router tests | Positive exact binding round trip; negative stale/missing validator and mismatched current binding | Temporary SQLite + mock provider; restart simulated by reopening backend | Exact completeness for every public semantic entry and concurrent restart are not independently demonstrated by these component tests |
| NF-003 partial current-domain binding | ResearchOrchestrator._validate_execution_binding, _current_execution_binding, RoutedLLMClient._execute_route | test_pre_root_authority_repairs.test_f007_current_domain_rejects_root_only_semantic_binding | Positive complete current binding; negative root-only binding must reject before provider | Temporary ProjectStore/orchestrator, mock provider with call counter | Test uses private orchestrator validator to set up current context; independent audit should repeat through CLI/campaign/public normal path and restart reconciler |
| NF-004 no-backend semantic guard | ModelRouter(require_execution_binding=True), RoutedLLMClient._execute_route | test_pre_root_authority_repairs.test_nf004_no_backend_required_binding_rejects_before_provider | Negative required-binding/no-backend route rejects before provider; transport provider-smoke is a separate path | Mock provider, no SQLite backend | Public API distinction between diagnostic transport and semantic routing should be audited for all callers, not only this test |

## Concern-oriented inventory

| Concern | Production symbols / files | Existing tests | Coverage character | Gaps |
|---|---|---|---|---|
| Routing and provider selection | routing.py ModelRouter/RoutedLLMClient; providers.py; provider-specific clients | test_heterogeneous_routing, test_openai_provider, test_codex_cli_provider, test_gemini_observatory, test_literature_trust | mocked clients and provider error/config paths; some real process behavior for Codex client | network/provider reality, secret/redaction edge cases, fallback authority, and all provider-specific binding orders |
| Typed response boundary | schemas.py parsers; openprover_adapter.py | test_worker_event_production_wiring, test_audit_protocol_v2, test_certification | strict JSON/footer/sidecar positives and malformed/missing negatives | schema version migration, adversarial Unicode/duplicate-field behavior, provider native structured-output differences |
| Runtime backend | runtime_model.py, runtime_backend.py, runtime_dispatch.py | test_durable_runtime, test_pre_root_blocker_repairs | transitions, leases, generations, outbox, result winner, effect slot, artifact registration | multi-process contention, SQLite/WAL durability under host power loss, clock skew, provider completion ambiguity |
| Restart and recovery | runtime_reconciler.py, checkpoint_migration.py, campaign.py, orchestrator resume | test_checkpoint_migration, test_durable_runtime, test_production_async_integration, test_hard_gate_and_replay, test_phase7_implementation | reopen backend/reload JSON and recover selected split states | actual subprocess restart at each split, concurrent process recovery, legacy states from every historical schema |
| State persistence | project.py, research_store.py, truth_store.py, campaign.py | test_project_and_retrieval, test_research_map_and_obligations, test_truth_store_facade, test_truth_mutation, test_campaign_cli | temporary filesystem round trips and immutable identity checks | fsync/rename durability, JSON/SQLite atomicity across processes, partial file writes |
| Late/stale results | runtime_backend, dispatcher, reconciler, routing | test_durable_runtime, pre-root blocker/authority repair tests | negative stale lease/generation/binding and result terminality | late result after Phase 7 artifact or truth mutation; provider callbacks after process death |
| Cross-map attempts | ResearchMap, ResearchStoreFacade, runtime_bindings validators | test_research_map_and_obligations, pre_root repairs, test_truth_identity | stale root/map/rebase and exact binding cases | broad public entry-point matrix across campaigns/formalization/observatory and concurrent map revisions |
| Cross-session attempts | Directive/TacticalSession/SessionClosure, runtime bindings | test_session_closure, test_directive_projection, pre_root blocker | stale closure/scope transfer/evidence retention | restart at session-close boundary; raw artifact manipulation; session id collision |
| Forged authority | architecture patch/critic/governance/store | test_architecture_critic_and_authorization, test_phase5_governance_e2e, pre-root authority repairs | positive authorized chain and negative forged/cloned authority | no external independent critic; private capability is in-memory; serialization/replay across processes |
| Root synthesis | phase7.py, orchestrator._finalize | test_phase7_implementation | positive normal/recovery path and negative stale/open/tampered inputs | exact coverage accounting, every obligation/evidence class, partial body/manifest writes, external independent final verifier |
| Truth promotion | truth_store.py, truth_mutation.py, project.py, runtime_effects.py | test_truth_mutation, test_truth_store_facade, test_state_machine, test_phase7_implementation | CAS/race/receipt recovery, lifecycle gate, tamper tests | multi-process CAS, receipt/file crash sequence, authority-source mutation between consolidation and promotion |
| Phase 7 state | orchestrator.py and phase7.py | test_phase7_implementation | success, restart after promotion, stale root/final proof | all phase state strings, status projection drift, partial root/consolidation restarts |
| Interrupt/process behavior | process_control.py, codex_cli_provider.py, scheduler/pipelines | test_interrupt_race, test_production_async_integration, test_codex_cli_provider | Windows subprocess tree and async cancellation evidence recorded | POSIX host run was not executed; filesystem/database crash is different from Ctrl-C interruption |
| Windows-specific behavior | process_control, Codex client, launcher | test_interrupt_race, CI Windows job, recorded Windows interrupt evidence | Windows process-tree/launcher checks | not a substitute for Linux/POSIX process semantics |
| POSIX-specific behavior | process_control, shell scripts, async runtime | CI Ubuntu job and shell scripts are present | workflow declares Ubuntu coverage | retained handoff explicitly says POSIX interrupt host run not executed; independent run required |
| Canonical authority | canonical_artifacts.py, trust_kernel.py, orchestrator | test_canonical_artifact_authority, test_trust_kernel, test_checkpoint_migration | missing/ambiguous/hash mismatch and registry/source hash checks | external corpus availability, authoritative spec unavailable, source path ambiguity under real deployment |
| Governance clock/effects | governance.py, structural_effect.py, structural_probe.py | test_structural_effect_and_review_clock, test_architecture_review_and_probe, test_phase5_governance_e2e | review due/clock reset/effect/session/route failure and scope loss | concurrent governance writers, projection corruption, cross-process clock reset |
| Literature/research plane | pipelines.py, scholarly.py, literature.py, research_store.py | test_async_literature_pipelines, test_scholarly_adapter, test_external_applicability_trust, test_research_map_and_obligations | mock/adaptor/cache/evidence and task lifecycle | live scholarly service behavior, source authority semantics, coverage completeness |
| Campaign/successors | campaign.py, campaign_cli.py, scheduler.StopController | test_campaign_cli, test_hard_gate_and_replay, test_scheduler_and_profiles | creation/profile/stop/checkpoint/successor/replay policy | concurrent campaign process, successor around provider unknown execution, JSON atomicity |
| Formalization | formalization.py, routing.py, schemas.py | test_certification and provider/routing tests | typed formalization success/failure path and root-only binding | actual Lean compiler/tool availability, interaction with Phase 7 authority, no-truth-mutation guarantee under crash |
| Observatory/presentation | observatory.py | test_gemini_observatory | snapshot and HTTP containment/presentation | stale/partial file reads, artifact exposure under concurrent deletion, presentation accidentally treated as authority |

## Positive versus adversarial balance

Positive tests dominate ordinary project, pipeline, provider, and Phase 7 flows. Adversarial tests are concentrated in:

- pre-root blocker/authority repairs;
- truth identity/mutation;
- durable runtime;
- research-map/rebase and session closure;
- architecture governance;
- Phase 7 tamper/stale-input tests.

That concentration is useful but creates a coverage-accounting question: an all-green test suite can still miss a public path that skips the validator used by the focused test.

## Mocked versus real dependencies

Most tests use:

- temporary directories;
- JSON files rather than a deployed project;
- SQLite in one process;
- mock provider clients;
- synthetic provider payloads;
- monkeypatching/sleeps/fault injectors.

The recorded CI and interrupt evidence adds real Windows subprocess behavior and locked dependency/lint/compile checks. No test here independently proves external model behavior, hosted Linux execution, POSIX interruption, filesystem power-loss durability, or an authoritative v3.2 specification mapping.

## Test-only entry points and caller discipline

Several tests intentionally call private methods such as _validate_execution_binding, _ensure_research_plane_ready, or runtime internals to isolate a state. That is appropriate for diagnosis but not equivalent to a public route. Tests that create actual orchestrator/provider wiring are stronger for production-path claims. The next audit should require each finding reproducer to run through the public entry path first, then use private seams only to localize the failing branch.

## Evidence interpretation

A passing test demonstrates the observed input/output and persistence assertions in that test. It does not prove:

- the test reached every caller;
- the chosen validator was the one used in production;
- another process cannot race it;
- a different provider/path cannot bypass it;
- a historical self-certification label is correct;
- the missing normative specification requirement has been satisfied.
