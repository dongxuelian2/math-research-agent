# PHASE 5 Structural Probe Report

## Purpose

A `StructuralProbePlan` is a bounded governance experiment against an exact,
committed ArchitectureReview and ResearchMap. The plan records one bounded
question, proposed mechanism/partition/parameterization, target obstruction,
explicit success and failure criteria, evidence, and hard limits for sessions,
workers, provider calls, and wall-clock seconds.

`StructuralProbe` closes that plan as `SUPPORTS_PATCH`, `REJECTS_PATCH`, or
`INCONCLUSIVE`, with evidence and result basis. Plan and result are separate
immutable artifacts so a result cannot silently rewrite its budget or
criteria.

## Authority limits

The controller rejects stale root/map bindings and reviews whose verdict does
not authorize a probe. A failed or inconclusive probe preserves the current
map and all obligations. A supporting probe only satisfies one input to an
ArchitectureCritic; it neither authorizes nor applies a patch. Probe schemas
have no TruthMutation or obligation-resolution field and never call either
mutation path.
