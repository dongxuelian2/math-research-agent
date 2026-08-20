# Phase 7 Implementation Report

## Specification recovered

The implementation follows the newest repository-native migration contract:

- `docs/v3_2_migration/V3_2_MIGRATION_REPORT.md` identifies the next frontier as
  `ROOT_SYNTHESIS × Final Consolidation × Promotion Closure`.
- `docs/v3_2_migration/V3_2_IMPLEMENTATION_MATRIX.md` defines ROOT_SYNTHESIS as
  exact root evidence synthesis and Final Consolidation as an immutable promoted
  proof with consolidation re-audit.
- `CLAIM_SNAPSHOT_REPORT.md`, `SESSION_CLOSURE_REPORT.md`, and
  `TRUTH_MUTATION_REPORT.md` define the existing exact-identity inputs and the
  separation between Research Plane resolution and Truth Plane promotion.

No older planning document was found that expands Phase 7 into a new proof
search, router redesign, authority redesign, or certification campaign.

## Implemented components

### RootSynthesis

`Phase7Store.synthesize_root()` creates a schema-v1, content-addressed,
immutable artifact and a hashed Markdown synthesis body. It requires:

- a compatible current audited ClaimSnapshot comparison;
- a passing `AuditGate` bound to that audited snapshot;
- a ResearchMap for the exact theorem with no open or blocked frontier;
- a SessionClosure with completed execution and typed validated evidence;
- exact map/root/closure/obligation identity and an authorized one-step
  resolution successor when closure precedes the resolved current map;
- immutable candidate and audit artifact byte references.

The existing lifecycle has two legitimate ClaimSnapshot identities: the
research map is bound when the theorem enters research, while status transitions
produce the later audited snapshot used for TruthMutation. RootSynthesis records
both explicitly as `root_claim_snapshot_hash` and
`audited_claim_snapshot_hash`; neither is treated as a wildcard.

### FinalConsolidation

`Phase7Store.consolidate()` writes an immutable final proof body containing a
machine-readable provenance manifest and the exact candidate bytes. It writes a
content-addressed consolidation record and a deterministic consolidation
re-audit record. The re-audit verifies bytes, identity, artifact hashes, and the
existing passing audit gate; it is not a new model-driven mathematical audit.

The theorem’s TruthMutation metadata now points to this final proof body rather
than the pre-Phase-7 resolution report.

### PromotionClosure

`Phase7Store.close_promotion()` requires the persisted RootSynthesis and
FinalConsolidation plus the durable TruthMutation intent and `PROVED` receipt.
It creates an immutable closure linking the audited snapshot, resulting
snapshot, truth mutation, consolidation re-audit, and final status.

### Orchestration and recovery

The normal `ResearchOrchestrator._finalize()` path now advances through:

```text
AUDITS_READY
  -> ROOT_SYNTHESIS
  -> FINAL_CONSOLIDATION
  -> TRUTH_PROMOTED
  -> PROMOTION_CLOSED
  -> COMPLETE
```

State fields persist every required identity and the explicit certification
freeze flags. Completed runs verify their promotion closure on resume. If a
process stops after TruthMutation is durable but before the final closure is
recorded, the `TRUTH_PROMOTED` checkpoint reloads the immutable artifacts and
finishes the closure without starting another proof attempt. Phase 7 artifact
creation is deterministic and idempotent across retries.

## Production files changed

- `openprover/openprover/math_research/phase7.py` — Phase 7 types, validation,
  immutable storage, synthesis, consolidation, re-audit, closure, and recovery
  verification.
- `openprover/openprover/math_research/orchestrator.py` — normal-path
  integration, state transitions, final-proof metadata, and resume recovery.
- `openprover/openprover/math_research/__init__.py` — public exports for the
  Phase 7 types and store.

## Test files changed

- `openprover/tests/math_research/test_phase7_implementation.py` — production-
  shaped success, completed-run resume, durable TruthMutation recovery, stale
  root/open frontier/failed gate rejection, and tampered final-proof rejection.

## Known implementation observation

`IMPLEMENTATION_OBSERVATION-001` was encountered and resolved in the Phase 7
integration: research-root and audited/current ClaimSnapshot identities differ
across ordinary theorem status transitions. The implementation preserves both
identities explicitly. This observation is not a new formal audit finding and
does not alter NF-003, NF-004, F-002, F-005, or F-007.

## Out of scope

NF-003 and NF-004 were not repaired. Existing terminal-rejection and
forged-authority defenses were reused. No old audit fact was edited; no hosted
CI, POSIX certification, final independent audit, push, or formal certification
was performed.
