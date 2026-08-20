# PHASE 5 Architecture Review Report

## Ownership

`GovernanceController` owns durable review scheduling and typed artifact
acceptance. It does not create mathematical strategy, WorkerTasks, provider
routes, obligation resolutions, or Truth mutations. `ArchitectureReview` is an
immutable governance object bound to one exact ClaimSnapshot and ResearchMap
version/hash.

## Mandatory clock

`ArchitectureReviewClock` persists logical, deterministic counters for
sessions, tactical and structural effects, per-obligation route failures,
blocked-obligation age, map-version movement, candidate repair cycles, and
unchanged root obstruction. It also retains explicit trigger signals. Wall time
is artifact provenance only; it is not the scheduler.

Supported triggers are `MANDATORY_INTERVAL`, `REPEATED_ROUTE_FAILURE`,
`LONG_BLOCKED_OBLIGATION`, `TACTICAL_WITHOUT_STRUCTURAL_PROGRESS`,
`MAJOR_SCOPE_CHANGE`, `ROOT_OBSTRUCTION_STALLED`, `HUMAN_REQUEST`,
`LITERATURE_MECHANISM_CHANGE`, and `PRE_DESTRUCTIVE_REFRAME`.

Only `GovernanceController.commit_review()` resets the clock. Worker success,
local lemmas, one obligation resolution, successor execution, provider
selection, and map activity do not reset it.

## Review schema and limits

Every review contains findings for all twelve mandatory dimensions:
partition, parameterization, root obstruction, obligation distribution,
route-failure concentration, structural-effect density, tactical/structural
ratio, termination visibility, dependency architecture, scope coverage, stale
authority/assumptions, and candidate-repair dominance.

The strict loader rejects extra fields, including truth or obligation mutation
fields. Commit validates the current root, exact map version/hash, complete
OPEN/BLOCKED summaries, route-failure references, and StructuralEffect
references. A review may recommend a probe or patch, but it cannot apply one.

## Durability and resume

Clock revisions and reviews are immutable typed JSON. Current clock/control
projections use atomic filesystem replacement under single-process semantics.
Run and campaign checkpoints bind the clock identity/hash/due state, last
review, active probe, and pending patch. Current checkpoints fail closed on a
clock-hash mismatch. A legacy checkpoint creates a current
`GOVERNANCE_REVIEW_REQUIRED` state and `HUMAN_REQUEST`; it does not fabricate a
historical review.
