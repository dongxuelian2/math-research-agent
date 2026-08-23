# Historical Finding Reconciliation

## Status vocabulary for this pass

This document does not emit CERTIFIED or CLOSED as an independent result. The allowed independent status for every requested finding is NOT_YET_REAUDITED unless the evidence is insufficient to identify the claim; prior historical dispositions are preserved as historical facts.

## Reconciliation table

| Finding | Historical finding and reproducer | Candidate repair commit(s) | Production symbols changed | Tests/probes claiming closure | Current candidate claim | Independent status in this pass |
|---|---|---|---|---|---|---|
| F-002 | Expired lease allowed a late provider result to become authoritative/winner/effect input. Reproducer: pre_root_repair/run_pre_root_repair_probes.py and named X4/F002 terminal-rejection probes; late result after lease expiry. | e0bff5c introduced/fenced stale runtime bindings/lease paths; f9f9a93 preserved runtime authority and terminalized rejected results; later hardening retained the behavior. | runtime_backend.record_result/accept_result; runtime_dispatch.DurableProviderDispatcher.execute; runtime_reconciler; routing.RoutedLLMClient result rejection. | pre_root_blocker_repairs.test_f002_expired_result_is_retained_but_cannot_be_accepted; pre_root_authority_repairs.test_f002_routed_client_cannot_parse_late_rejected_payload and test_f002_rejected_provider_payload_is_terminal_and_non_consumable; LEASE_FENCING_REPAIR_REPORT.md. | final_debug_handoff/CURRENT_STATE.md and FINAL_CANDIDATE_REPORT.md say F-002=CLOSED / locally revalidated; formal certification flags remain NO. | NOT_YET_REAUDITED |
| F-005 | Caller-supplied revision reason could present strategic thesis mutation as HUMAN_STEERING and bypass review -> patch -> critic -> authorization. Reproducer: GOV-THESIS-BYPASS / forged authorization matrix. | e6938fc binds destructive map mutation to durable authority; 30b51b6 requires issued governed-reframe capability; 3bc64de enforces thesis governance and critic independence. | research_store.revise_map; _require_durable_patch_authorization; apply_governed_reframe; GovernanceController.authorize_patch/apply_authorized_patch; ArchitecturePatch/Authorization/Critic. | pre_root_authority_repairs.test_f005_forged_authorization_cannot_mutate_destructive_map; test_architecture_critic_and_authorization; test_phase5_governance_e2e; GOVERNANCE_BYPASS_REPAIR_REPORT.md and F005-A1-A14. | final_debug_handoff says F-005=CLOSED, while preserving this as a local/candidate disposition; no final system certification. | NOT_YET_REAUDITED |
| F-007 | Routed work lacked enough identity to distinguish ClaimSnapshot/map/session/generation, so runtime reconciliation could accept a result for the wrong research context. Earlier complete-binding restart controls were positive but incomplete. | e0bff5c introduced/expanded CrossPlaneExecutionBinding and runtime persistence; f9f9a93 strengthened runtime authority; 37826ff closes the final partial-binding/no-backend variants and adds public-entry tests. | runtime_bindings.CrossPlaneExecutionBinding; runtime_backend job/attempt/result/effect binding columns; runtime_dispatch; runtime_effects; orchestrator._current_execution_binding/_validate_execution_binding/_validate_map_execution_binding; routing._execute_route. | pre_root_blocker_repairs.test_f007_stale_binding_is_fenced_at_acceptance and test_f007_binding_survives_job_attempt_result_and_effect_persistence; pre_root_authority_repairs F007 restart/configured-router tests; pre_root_final_reauthorization X1/X7 and F007-RESTART-CONTROLS. | final_debug_handoff calls F-007=REPAIR_CANDIDATE_CLOSED after 37826ff, but explicitly says the label is not formal certification. | NOT_YET_REAUDITED |
| NF-003 | New finding from final reauthorization: root-only binding was treated as a wildcard while current map/obligation/directive/session context was richer. Reproducer: NF-003-PARTIAL-BINDING in run_final_adversarial_probes.py. | 37826ff adds missing-current-dimension rejection to _validate_execution_binding, explicit _validate_map_execution_binding, and public regression coverage. | orchestrator._validate_execution_binding; orchestrator._validate_map_execution_binding; map-scoped effect call sites; routing._execute_route validator invocation. | pre_root_final_reauthorization/CROSS_PLANE_ADVERSARIAL_RESULTS.md records the pre-repair FAIL; final_debug_handoff/FINAL_CANDIDATE_REPORT.md records NF-003-PARTIAL-BINDING PASS; test_pre_root_authority_repairs.test_f007_current_domain_rejects_root_only_semantic_binding. | final candidate says NF-003_REPAIRED=YES pending independent certification. | NOT_YET_REAUDITED |
| NF-004 | New finding: require_execution_binding was checked only inside the runtime-backend branch, so a no-backend semantic RoutedLLMClient could return output without binding. Reproducer: NF-004-NO-BACKEND-GUARD. | 37826ff moves required-binding validation before the backend/no-backend branch and adds a provider call-count regression. | routing.RoutedLLMClient._execute_route; ModelRouter.require_execution_binding and execution_binding_validator handling. | pre_root_final_reauthorization records the pre-repair FAIL; final_debug_handoff records NF-004-NO-BACKEND-GUARD PASS; test_pre_root_authority_repairs.test_nf004_no_backend_required_binding_rejects_before_provider. | final candidate says NF-004_REPAIRED=YES pending independent certification. | NOT_YET_REAUDITED |

## Evidence chronology

1. The pre-root repair package reports local closures for F-002, F-005, and F-007.
2. The independent pre-root reauthorization at candidate head 3e2b6f4 explicitly denied authorization because F-007 remained open through NF-003 and NF-004.
3. Commit 37826ff changed production routing/orchestrator code and added the two public-entry regressions.
4. The final_debug_handoff then records candidate PASS results, 289 local tests, and deferred hosted/POSIX gates; it also explicitly keeps formal certification flags at NO.
5. This audit starts from the later canonical head 3229aced9fa9bcae41c5ddfea6b6291a6e68d725, but does not rerun the final probes or certify the repairs.

## Code-level reconciliation

The current code contains materially stronger controls than the pre-37826ff candidate:

- ResearchOrchestrator._validate_execution_binding rejects a current map when the supplied binding omits it and rejects omitted current obligation/directive/session/governance dimensions.
- ResearchOrchestrator._validate_map_execution_binding is an explicit narrower validator for map-scoped effects and requires exact current map identity.
- RoutedLLMClient._execute_route evaluates require_execution_binding before the runtime-backend presence branch and refuses a missing binding or validator.
- RuntimeBackend persists binding fields through logical job, attempt, result, and effect records and rechecks them at acceptance/effect preparation.
- ResearchStoreFacade requires a private _TrustedGovernedReframe and reloads the durable review/critic/authorization chain before destructive map changes.
- RuntimeBackend fences expired/stale result ingestion and runtime reconciliation retains but does not authorize late artifacts.

These are code facts about the candidate. They are not evidence that every caller, process boundary, provider path, or restart sequence has been independently attacked.

## Required independent follow-up

For each finding, rerun the historical reproducer and the repaired variant against this exact baseline. Include:

- core CLI and campaign public routes;
- runtime backend reopen/reconcile between every split boundary;
- no-backend and provider-smoke separation;
- concurrent or cross-process result/effect attempts;
- exact current map/session/directive/governance context;
- no Phase 7/truth-promotion execution until the re-audit disposition is explicit.
