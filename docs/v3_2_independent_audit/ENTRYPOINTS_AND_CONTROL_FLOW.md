# Entrypoints and Control Flow

## Entry surface

| Entry path | Source | Starts or resumes | Primary authority boundary |
|---|---|---|---|
| Core CLI | math_research/cli.py: build_parser, dispatch, main | init, theorem/project changes, context, run, formalize, runtime check/reconcile, human transition, failed route, steering | ProjectStore, TruthStoreFacade, RuntimeBackend |
| Module dispatch | math_research/__main__.py | routes argv to core/campaign/demo/benchmark/observatory | command selection only |
| Windows launcher | run_math_agent.ps1 | wraps module dispatch with project/config paths | operational wrapper; no independent semantic authority |
| Campaign CLI | campaign_cli.py: dispatch | create/run/status/stop/resume bounded successors | CampaignStore, StopController, CampaignEngine |
| Python orchestrator API | orchestrator.py: ResearchOrchestrator | normal run or resume; optional stop-after phase | Truth/Research/Runtime/Governance/Phase7 owners |
| Formalization API | formalization.py: run_formalization | typed post-candidate formalization | root-only execution binding; does not mutate theorem truth |
| Benchmark | benchmark.py: run_benchmark | fresh project per case | observed benchmark outcome only |
| Demo | showcase_demo.py | mock project/run | illustrative only |
| Observatory | observatory.py: build_snapshot/run_server | read-only presentation | project-root containment for artifacts |
| Test/debug probes | openprover/tests and retained migration probe scripts | temporary stores and public/private seams | test harness; not production authorization |

## Normal semantic path

The normal run is initiated by cli.py run or CampaignEngine._make_orchestrator and follows this control shape:

~~~text
CLI / CampaignEngine
  -> ProjectStore(project + theorem)
  -> ResearchOrchestrator.__init__
       -> TruthStoreFacade
       -> optional legacy checkpoint inspection/migration
       -> SQLiteRuntimeBackend(project/runtime/control.sqlite3)
       -> RuntimeReconciler
       -> ModelRouter(require_execution_binding=True)
       -> ResearchStoreFacade + GovernanceController + Phase7Store
       -> current canonical authority + ClaimSnapshot
       -> current ResearchMap / obligation / Directive / TacticalSession
  -> ResearchOrchestrator.run
       -> validate snapshot/map/frontier
       -> CONTEXT_READY
       -> candidate_engine / OpenProver planner-worker pipeline
            -> RoutedLLMClient
            -> binding validation before provider/backend branch
            -> LogicalJob -> AttemptIntent -> provider
            -> artifact write/register -> result record -> accept winner
            -> typed WorkerEvent sidecars and PreSubmitGate
       -> CANDIDATE_READY
       -> AuditCoordinator.run_audits
            -> typed specialist/final auditor responses
            -> AuditGate built from local results and policy
       -> optional secondary verification
       -> completion task / session close
            -> typed EvidenceProjection + SessionClosure
            -> ResearchStore resolution/revision
       -> _finalize
            -> authority_promotion_decision
            -> Phase 7 root synthesis/consolidation/truth mutation path
~~~

### Binding validation in the normal path

ResearchOrchestrator._current_execution_binding captures the current root ClaimSnapshot and, when available, ResearchMap id/version/hash, obligation, Directive, TacticalSession, and governance identity. The normal validator is _validate_execution_binding. It rejects a missing binding, stale root, missing current map, missing current dimensions, and mismatched values.

RoutedLLMClient._execute_route in routing.py applies require_execution_binding before selecting the runtime-backend/no-backend branch. It requires both a binding and a binding validator, calls the validator, then either enters durable dispatch or rejects the semantic route. The provider is not called for a rejected binding.

RuntimeBackend and RuntimeDispatcher then enforce their own binding, artifact, lease/generation, idempotency, and result-acceptance checks. The Router is a transport/compute coordinator; it does not own ResearchMap strategy.

## Governance effect path

Governance effects are not ordinary theorem candidate outputs.

~~~text
ResearchOrchestrator / GovernanceController signal
  -> current ResearchMap + current root snapshot
  -> map-scoped CrossPlaneExecutionBinding
  -> RuntimeEffectCoordinator.register_semantic_result
       -> durable logical job/result
       -> RuntimeEffectCoordinator.apply_domain_effect
            -> EffectSlot unique idempotency
            -> GovernanceController.record_effect/session/route_failure
            -> immutable governance artifact + review clock projection
~~~

Review and destructive-reframe path:

~~~text
review trigger / due clock
  -> ArchitectureReview
  -> optional StructuralProbePlan + StructuralProbe
  -> ArchitecturePatch
  -> independent ArchitectureCritic + IndependenceReceipt
  -> PatchAuthorization(status=AUTHORIZED)
  -> ResearchStoreFacade.apply_governed_reframe
       -> private _TrustedGovernedReframe capability
       -> immutable ResearchMap version
       -> scope-transfer and root checks
  -> ArchitecturePatchApplication
  -> governance clock/map projections
~~~

GovernanceController._validate_review_bindings and _validate_effect bind review/effect artifacts to the current ResearchMap and root. GovernanceController.apply_authorized_patch refuses non-AUTHORIZED status. The private capability is an in-memory anti-forgery guard, not a durable token; the durable chain is reloaded and revalidated before issuing it.

