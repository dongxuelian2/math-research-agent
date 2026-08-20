# F-007 — Cross-Plane Binding Repair Report

## Finding and cause

Routed work could create a LogicalJob and AttemptIntent with only provider,
role, obligation, and payload context. Runtime reconciliation therefore had no
independent way to distinguish a result bound to ClaimSnapshot C1 or ResearchMap
v1 from a result that was current for C2 or v3.

## Repair

`CrossPlaneExecutionBinding` is a frozen, content-addressed contract. It carries
the root ClaimSnapshot hash and, when semantically applicable, exact ResearchMap
id/version/hash, obligation, directive, tactical session, and governance object
identity. The owning orchestrator constructs it; the Router only forwards it.

The binding is persisted in explicit columns and canonical JSON on LogicalJob,
AttemptIntent, AttemptResult, and EffectSlot. Runtime schema version 3 adds a
forward migration from version 2; existing databases are migrated in place and
retain their migration history. Result acceptance validates current binding;
EffectSlot preparation validates it again. Artifacts remain retainable when the
binding is stale, but semantic authority is denied.

Truth-only jobs use a root-only binding. Research jobs use the map root captured
by the ResearchMap, which is intentionally stable across non-authoritative
Truth lifecycle transitions; final TruthMutation uses the current ClaimSnapshot
root at its own boundary.

## Proof

- `test_f007_stale_binding_is_fenced_at_acceptance` proves a C1-bound result
  cannot be accepted when the current binding is C2.
- `test_f007_binding_survives_job_attempt_result_and_effect_persistence`
  verifies exact binding round-trip through all four runtime records.
- `test_production_semantic_effects_use_effect_slots_and_exact_bindings`
  inspects a real orchestrator run and verifies the SessionClosure job carries
  the exact map/root/directive/session identity.
- X1 is `CERTIFIED`; stale artifacts do not create Truth, Research, or
  Governance effects.

## Status

`F_007_ROUTED_CROSS_PLANE_BINDINGS = CLOSED`
