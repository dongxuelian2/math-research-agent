# Phase 6 Runtime Path Audit

Date: 2026-08-20  
Branch: `codex/v3-2-reconciliation`  
Audited baseline: `bdab5cff50227a1504208359539bfe7dba5e7bc2`

## 1. Scope and audit rule

This report records the production runtime ownership and crash boundaries that exist before the Phase 6 durable-runtime implementation. It was written before any production runtime refactor. The governing boundary is the Harness v3.2 merge/freeze specification and the Phase 3–5 evidence already committed in this repository.

The audit traced these paths:

- `ResearchOrchestrator`, `CampaignEngine`, and `CampaignStore`
- `AsyncDAGScheduler`, `AsynchronousPipelineRuntime`, and `AtomicResourceBudget`
- `ModelRouter`, `RoutedLLMClient`, provider clients, retry, and fallback
- `CandidateEngine`, `AuditCoordinator`, and `ResearchPolicy`
- `TruthStoreFacade`, `ResearchStoreFacade`, and `GovernanceController`
- run/campaign checkpoint and resume
- filesystem artifact writers and atomic-replace helpers
- subprocess interruption and `tests/test_interrupt_race.py`

This is a description of the current implementation, not a claim that its behavior already satisfies Phase 6.

## 2. Current ownership map

| Concern | Current owner | Current durable representation | Audit finding |
|---|---|---|---|
| Run phase/status | `ResearchOrchestrator` | `runs/<run>/state.json` | JSON checkpoint is used as execution authority. `_write_json` writes directly to the destination and is not atomic. |
| Campaign/run lineage | `CampaignStore` | `campaigns/<campaign>.json` | Atomic temp/replace, but independent from run and pipeline writes. |
| Pipeline task state | `AsyncDAGScheduler` | `runs/<run>/pipeline_state.json` | Atomic temp/replace, but no database transaction, lease, or fencing token. |
| Routing call state | `ModelRouter` | `runs/<run>/routing_state.json` | `begin_call` persists `ACTIVE` before the provider call; it is neither an AttemptIntent nor a transactional outbox record. |
| In-process execution | `AsynchronousPipelineRuntime` | `Future`, `TaskExecutionContext`, and reservation dictionaries | Memory only. Lost on process death. |
| Provider retry | Individual provider clients and routed fallback | Provider archive files plus counters | A client retry is a new physical request hidden inside one routing call; no durable attempt identity separates the requests. |
| Usage reservation | `AtomicResourceBudget` | Embedded in `pipeline_state.json` | Reservation is in memory until a later scheduler save and is not committed with dispatch intent. |
| Candidate and audits | `CandidateEngine`, `AuditCoordinator`, OpenProver | Markdown/JSON files under the run | Files are generally written independently of run, task, and routing state. Several writers are direct `write_text`. |
| Truth mutation | `TruthStoreFacade` plus `ProjectStore` | immutable intent/receipt files plus theorem projection | Content-addressed intent and replayable receipt exist, but the theorem transaction and receipt file are not one atomic commit. |
| Research closure/disposition | `ResearchStoreFacade` | immutable closure/decision/obligation/map artifacts plus current-index projection | Deterministic artifacts exist, but no accepted-effect slot protects the map revision across crash/replay. |
| Governance review/patch | `GovernanceController` | immutable review/patch/application artifacts plus clock/control projections | Validation is strong, but artifact, clock, map, and control updates are separate filesystem commits. |
| Graceful stop | `StopController` | campaign stop JSON | Cross-process request exists, but it is not a lease cancellation/fencing mechanism. |
| Process interruption | provider clients and OpenProver | process-local events/process handles | Codex CLI has a Windows `taskkill` path; the required interruption test is POSIX-specific and fails on Windows. |

There is no `RuntimeBackend`, SQLite database, WAL configuration, schema migration ledger, transactional outbox, attempt journal, lease table, result registry, effect slot, reconciliation action log, or fencing check in the audited baseline.

