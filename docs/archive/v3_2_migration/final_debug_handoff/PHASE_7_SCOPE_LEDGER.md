# Phase 7 Scope Ledger

## Authoritative specification used

The scope is recovered from the newest explicit migration records:

1. `docs/v3_2_migration/V3_2_MIGRATION_REPORT.md`: the next safe frontier is
   `ROOT_SYNTHESIS × Final Consolidation × Promotion Closure`.
2. `docs/v3_2_migration/V3_2_IMPLEMENTATION_MATRIX.md`: `ROOT_SYNTHESIS full`
   requires exact root evidence synthesis, and `Final Consolidation full`
   requires an immutable promoted proof and consolidation re-audit.
3. `docs/v3_2_migration/CLAIM_SNAPSHOT_REPORT.md`: the root-synthesis seam must
   fail closed on stale or unresolved ClaimSnapshot state.
4. `docs/v3_2_migration/SESSION_CLOSURE_REPORT.md` and
   `TRUTH_MUTATION_REPORT.md`: validated research evidence and receipt-backed
   truth promotion are existing Phase 7 inputs and must remain separate.

No older planning record expands this scope with a new proof-search strategy,
new authority ontology, or a router redesign.

## Required components

| Component | Status before Phase 7 | Required implementation |
|---|---|---|
| Exact root ClaimSnapshot comparison | `ALREADY_IMPLEMENTED` | Reuse the existing TruthStore/ClaimSnapshot comparison and reject unknown/stale state. |
| Complete ResearchMap frontier | `ALREADY_IMPLEMENTED` | Consume the current immutable map; require no open or blocked obligation. |
| Validated SessionClosure evidence | `ALREADY_IMPLEMENTED` | Consume the existing closure and its typed evidence, never raw prose as authority. |
| Root synthesis artifact | `MISSING` | Add immutable, content-addressed synthesis with exact root/map/closure/evidence identities. |
| Final consolidation artifact | `MISSING` | Add immutable content-addressed final proof copy and provenance manifest. |
| Consolidation re-audit | `MISSING` | Add deterministic byte/provenance re-audit bound to the exact root synthesis and existing passing gate. |
| Promotion closure | `MISSING` | Add immutable record linking synthesis, consolidation, TruthMutation intent/receipt, and resulting ClaimSnapshot. |
| Orchestrator integration | `PARTIAL` | `_finalize()` currently closes research and promotes truth but has no Phase 7 artifacts or state fields. |
| Restart/resume integration | `PARTIAL` | Persist artifact identities in `state.json`; reload and verify immutable artifacts before treating completion as closed. |
| Positive functional tests | `MISSING` | Test a complete production-shaped path through synthesis, consolidation, and closure. |
| Basic negative tests | `MISSING` | Test stale root, open frontier, failed gate, missing closure, and tampered artifact rejection. |
| Documentation/handoff | `MISSING` | Create the self-contained final debug handoff package. |

## Explicit Phase 7 non-goals

- Do not repair NF-003 or NF-004 inside the Phase 7 implementation itself;
  those defects were handled by the separate final hardening pass recorded
  below.
- Do not change the prior audit disposition or certification flags.
- Do not redesign execution binding, routing, or authority contracts.
- Do not rewrite terminal rejection or forged-authority defenses.
- Do not perform hosted CI, POSIX certification, or final independent audit.
- Do not add a new model-driven proof-search phase; Phase 7 consumes the
  already audited candidate and typed evidence.

## Required state/persistence contract

The normal orchestrator must persist these Phase 7 identities before marking a
successful run complete:

```text
root_synthesis_id/hash/file
final_consolidation_id/hash/file
final_consolidation_reaudit_hash
promotion_closure_id/hash/file
phase7_implementation_status
```

The artifacts are immutable and project-local. A promotion closure is only
closed after the existing TruthMutation receipt is durable.

## Implementation outcome

The missing production components are now implemented in
`openprover/math_research/phase7.py` and integrated through the normal
`ResearchOrchestrator._finalize()` path. The implementation records both the
research-root ClaimSnapshot and the later audited ClaimSnapshot because the
existing theorem lifecycle changes status between research start and truth
promotion. Restart recovery verifies completed closures and can finish from a
durable `TRUTH_PROMOTED` checkpoint.

Focused tests cover a successful production-shaped path, restart/resume,
durable truth-promotion recovery, stale-root rejection, open-frontier
rejection, failed-gate rejection, and tampered-proof rejection.

## Post-Phase-7 final hardening

The final candidate pass added strict normal semantic binding completeness and
an explicit pre-backend required-binding guard. It also added regressions for
the partial current-domain and no-backend public entry points, preserved the
map-scoped effect adapter, ran the global Ruff formatter, and revalidated
F-002/F-005/F-007 plus the Phase 7 lifecycle. The formal certification flags
remain `NO` pending independent and external gates.
