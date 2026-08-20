# PHASE 5 Structural Effect Report

## Deterministic classification

`StructuralEffectKind` maps to exactly one level:

- `ACTIVITY`: worker spawn, tokens spent, artifact retention, provider call.
- `TACTICAL_PROGRESS`: local lemma, parameter-range reduction, one obligation
  resolution, bounded computation, dependency resolution.
- `STRUCTURAL_PROGRESS`: branch-class closure, global invariant, partition or
  parameterization simplification, infinite-to-finite reduction, termination
  mechanism, root-obstruction change, dependency-architecture change, or a
  validated new mechanism.

Worker count, token use, and artifact count therefore cannot become progress by
volume. A local lemma and an isolated obligation resolution are tactical by
default.

## Evidence boundary

Each effect binds an exact ClaimSnapshot, ResearchMap id/version/hash,
obligations, evidence references, validation basis, source, and producer.
`VALIDATED` requires evidence; prose-only claims remain
`UNVALIDATED_CLAIM`. Only validated effects increment tactical or structural
governance counters. Effect persistence is immutable and idempotent by typed
identity.

The production SessionClosure path emits a validated
`ONE_OBLIGATION_RESOLVED` effect only after the deterministic
Evidence-to-Obligation gate accepts the resolution. It remains tactical and
does not reset the review clock.