## 3. Actual call and commit path

### 3.1 Async pipeline call

The production path is:

1. `AsyncDAGScheduler.dispatch_window` removes a task from a JSON queue, marks it `ACTIVE`, and saves `pipeline_state.json`.
2. `AsynchronousPipelineRuntime.start_window` reserves budget in memory.
3. It creates a process-local `TaskExecutionContext`.
4. It adds a synthesized `call_id` to the task and saves `pipeline_state.json` again.
5. The handler creates `RoutedLLMClient`.
6. `ModelRouter.begin_call` appends an `ACTIVE` routing call and saves `routing_state.json`.
7. The provider client performs one or more physical requests, including its internal retries.
8. The provider may write response/archive files.
9. `ModelRouter.finish_call` marks the routing record complete and saves `routing_state.json`.
10. The future returns a parsed result to `AsynchronousPipelineRuntime.poll`.
11. Usage reconciliation, usage recording, task completion, obligation mutation, and pipeline save occur as separate operations.

No single durable commit covers steps 1–6. No durable transaction covers steps 7–11.

### 3.2 Candidate/audit call

Candidate generation delegates to OpenProver, whose planner/worker/verifier artifacts and step metadata are filesystem files. `AuditCoordinator` calls routed clients in a thread pool, then writes each audit JSON independently. Run phase advancement is a later `state.json` write. Consequently, a completed provider call, an audit artifact, the audit gate, and the run phase can each survive or disappear independently.

### 3.3 Domain effects

- Truth promotion writes an immutable mutation intent first, performs the theorem compare-and-transition and resulting snapshot under the project truth lock, exits that lock, then writes the immutable mutation receipt.
- Research closure resolution writes an immutable resolution decision, then creates a new disposition/obligation/map revision and advances the current-map projection.
- Architecture review writes the immutable review, then creates and persists a reset clock.
- Authorized patch application advances the ResearchMap, then writes the application, clears pending control, and ensures the clock.

These are well-validated filesystem sagas, but none has a durable runtime effect slot or reconciler-owned commit protocol.

## 4. Required questions and answers

### 1. What is the current job identity?

There is no canonical runtime job identity. The implementation uses several unrelated identifiers:

- run identity: `run_id` / run-directory name;
- pipeline logical work: `task_id`;
- pipeline retry marker: mutable `attempt_id` inside the task JSON;
- routing call: `call_id`;
- provider-internal retry: an attempt-directory number or request counter;
- research execution: `tactical_session_id`;
- domain effects: content-derived mutation, review, patch, and decision identifiers.

`task_id` is the closest object to a logical job, but it is not separated from attempt state. A restarted `ACTIVE` task mutates its `attempt_id` in place. The provider's internal retry requests do not receive durable AttemptIntent identities.

### 2. What durable intent exists before a provider call?

For routed calls, `ModelRouter.begin_call` saves an `ACTIVE` call record immediately before invoking the client. For async pipeline calls, the scheduler also saves `ACTIVE` task state and a `call_id`. These writes are independent JSON replacements and do not form a transaction.

There is no durable record that simultaneously commits:

- LogicalJob identity;
- immutable AttemptIntent identity and payload digest;
- attempt state transition;
- provider dispatch command in an outbox;
- budget reservation;
- lease owner/epoch/fencing token.

Therefore the current `ACTIVE` records are observations, not a transactional authorization to perform one exact external call.

### 3. What happens if the process crashes after the provider call but before artifact write?

The provider may have executed and billed the request, while no parseable local result survives. On restart, routing state can remain `ACTIVE` and pipeline task state can remain `ACTIVE`. The scheduler treats the task as an orphan and makes it dispatchable again (proof/verification become `RETRY_READY`; literature usually becomes `READY` unless a cache/artifact hint is present). The next process can issue another provider request.