## Root / truth mutation path

Root and truth paths are distinct from normal provider result ingestion.

~~~text
AuditGate passed + SessionClosure completed
  -> ResearchOrchestrator._finalize
  -> Phase7Store.synthesize_root
       prerequisites:
       current ClaimSnapshot compatible
       exact gate hash
       current ResearchMap/root identity
       closed obligations and validated evidence
       retained candidate/audit artifacts
       no open/nonterminal map obligations
       -> immutable RootSynthesis record + body
  -> Phase7Store.consolidate
       exact root/gate/candidate identity
       final proof bytes and manifest
       consolidation re-audit passed
       -> immutable FinalConsolidation + re-audit
  -> TruthMutationIntent
       root-only current ClaimSnapshot binding
       audit artifact refs
       expected theorem status
  -> RuntimeEffectCoordinator.apply_truth_transition
       -> TruthStoreFacade.compare_and_transition
            current snapshot comparison under ProjectStore truth_transaction
            expected status + exact identity + passing AuditGate
            -> theorem status transition to PROVED
            -> resulting ClaimSnapshot
            -> immutable TruthMutationReceipt
  -> Phase7Store.close_promotion
       exact audited/resulting snapshots
       synthesis/consolidation hashes
       durable receipt
       resulting status PROVED
       -> PromotionClosure and state PROMOTION_CLOSED
~~~

Code evidence: orchestrator.py _finalize constructs the root, consolidation, intent, truth effect, and promotion closure in sequence; phase7.py separates RootSynthesis, FinalConsolidation, and PromotionClosure loaders and verifies hashes/linkage; state_machine.py permits PROVED only for Archivist with a passing AuditGate; truth_store.py performs the current snapshot comparison and durable receipt write.

This pass did not call _finalize, Phase7Store.synthesize_root, Phase7Store.consolidate, TruthStoreFacade.compare_and_transition, or Phase7Store.close_promotion.

## Recovery and resume path

~~~text
CLI run --resume / CampaignEngine / orchestrator resume
  -> resolve run directory and load state.json
  -> inspect legacy state when runtime DB is absent
       INCOMPATIBLE -> reject
       ARCHIVE_ONLY -> do not drive current state
       REVALIDATION_REQUIRED -> current validation required
       DIRECT_IMPORT -> only if all direct-import checks pass
  -> open SQLiteRuntimeBackend
  -> reconcile expired leases, outbox, artifact manifests, results, accepted winners
  -> reload canonical authority and ClaimSnapshot
  -> reload or rebase ResearchMap / Directive / TacticalSession
  -> validate current execution binding and phase
  -> continue, checkpoint, or fail closed
~~~

CampaignStore.resume validates stored ClaimSnapshot, ResearchMap projection, and governance clock. CampaignEngine creates a successor run rather than reopening a terminal run and carries only serialized logical state. StopController provides checkpointed stop/resume semantics.

Key recovery guards:

- expired leases are orphaned and late results are retained as STALE_FENCED;
- unknown dispatched executions go to durable unknown/manual-review states;
- missing artifacts block the attempt/job;
- existing manifests/results require a trusted binding validator before ingestion/acceptance;
- rejected provider results are terminal/non-consumable for routed semantic callers;
- Phase 7 state can resume after durable truth promotion by loading synthesis/consolidation/receipt artifacts and closing the remaining closure record.

## Auxiliary paths

### Formalization

formalize -> load candidate/context -> load/capture ClaimSnapshot -> construct root-only CrossPlaneExecutionBinding -> ModelRouter with required binding -> typed FormalizationResultSchema -> formalization JSON. It is an evidence lane and explicitly does not change theorem truth.

### Provider smoke

provider-smoke -> config/provider client -> one DurableProviderDispatcher job -> archive/summary. It is diagnostic and intentionally not the normal semantic route. The distinction matters when auditing require_execution_binding.

### Runtime check and reconcile

runtime-check -> SQLiteRuntimeBackend.check and table counts.

reconcile -> SQLiteRuntimeBackend.reconcile -> RuntimeReconciler actions. Reconciliation can repair projections or block/manual-review; it does not invent provider completion or semantic truth.

### Observatory

observatory -> build_snapshot reads project/index/runs/events/usage/formal/provenance and serves a local HTTP view. Artifact reads are constrained to project root. It is presentation only.

### Test/debug paths

The tests construct temporary ProjectStore, SQLiteRuntimeBackend, ModelRouter, ResearchStoreFacade, and orchestrators. Retained pre-root scripts run named adversarial cases. These paths may call private validators to isolate a finding; public-entry tests in test_pre_root_authority_repairs.py are more representative for NF-003/NF-004. Neither is a production authority path.

## Terminal states and transitions

There are two interacting state vocabularies:

1. Project theorem lifecycle: OPEN, IN_RESEARCH, PARTIAL, BLOCKED, REJECTED, PROVED and related statuses in state_machine.py.
2. Run orchestration phases: CREATED, CONTEXT_READY, CANDIDATE_READY, AUDITS_READY, COMPLETE, CHECKPOINT, plus Phase 7 state strings persisted in state.json.

ProjectStore._transition_locked owns theorem lifecycle validation. Orchestrator owns run/Phase 7 sequencing. The distinct vocabularies are intentional but create a phase-semantics audit target; state/status/phase projections must be checked for drift.
