# Harness v3.2 Architecture Decisions

Status: reviewed against the supplied `Harness_v3_2_合并架构与冻结规范.md`; implementation is **not FROZEN**.

## Frozen direction

1. Truth, Research, Execution, and Artifact planes have distinct ownership.
2. OpenProver remains the Tactical Kernel; Planner remains a Tactical Coordinator.
3. `ResearchObligationSpec` is the durable frontier; Directive and WorkerTask are execution-local.
4. ResearchMap versions are immutable, revisable, multi-view, and never theorem authority.
5. Coverage is preserved through immutable anchors/dispositions/transfers; no partition is sacred and no tracked scope may vanish silently.
6. Raw Worker prose never mutates durable research state. Typed validated events are suggestions/evidence inputs, not theorem authority.
7. ModelRouter selects compute only. Current failure/strategy state inside the Router is compatibility debt.
8. SQLite/WAL will own normalized v3 research/runtime control state; filesystem owns large immutable artifacts; ProjectStore truth writes go through a facade and cross-store saga.
9. External work uses LogicalJob→AttemptIntent→ResultArtifact→TrustReceipt→AcceptedEffect. Work may be at-least-once; effect slots are exactly-once.
10. Unknown schema/policy/execution fails closed or requires migration/revalidation.
11. Canonical proof/replay authority requires the actual body with verified hash and provenance. Hash-only/summary/extract/model reconstruction is never authority.
12. Promoted proof bodies are immutable; proof-body consolidation creates a new candidate and requires a new final audit.

## Phase boundary chosen in this change

Allowed: forensic reconciliation, P0 preservation repair, public event hooks, explicit capacity rejection, Windows/Bash/CI parity, deterministic production E2E, and migration documentation.

Forbidden: ResearchMap/SQLite implementation, Router strategy rewrite, successor ontology deletion, provider removal, legacy snapshot rewrite, theorem registry mutation outside isolated tests, or canonical-authority claims without a body resolver.

This boundary is an incremental facade/adapter change. It does not silently reinterpret the v3.2 target.