The system cannot distinguish “not sent,” “sent but no response,” and “response received but not persisted.” It does not preserve `UNKNOWN_EXECUTION`, and it can blindly re-execute logical work.

### 4. What happens if the artifact is written but state is not written?

Resume behavior depends on which subsystem produced the artifact:

- A provider archive or OpenProver worker result is generally not registered in a durable result registry, so the scheduler can redispatch and create another result.
- Literature orphan reconciliation only checks payload hints such as `result_cache` or `artifact_path`; it does not scan and validate all artifacts.
- A candidate file can exist while run phase remains `CONTEXT_READY`; the orchestrator may rerun candidate generation before it rechecks the candidate boundary.
- An audit JSON can exist while `state.json` remains before `AUDITS_READY`; the audit suite can run again and overwrite or collide with artifacts.
- An immutable domain artifact may block a different replay payload, but it does not automatically complete the missing control transition.

There is no deterministic orphan-artifact adoption protocol.

### 5. What happens if the same result is ingested twice?

There is no uniform result-ingestion identity or registry. Pipeline `complete_task` is idempotent only after the task has reached one of its recognized terminal statuses; that protects repeated calls in the same saved task state, not repeated physical results across changed attempt IDs or crash recovery. OpenProver sidecars and audit artifacts can be overwritten by direct writers. Domain immutable writers accept byte-identical repeats but that only protects an artifact path; it does not prove that downstream semantic effects are exactly once.

The baseline therefore cannot guarantee one accepted result per LogicalJob.

### 6. Can retry create duplicate ResearchMap revisions?

Yes, across the relevant crash window. `resolve_session_closure` deterministically writes the same resolution decision and then calls `record_disposition`, which produces a new obligation/map revision. If the map revision/current index commits but the caller loses the success acknowledgement, replay reevaluates against the new current map. It may reject, raise due to stale bindings, or in another retry path create further revisions; there is no effect-slot receipt binding the accepted resolution to exactly one target map revision.

Normal sequential replay is often stopped by current-map validation, but that is optimistic CAS-like behavior, not an exactly-once guarantee and not deterministic recovery of a partially completed effect.

### 7. Can a TruthMutationReceipt be replayed?

If the immutable receipt exists, `compare_and_transition` loads it and returns the already-applied result, so that completed path is replay-safe. The unsafe window is after the theorem transition/resulting snapshot commit and before receipt write. A crash there leaves the content-addressed intent but no receipt. Replay starts from the old audited snapshot, observes the changed theorem, and can be blocked rather than reconstructing the missing receipt. Thus receipt-present replay works; truth mutation as a whole is not yet crash-exactly-once.

### 8. Can ArchitectureReview commit reset the clock twice?

Ordinary sequential replay after a completed reset is rejected because the new clock is no longer due. However, `commit_review` writes the immutable review before persisting the reset clock. A crash between those operations leaves a committed review and an old due clock; replay can perform a reset later, but there is no durable effect receipt proving which clock revision belongs to the review. Concurrent processes can also both validate the same due clock before independent writes. Filesystem collision checks may reject one projection write rather than deterministically accepting exactly one reset. The current code does not provide an exactly-once reset guarantee under crash/concurrency.

### 9. Can a stale worker overwrite a newer worker result?

Yes. Task ownership is process-local and there is no lease epoch or fencing token. Restart changes the task attempt ID and redispatches it, but the old process/provider can still finish. If both processes retain filesystem access, either can write provider archives, routing state, pipeline state, or direct result paths. `complete_task` checks task status, not worker lease ownership. No semantic acceptance fence prevents a stale result from being selected or a stale executor from finalizing state.

### 10. How is an orphan detected from current `RUNNING` state?

Only pipeline tasks persisted as `ACTIVE` are treated as orphans when a new `AsyncDAGScheduler` is constructed from saved state. They are removed from the active list, tagged `ORPHANED_AFTER_RESTART`, assigned a new mutable `attempt_id`, and usually returned to a queue. Detection does not use process identity, heartbeat expiry, lease expiry, provider dispatch evidence, result registry, or outbox state.

