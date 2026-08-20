# PHASE 4 Production Research-Path Audit

## Audit boundary

- Audited branch: `codex/v3-2-reconciliation`.
- Audited HEAD: `55640cd5301932d62034c696635076d2511b5167`.
- Working tree at audit start: clean.
- Governing specification: `Harness_v3_2_合并架构与冻结规范.md`.
- This document was written before any PHASE 4 production-module change.
- PHASE 3 Truth Plane artifacts are treated as frozen production authority and
  are not replaced by this phase.
- Out of scope: Architecture Review and clock, Structural Probe, Architecture
  Critic, SQLite/WAL, transactional outbox, leases/heartbeats, AttemptIntent
  runtime, cross-process reconciliation, full ROOT_SYNTHESIS/consolidation,
  distributed execution, graph databases, event sourcing, provider redesign,
  and tactical-kernel redesign.

## Executive finding

The current system has durable execution and truth state, but no canonical
Research Plane owner. Long-term research meaning is split across campaign runs,
pipeline records called “obligations”, mutable steering, failed-route prose,
`StrategyFingerprintStore`, and `ModelRouter` counters. None of those objects is
an immutable, root-ClaimSnapshot-bound research frontier.

The production path therefore cannot yet answer, with a typed durable record:

```text
What remains open?
Why is it open or blocked?
Which exact evidence can resolve it?
Which failed route is invalidated by which changed dependency?
Which scope was retained, reframed, superseded, or abandoned between revisions?
```

PHASE 4 must add those answers without treating research judgments as theorem
truth and without reinterpreting execution lifecycle as research disposition.

## Current ownership map

| Concern | Current production owner | Durable identity/state | Audit finding |
|---|---|---|---|
| Root theorem truth | `TruthStoreFacade` over `ProjectStore` | immutable `ClaimSnapshot` plus truth mutation intents/receipts | Correct Truth Plane authority; Research Plane must only bind to it |
| Campaign continuity | `CampaignStore` / `CampaignEngine` | campaign id, run ids, parent/successor links, pipeline/routing snapshots | Durable execution lineage, but a successor run is currently used as a proxy for a new research world |
| Candidate lifecycle | `ResearchOrchestrator` and `CandidateEngine` | run directory, candidate/audit artifacts, run phase/status | Tactical attempt lifecycle, not a durable research obligation |
| Pipeline “obligation” | `AsyncDAGScheduler` | mutable record in `pipeline_state.json` | An execution projection mixing queues, tasks, provider tier, proof/literature/verification status, and `CLOSED`; it is not a ResearchObligation |
| Worker task | `RoleScheduler`, `ResearchPolicy`, pipeline tasks | task/assignment/attempt ids and execution statuses | Session-local execution; task completion cannot by itself resolve research scope |
| Research strategy/escalation | `ModelRouter` | per-obligation tier, failure counts/history, disagreement counts, stalled cycles, escalation history | Router currently owns long-lived research-stall semantics in addition to compute routing |
| Repeated failed strategy | `StrategyFingerprintStore` | hash of theorem/branch/target lemma/method/key dependency/failure point | Durable suppression memory, but dependency and assumption context is not snapshot-exact |
| Failed route | `ProjectStore.failed_routes.json` | free-form strategy/target/obtained/failure/recovery fields | Useful legacy evidence, but no exact obligation, dependency snapshot, authority context, or typed reopen condition |
| Repair diagnosis | `FailureMap` | categories, gaps, frozen fingerprints, repair suggestions | Heuristic typed adapter candidate; currently mixes mathematical, provider, and infrastructure failures |
| Project steering | `ProjectStore.update_steering` and `ContextBuilder` | mutable frozen/prohibited routes, allowed scope, added lemmas | Direct mutation can remove/reframe scope without an immutable map revision or disposition provenance |
| Planner context | `ContextBuilder` and `CandidateEngine` | generated context artifacts for one run | Planner receives project/run context, not an immutable Directive projected from a ResearchObligation |
| Evidence/audit result | worker footer, candidate, audit artifacts, `AuditGate` | typed worker event and audit/gate artifacts | Strong Truth Plane gate exists, but no separate Evidence-to-Obligation resolution gate or SessionClosure |
| Resume | orchestrator `state.json`, routing and pipeline snapshots | schema-checked run checkpoint plus ClaimSnapshot validation | Root truth staleness is guarded, but no map id/version, open frontier, directive, or session binding is reconstructed |

## Old production research path

```text
Campaign target/run
        ↓
target id is reused as pipeline “obligation” and router obligation id
        ↓
ContextBuilder emits project + steering + failed-route context
        ↓
Planner creates WorkerTasks; RoleScheduler truncates to capacity
        ↓
typed worker events mutate pipeline execution state
        ↓
failure/stall/disagreement counters mutate ModelRouter state
        ↓
candidate + verifier + auditors + Truth audit gate
        ↓
on PASS: theorem Truth Plane promotion
on failure: FailureMap + StrategyFingerprint + optional successor run
```

