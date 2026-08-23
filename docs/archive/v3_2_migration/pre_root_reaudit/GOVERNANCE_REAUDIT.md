# Governance Re-Audit

## F-005 — strategic thesis authorization

The positive ordinary probe still rejects:

```text
revise_map(revision_reason="HUMAN_STEERING", strategic_thesis=changed)
```

when no authorization is supplied.  The legal governance path also remains
positive through ArchitectureReview, StructuralProbe, ArchitecturePatch,
critic, `GovernanceController.authorize_patch`, and
`ResearchStoreFacade.apply_governed_reframe`.

The independent negative case is stronger.  A caller constructed a typed
`PatchAuthorization` with status `AUTHORIZED`, forged patch/review/critic
identities, `scope_validation_passed=False`, and
`truth_boundary_intact=False`.  Public `ResearchStoreFacade.revise_map`
accepted it and changed the strategic thesis.  It only checked type and status
at the destructive gate.  Therefore F-005 is `OPEN`.

## F-006 — critic independence

The frozen same-model case remains negative: different actor and fresh context
do not override `same_model=True`; `policy_satisfied=False`; authorization is
not `AUTHORIZED`.  The different-model positive workflow passes.  F-006 is
`CLOSED`.

## Governance effect ownership

Normal orchestrator governance signals, sessions, structural effects, and
route failures are runtime-effect adapters with exact map bindings.  Direct
governance controller calls are bounded domain APIs used by the adapters,
explicit admin/migration flows, or tests.  The governance ownership limitation
is the forged direct `revise_map` entry point, not the critic predicate.
