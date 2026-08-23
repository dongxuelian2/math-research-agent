# Pre-Root Blocker Repair Plan

## Scope

This repair slice closes only the seven findings named in the frozen
pre-root audit: F-001 through F-007. The authoritative inputs remain
`docs/v3_2_migration/pre_root_audit/FINDINGS.md`,
`PRE_ROOT_SYNTHESIS_CERTIFICATION.md`, and
`CROSS_PLANE_TEST_MATRIX.md`; those files are intentionally unchanged.

Starting integration commit: `f48269c8a929a67b90eff56af4a200f2ed757c61`
(`codex/v3-2-reconciliation`). No push is authorized or performed.

## Repair order

1. Reject stale SessionClosure resolution and add explicit typed transfer
   revalidation (F-001).
2. Fence expired lease results while retaining their artifacts (F-002).
3. Persist and validate immutable cross-plane bindings through routed runtime
   objects and EffectSlot preparation (F-007).
4. Route normal production semantic finalization through RuntimeEffectCoordinator
   and durable EffectSlots (F-003).
5. Classify the `AFTER_PROVIDER_RESULT` crash window as durable unknown
   execution instead of leaving `DISPATCHED` stranded (F-004).
6. Make strategic-thesis changes require typed governance authorization (F-005).
7. Make same-model critic fallback fail the configured independence policy
   and block destructive authorization (F-006).

## Acceptance gates

- F-001 through F-007 are `CLOSED` in the repair matrix.
- Direct repaired probes X1, X2, X4, X12, GOV-THESIS-BYPASS, and
  GOV-SAME-MODEL-FALLBACK are `CERTIFIED`.
- The complete X1–X16 rerun has no `PARTIAL` or `FAILED` result. Any
  `CERTIFIED_WITH_LIMITATION` result names the precise non-claim.
- Focused regressions include the B1–B16 scenarios listed in the request;
  the production EffectSlot test checks actual `ResearchOrchestrator` output.
- Full local-safe regression, interrupt-race, lint, compile, lock, and diff
  checks are recorded before handoff.

## Explicit non-scope

`ROOT_SYNTHESIS_FULL`, `FINAL_CONSOLIDATION_FULL`, and all Phase 7
implementation remain not started. This document does not self-authorize
Phase 7 or certify the frozen pre-root synthesis; it only records repair
evidence and readiness for an independent re-audit.