Routing calls left `ACTIVE`, run state left `RUNNING`, in-flight audit calls, and in-flight candidate calls have no equivalent authoritative orphan reconciliation.

### 11. Which is authoritative: checkpoint or runtime state?

The baseline has multiple competing authorities:

- `state.json` controls orchestrator phase/resume;
- `pipeline_state.json` controls queues/tasks/obligations;
- `routing_state.json` controls routing call history and counters;
- campaign JSON stores a copied `pipeline_state` checkpoint and run status;
- in-memory futures/contexts decide which work is actually executing.

Campaign resume can inject its copied pipeline state into a new scheduler. There is no database current-state authority or ordering rule that resolves disagreement among those files. In practice, each consumer treats its own file/checkpoint as authoritative for its slice.

### 12. Who owns transition authority: filesystem or durable control state?

The filesystem owns both, because there is no distinct durable control plane. Mutable JSON projections authorize execution transitions, while immutable/domain files establish Truth, ResearchMap, and governance facts. The same filesystem therefore serves as queue, journal, checkpoint, result store, and domain authority without an atomic boundary between these roles.

Phase 6 must split this responsibility: SQLite/WAL owns current execution-control state and transition authorization; the filesystem remains authoritative for large and domain artifacts; neither silently substitutes for the other.

### 13. Are there partial commits where file write succeeds and state write fails, or the reverse?

Yes, in both directions. Confirmed examples include:

- provider response/archive succeeds, then routing/task/run state update fails;
- routing `ACTIVE` succeeds, provider process never starts;
- task `ACTIVE` succeeds, budget reservation or future submission fails/process dies;
- worker/audit/candidate artifact succeeds, later phase checkpoint fails;
- phase/task state succeeds, expected artifact write was interrupted or malformed;
- Truth theorem transition succeeds, receipt write fails;
- resolution decision succeeds, ResearchMap update fails;
- ResearchMap patch succeeds, application/control/clock update fails;
- ArchitectureReview succeeds, clock reset fails;
- campaign checkpoint succeeds while run/pipeline files contain a different generation, or vice versa.

Atomic temp/replace helpers reduce torn-file risk but do not make multi-file operations atomic. Several important writers (`orchestrator`, `candidate_engine`, `audit_coordinator`, and `ResearchPolicy`) write directly to final paths and can additionally leave torn or truncated JSON/text.

### 14. Is the interruption path truly cross-platform?

No. `CodexCLIClient` itself has a Windows-aware process-group creation and `taskkill /T /F` fallback, which is useful. But the required `tests/test_interrupt_race.py` is POSIX-specific:

- it invokes `python3` rather than `sys.executable`;
- it passes `start_new_session=True` unconditionally;
- it calls `os.killpg`, which does not exist on Windows, and does not catch `AttributeError`;
- it requires a negative signal return code, which is not the Windows termination convention;
- it executes tests at import time rather than under a guarded test runner entry point.

On the audited Windows baseline, collection fails at `os.killpg`. More broadly, cancellation is process-local and does not establish durable attempt cancellation or fence late results.

## 5. Crash-window inventory

| Window | Surviving evidence | Current recovery | Required Phase 6 disposition |
|---|---|---|---|
| Before durable dispatch authorization | task may be `ACTIVE` only | orphan redispatch | No provider call unless job + intent + outbox commit exists. |
| After out-of-process request starts, before local response | routing/task `ACTIVE` | redispatch | Preserve `UNKNOWN_EXECUTION`; reconcile, do not blindly resend same attempt. |
| Response received, before artifact durability | perhaps provider-side execution only | redispatch | Attempt remains unknown; new intent only under explicit retry policy. |
| Artifact durable, before registration | orphan file | mostly ignored | Validate and register through deterministic orphan-artifact reconciliation. |
| Artifact registered, before result acceptance | no current equivalent | no current equivalent | Idempotent ingestion plus deterministic one-winner acceptance fence. |
| Result accepted, before semantic effect | task state may lead artifact | ad hoc replay | Claim durable effect slot, then execute/reconcile saga. |
| Domain mutation applied, before effect receipt | changed domain files | often block/reject replay | Reconstruct/finalize the same effect receipt deterministically. |
| Lease expires while worker still runs | no lease evidence | both workers may write | Epoch/fencing token rejects stale finalization; late result may be retained only as an artifact. |

