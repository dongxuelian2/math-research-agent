# PHASE 5 Architecture-Governance Path Audit

## Audit boundary

- Audited branch: `codex/v3-2-reconciliation`.
- Audited HEAD: `577035b940a6f3ae55beca823e08ad1be4879d94`.
- Working tree at audit start: clean.
- Governing specification: `Harness_v3_2_合并架构与冻结规范.md`.
- This audit was completed before any PHASE 5 production-module change.
- PHASE 3 Truth Plane and PHASE 4 Research Plane ownership are preserved.
- Out of scope: SQLite/WAL, transactional outbox, leases/heartbeats,
  AttemptIntent, cross-process reconciliation, distributed runtime, graph
  databases, event sourcing, provider redesign, tactical-kernel rewrites, full
  ROOT_SYNTHESIS, and full Final Consolidation.

## Executive finding

The production path now knows the exact theorem and durable research frontier,
but it has no canonical architecture-governance owner. Architecture-like
judgments remain distributed among worker progress markers, legacy Router
escalation methods, literature `architecture_changing` flags, audit failure
repair successors, free-form steering, and direct `ResearchStoreFacade`
revision calls.

There is no durable distinction between activity, tactical progress, and
structural progress; no mandatory review clock; no immutable review or bounded
probe; and no independent authorization boundary for destructive map changes.
Consequently the system cannot yet prove that local success did not postpone a
required review, or that a destructive reframe had independent criticism and
complete scope transfer.

## Current implicit governance ownership

| Concern | Current production owner | Current behavior | Governance finding |
|---|---|---|---|
| Worker progress | typed `WorkerEventSchema.progress_signals` and `high_value` | records strings such as `VERIFIED_LEMMA`, `BRANCH_CLOSURE`, and `PARAMETER_REDUCTION` | typed transport, but not evidence-backed StructuralEffect classification |
| Candidate progress | `CandidateEngine` and pipeline state | candidate existence is `high_value` and moves execution to verification | tactical/execution progress only; not architecture health |
| Route failure | `FailureMap` → native `RouteFailureRecord` | audit failures attach exact route memory and create map revisions | correct Research evidence, but repeated failures do not create a review trigger |
| Repair loop | `CampaignEngine` | audit `REJECTED` plus `auto_successor` creates bounded successor runs | execution lineage is correct, but repeated repair cycles have no architecture-review escalation |
| Long-blocked work | ResearchObligation disposition and pipeline blockers | blockers remain durable or execution-scoped | no deterministic age measured in sessions/map versions and no forced review |
| Map reframe | public `ResearchStoreFacade.revise_map` and `record_disposition` | callers can change strategic thesis, projected scope, and supersession records | no local/destructive classifier and no patch authorization gate |
| Human steering | mutable `steering/directives.json` | freeze/prohibit/allow/add/stop instructions affect later Directive/context projection | timestamped compatibility input, not immutable review/patch/authorization provenance |
| Literature architecture signal | `LiteraturePipelineExecutor` | `architecture_changing` or conflicting literature escalates legacy Router tier | compute escalation is mixed with architecture concern; no durable review trigger |
| Router strategy compatibility | deprecated `ModelRouter` methods | failure/frontier/high-value counters can still be called directly | not used by the PHASE 4 CandidateEngine production policy, but remains a legacy architecture-like mutation path |
| Truth success | orchestrator finalization | audited candidate may promote Truth and close a project branch | valid Truth lifecycle action; it is not an ArchitectureReview commit |
| Checkpoint/resume | run and campaign JSON | restores ResearchMap/frontier/directive/session/root bindings | governance due state, last review, probe, and pending patch do not exist |

## Direct answers required by PHASE 5

### What currently triggers “change route”?

- A worker `FAILED_ROUTE` or audit failure produces failure evidence.
- Audit rejection can create an automatic bounded repair successor.
- Legacy Router counters can escalate compute tier after repeated failure or a
  stalled frontier.
- Literature can request a strategic compute tier after conflicting or
  architecture-changing output.
- Human steering can freeze/unfreeze branches and prohibit routes.

None of these is a formal architecture review. No current event is authorized
to perform a governed destructive ResearchMap reframe.

### Who currently owns reframe authority?

No canonical owner. At the API boundary, any caller holding a
`ResearchStoreFacade` can call `revise_map`, change `strategic_thesis`, supply
`removed_or_reframed_scope`, or project superseded dispositions. The PHASE 4
no-scope-loss check prevents omission of obligation ids, but it does not prove
that a major repartition, merge, split, or strategic-thesis change received a
review, probe, independent critic, or authorization.

### Are tactical and structural progress distinct?

No. Worker markers are a flat string list and a `high_value` boolean. Legacy
Router `record_frontier_cycle` treats branch closure, parameter reduction,
stronger invariant, verified lemma, and dependency simplification as one
“meaningful” bucket. Production PHASE 4 stopped feeding these signals into the
Router, but no replacement StructuralEffect ledger exists.

Therefore a local lemma cannot currently be formally classified as tactical
rather than structural, and a claim of an infinite-to-finite reduction has no
evidence-backed structural validation artifact.

### Can local lemma success make the system think the whole architecture is healthy?

In legacy adapters, yes: any recognized marker resets the Router stalled-cycle
counter. In the current production CandidateEngine route the Router hook is not
supplied, so local success does not mutate long-term Router state; however,
there is also no mandatory architecture clock to remain due despite that
success. The required invariant is therefore absent rather than violated by a
new canonical owner.

### How do repeated failures become an architecture concern?

They do not. Each audit failure can create a precise RouteFailureRecord, but no
controller counts distinct routes, common method families, or unchanged root
obstructions into an `ARCHITECTURE_REVIEW_TRIGGER`. Campaign repair is bounded
only by `max_repair_cycles`; exhausting that bound ends in human or
mathematical-exhaustion status rather than a typed review commitment.