This path preserves substantial evidence, but it has no immutable
`ResearchMap → ResearchObligation → Directive → TacticalSession →
SessionClosure → ResearchMap revision` chain.

## Direct answers required by PHASE 4

### Who owns long-term research progress today?

No single component does. Campaign successor lineage, pipeline obligation
records, router escalation history, strategy fingerprints, mutable steering,
and failed-route records each own fragments. Their meanings overlap and none is
the canonical root-snapshot-bound research frontier.

### Does `ModelRouter` own research strategy in practice?

Yes. Its durable schema stores per-obligation failure history, escalation
history, verifier disagreements, stalled frontier cycles, and the current tier.
`ResearchPolicy` forwards worker failure and progress signals into those
counters. Compute/model selection is valid Router ownership; durable judgments
about route failure or research stall are not and must move to the Research
Plane.

### Does `StrategyFingerprint` own research strategy in practice?

Partly. It freezes repeated failed theorem/branch/method combinations and can
reopen based on coarse booleans such as “new dependency”. This is long-term
route memory, but its context is not exact enough for PHASE 4. Existing records
must remain readable and be projected as `LEGACY_DERIVED` RouteFailureRecords;
they must not be deleted or silently treated as fully precise records.

### Which object is masquerading as `ResearchObligation`?

The strongest collision is `AsyncDAGScheduler.state["obligations"]`. Those
records contain execution statuses (`PROOF_READY`, `PROOF_ACTIVE`,
`LITERATURE_ACTIVE`, `VERIFICATION_ACTIVE`, `BLOCKED_DEPENDENCY`, `DUAL_TRACK`,
`CLOSED`), queue/task state, current model tier, and runtime metadata. They are
execution projections. Campaign runs and candidates also proxy “the thing being
worked on”, but neither has durable research semantics or disposition history.

### Which execution states are mixed into research semantics?

Pipeline obligation `status`, `proof_status`, `literature_status`, and
`verification_status`; task `READY`, `ACTIVE`, retry, cancellation, interruption,
and error states; run `RUNNING`, checkpoint, candidate, audit, rejection, and
completion phases; and Router tier/escalation counters. None may become a
ResearchObligation disposition. PHASE 4 dispositions are research-only:
`OPEN`, `BLOCKED`, `RESOLVED`, `SUPERSEDED`, and
`ABANDONED_WITH_REASON`.

### Where can scope disappear silently?

1. `RoleScheduler.assign_tasks` selects `tasks[:capacity]`; unselected items are
   not a durable research frontier at that adapter boundary.
2. `ProjectStore.update_steering` mutates allowed scope and can unfreeze/remove
   branches without a parent map version, omission disposition, or provenance.
3. Pipeline closure/cancellation removes runnable work from queues but does not
   retain a canonical research obligation.
4. A single-target campaign/run only serializes its local execution projection;
   sibling open questions have no required carry-forward invariant.
5. Repair successors inherit selected artifacts/state, but there is no typed
   rule requiring every prior open obligation to be retained or explicitly
   disposed.

The existing Planner over-capacity guard prevents one known production plan
overflow, but it does not provide a durable multi-obligation no-scope-loss
guarantee.

### How are failed-route reopen conditions expressed today?

`failed_routes.json` stores free-form `recovery_conditions`, and
`StrategyFingerprintStore.can_attempt` accepts coarse flags
`new_dependency`, `new_lemma`, and `failure_condition_changed`. Neither binds a
failure to exact dependency, assumption, authority, or ClaimSnapshot identities.
Consequently reopening cannot be deterministically justified from immutable
context changes.

### Can local lemma/progress be mistaken for global research progress?

Yes. Worker events and pipeline task completion can advance pipeline status,
reset Router stalled cycles, promote tiers, or close the pipeline obligation.
These are useful tactical signals, but without a SessionClosure and
Evidence-to-Obligation gate they can be over-read as global research progress.
A candidate, worker prose, or local task `COMPLETED` must never directly resolve
a ResearchObligation.

## Evidence and closure audit

### What is already safe

- Free-form worker prose is separated from the one typed worker-event footer.
- Strict Pydantic schemas reject unknown control-plane fields.
- Verification and audit artifacts exist independently from candidate prose.
- Canonical authority and ClaimSnapshot gates fail closed.
- Pipeline applicability promotion rechecks its exact applicability assumption
  snapshot.

### What is missing

- No immutable TacticalSession binding to map version, obligation revision,
  Directive, and root ClaimSnapshot.
- No SessionClosure that retains raw artifacts while projecting only validated
  evidence.
- No deterministic research resolution statuses:
  `RESOLUTION_ACCEPTED`, `INSUFFICIENT_EVIDENCE`, `STALE_EVIDENCE`,
  `AUTHORITY_BLOCKED`, `AUDIT_FAILED`, or `SCOPE_MISMATCH`.
- No reverse index from evidence/authority/snapshot identity to affected
  obligations, map versions, and route failures.
- Pipeline `close_obligation` can set `CLOSED` after its execution verification
  gates, but that is not a Research Plane disposition and cannot be migrated by
  name alone.