## 6. Reusable primitives and limits

The refactor should preserve these useful properties rather than discard them:

- immutable content-addressed Truth, ResearchMap, evidence, review, patch, and effect artifacts;
- existing schema validation and exact binding checks;
- Truth mutation intent/receipt identity;
- atomic temp/replace helpers for individual filesystem projections;
- deterministic resolution and governance gates;
- task-scoped cancellation handles;
- provider-specific process-tree termination;
- current resource accounting fields and unknown-usage policy;
- checkpoint migration classification and project/target binding validation.

Their current limitation is consistent: they protect one object or one file, not the execution transaction spanning intent, external call, result, acceptance, and semantic effect.

## 7. Required Phase 6 ownership boundary

The implementation following this audit must enforce this boundary:

1. A project-scoped SQLite database in WAL mode is the sole current execution-control authority.
2. `RuntimeBackend` is the interface through which production code creates jobs/intents, claims outbox work, transitions attempts, leases work, registers results/artifacts, claims effects, and records reconciliation.
3. A LogicalJob is stable across retries. Every physical dispatch has an immutable AttemptIntent.
4. Job + AttemptIntent + initial attempt transition + outbox command commit in one database transaction before any provider call.
5. The append-only transition journal is evidence, not a second mutable-state authority.
6. Leases carry monotonic fencing epochs. Stale executors cannot transition current state or directly finalize a semantic effect.
7. The filesystem remains authoritative for large/provider/domain artifacts. Artifact registry rows bind path, digest, size, producer attempt, and durability state.
8. Result ingestion is idempotent and deterministically selects at most one accepted result per effect slot.
9. Truth, research, and governance mutations run as named cross-store sagas with durable effect slots and deterministic reconciliation.
10. JSON checkpoints become exports/projections for compatibility and inspection; they cannot override newer database state.
11. Controllers perform no provider call or slow filesystem/domain operation while holding a SQLite write transaction.
12. Process interruption uses a shared cross-platform termination primitive and late-result fencing.

## 8. Migration order and non-goals

Safe implementation order:

1. add the backend protocol, SQLite schema/migrations, WAL settings, and project isolation;
2. add jobs, immutable attempts, state machine, journal, transactional outbox, leases, and fencing;
3. add artifact/result registry, accepted-result fence, effect slots, and reconciliation log;
4. route provider calls through the durable dispatch path;
5. wrap Truth, research, and governance effects as reconciled sagas;
6. make database state authoritative for checkpoint/resume and provide legacy import/projection;
7. replace the POSIX-only interruption test/path with cross-platform behavior;
8. add fault-injection, crash/restart, and production E2E evidence before declaring Phase 6 complete.

This phase does not introduce full event sourcing, a graph database, or a distributed runtime. It does not move large/domain artifacts into SQLite and does not redesign mathematical strategy. Phase 7 root synthesis/final consolidation remains out of scope until every Phase 6 gate is evidenced.

## 9. Audit conclusion

The baseline is locally durable at the individual-file level in several subsystems, but it is not a durable execution runtime. It cannot prove whether an external request executed, cannot fence a stale worker, cannot register/adopt artifacts uniformly, and cannot guarantee exactly one accepted semantic effect across crash and replay. The Phase 6 SQLite/WAL control plane is therefore an ownership correction, not merely a storage optimization.