### Who forces review for a long-open or long-blocked branch?

Nobody. ResearchObligation preserves the frontier and blockers, but no durable
control state records sessions/map versions since review or blocked duration in
logical units. Wall-clock timestamps exist but are not an adequate mandatory
review policy.

### Does destructive revision have an independent critic?

No. PHASE 4 supplies immutable revisions and explicit disposition provenance,
but no `ArchitecturePatch`, `ArchitectureCritic`, independence receipt, or
`PatchAuthorization`. The caller is effectively proposer and committer.

### Can scope transfer be checked by a critic?

Only partially and mechanically. `revise_map` refuses omitted obligation ids,
and supersession records retain replacements. There is no typed many-to-many
`ScopeTransfer` plan checked against all old scope, and no independent verdict
such as `SCOPE_LOSS` before mutation.

### Can Worker or Planner prose mutate architecture?

Not through the PHASE 4 production facade: their outputs are retained as raw
artifacts/proposals and neither receives a map-write capability. Nevertheless,
worker marker strings, Planner suggestions, and literature flags can influence
legacy routing/repair/steering paths. PHASE 5 must preserve the non-mutation
boundary and route all architecture effects through typed validation.

## Candidate-repair loop audit

The current loop is:

```text
candidate
→ audits
→ FailureMap / RouteFailureRecords
→ REJECTED
→ optional successor execution run
→ repaired candidate
```

This retains evidence and keeps successors as `EXECUTION_LINEAGE_ONLY`, which
must remain. Missing governance signals are:

- sessions and repair cycles since the last formal review;
- tactical effects without any validated structural effect;
- unchanged root obstruction and termination-mechanism absence;
- map-version churn caused only by local dispositions/route memory;
- concentration of failures by obligation, method family, or obstruction.

Creating a successor run must increment session/control evidence but must never
reset the mandatory review clock.

## ResearchMap mutation audit

`ResearchStoreFacade.revise_map` currently enforces:

- immutable parent/current comparison;
- root ClaimSnapshot validation;
- no omitted obligation ids;
- exact added-scope declaration;
- exact obligation/disposition/root binding.

It does not classify or gate:

- replacing a partition or parameterization;
- changing termination architecture;
- merging/splitting obligation families;
- changing a strategic thesis with broad scope impact;
- abandoning a route family;
- destructive supersession or reframing.

PHASE 5 must retain `revise_map` for typed local PHASE 4 operations while adding
a policy-enforced governed API for destructive changes. A destructive patch
must not be applicable by simply labelling it `HUMAN_STEERING` or
`SCOPE_SUPERSESSION`.

## Required PHASE 5 ownership boundary

```text
SessionClosure / RouteFailureRecord / human request / literature evidence
        ↓ typed signals only
GovernanceController
        ├── StructuralEffect ledger
        ├── mandatory ArchitectureReviewClock
        ├── immutable ArchitectureReview commits
        ├── bounded StructuralProbe
        ├── immutable ArchitecturePatch + ScopeTransfer
        ├── independent ArchitectureCritic verdict/receipt
        └── PatchAuthorization
        ↓ authorized destructive transition only
ResearchStoreFacade governed map revision
```

Ownership rules:

1. Activity, tactical progress, and structural progress are distinct typed
   effects; token, worker, call, and artifact counts are activity only.
2. Structural progress requires exact evidence, obligation, map, root, and
   validation-basis bindings. Worker prose becomes at most an unvalidated
   proposal.
3. Only a committed immutable ArchitectureReview resets the mandatory review
   clock.
4. Review triggers are durable governance control, not Router strategy state.
5. ArchitectureReview diagnoses architecture and proposes actions; it cannot
   mutate Truth, resolve obligations, create WorkerTasks, or revise a map.
6. StructuralProbe is bounded and non-authoritative. Success only supports a
   patch; failure leaves the current map and theorem truth unchanged.
7. ArchitectureCritic evaluates one exact patch and records independence
   provenance. Even `APPROVE` has no mutation authority.
8. PatchAuthorization requires current root/evidence, valid review and required
   probe, critic approval, no scope loss, and an intact Truth boundary.
9. Every destructive source scope is retained, explicitly disposed, or mapped
   through a typed many-to-many ScopeTransfer.
10. Router remains a compute allocator; successors remain execution lineage;
    Planner/Worker outputs remain proposals/evidence only.

## Checkpoint and compatibility requirements

New checkpoints must project:

```text
last_architecture_review_id
architecture_review_clock
architecture_review_due
active_structural_probe_id
pending_architecture_patch_id
root_claim_snapshot_hash
research_map_id/version/hash
```

Resume must reload authoritative immutable governance artifacts, validate the
current root and map, and preserve the due state. A legacy checkpoint cannot
fabricate historical reviews; it must produce
`GOVERNANCE_REVIEW_REQUIRED`/`REVALIDATION_REQUIRED`.

## Storage and phase boundary

The minimum implementation may use immutable typed JSON, atomic replacement,
and rebuildable projections under `research/governance/`. This is explicitly
single-process/filesystem durability. SQLite/WAL, outbox, leases, AttemptIntent,
and cross-process reconciliation remain PHASE 6 `NOT_STARTED`.

## Audit conclusion

The safe additive migration is to introduce one GovernanceController beside
the PHASE 4 ResearchStoreFacade, validate StructuralEffects from retained
evidence, make the review clock durable and resettable only by a review commit,
and require review → bounded probe when needed → patch → independent critic →
authorization before any destructive ResearchMap reframe. Existing local map
updates, tactical execution, Truth mutation, providers, and Planner/Worker
cores remain intact.