## ClaimSnapshot and resume audit

PHASE 3 already blocks a stale or unknown root ClaimSnapshot on resume and
promotion. PHASE 4 can reuse that comparison, but must bind every ResearchMap,
ResearchObligation, Directive, TacticalSession, SessionClosure, and accepted
resolution to the exact root snapshot hash.

Current run checkpoints store routing and pipeline state but not:

```text
research_map_id
research_map_version
open_obligation_ids
active_directive_id
tactical_session_id
root_claim_snapshot_hash as a Research Plane binding
```

A legacy checkpoint without a ResearchMap cannot prove its frontier. It must be
classified `REVALIDATION_REQUIRED`; adapters may create an explicit initial map,
but resume must never fabricate one silently.

## Required PHASE 4 ownership boundary

```text
TruthStoreFacade / ClaimSnapshot
        ↓ immutable root binding only
ResearchStoreFacade
        ├── immutable versioned ResearchMap
        ├── immutable ResearchObligation semantic revisions
        ├── immutable disposition records and complete current projection
        ├── immutable Directive
        ├── TacticalSession binding + SessionClosure
        ├── Evidence-to-Obligation resolution gate
        ├── dependency-aware RouteFailureRecord
        └── minimal reverse-reference indexes
        ↓ tactical projection only
Planner → WorkerTask / CandidateAttempt / Verifier / Audit
```

Ownership rules:

1. ResearchMap is non-authoritative and may be wrong; it cannot mutate theorem
   truth, ClaimSnapshot, AuthorityBinding, TrustReceipt, or TruthMutationReceipt.
2. ResearchObligation is durable across task, candidate, session, crash, and
   successor-run lifecycles.
3. WorkerTask and CandidateAttempt are execution objects, never research state.
4. Directive is immutable session intent projected from one exact map version
   and obligation revision; Planner output cannot mutate the map.
5. SessionClosure retains raw artifacts and separately lists validated evidence.
6. Only the typed evidence gate may accept a resolution; worker prose and an
   unaudited candidate are insufficient.
7. RouteFailureRecord owns research-route memory. ModelRouter receives bounded
   routing inputs and may choose compute/model escalation, but cannot revise the
   map or own durable research-stall policy.
8. Every map revision must carry every prior open obligation or give an explicit
   typed disposition with provenance. Omission is an error.
9. Root staleness blocks map revision, Directive creation, session continuation,
   and evidence acceptance until an explicit rebase records old/new snapshots
   and carried, invalidated, or revalidation-required obligations.
10. Filesystem persistence remains the minimal PHASE 4 mechanism; database and
    distributed-runtime work stays out of scope.

## Planned migration seams

### Campaign and orchestrator

- Campaign creation captures or reuses the exact ClaimSnapshot and creates an
  explicit initial ResearchMap plus root ResearchObligation.
- A run/successor binds to an existing obligation and records execution lineage;
  it does not create a new research ontology merely because a candidate failed.
- Checkpoints persist the map/version/frontier/directive/session/root binding and
  validate it on resume.

### Planner and workers

- Planner receives a Directive projection, not mutable ResearchMap ownership.
- Worker assignments remain execution tasks scoped to the Directive obligation.
- Planner/worker output becomes raw session evidence or typed proposals; it
  cannot directly change obligation disposition or map versions.

### Failure memory

- `FailureMap` becomes a typed compatibility adapter that proposes blockers and
  RouteFailureRecords; prose categories do not mutate the map.
- New failures are stored as exact-context RouteFailureRecords.
- Legacy `StrategyFingerprint` and `failed_routes.json` remain intact and are
  converted only through provenance-marked adapters.
- Production worker failure/stall events stop mutating long-term strategy inside
  ModelRouter.

### Session closure

- A closure records all raw artifact references, the validated evidence
  projection, unresolved findings, exact bindings, and an explicit closure
  status.
- The evidence gate compares scope, root snapshot, authority, verifier/audit
  evidence, and invalidation indexes before producing a research disposition.
- Accepted resolution creates a new immutable obligation disposition and map
  version; it does not promote theorem truth.

## Fail-closed and compatibility requirements

- Every new schema has an exact version and object type; unknown versions and
  unknown fields require migration.
- Existing immutable content-addressed bodies cannot be overwritten with
  different content.
- Current projections are rebuildable indexes, not authority.
- Legacy campaign, pipeline, fingerprint, failed-route, and checkpoint files are
  preserved.
- A legacy execution `CLOSED`, candidate success, audit prose, or worker event is
  never auto-promoted to `RESOLVED`.
- All compatibility conversions record source references and provenance.

## Audit conclusion

The current production system does not yet have a Research Plane. The safe
migration is additive: introduce one canonical filesystem-backed
`ResearchStoreFacade`, bind it to PHASE 3 ClaimSnapshots, project immutable
Directives into the existing tactical path, and return validated SessionClosure
evidence to immutable map revisions. Campaign, pipeline, Router, and legacy
failure stores become execution or compatibility projections; none remains an
independent owner of long-term research meaning.
