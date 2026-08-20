# Architecture Deviations

This file distinguishes resolved ownership migrations from remaining, explicitly
bounded deviations. Later-phase work is not presented as v3.2-complete.

## RESOLVED IN PHASE 4: Router owned strategy state

- 规范要求: ModelRouter decides provider/model/effort/quota/fallback only.
- PHASE 4 结果: production CandidateEngine no longer sends failure, stall,
  disagreement, or candidate-existence signals into Router strategy state.
  Merely resolving a model route creates no obligation state. Deprecated
  methods remain callable only for schema-2/direct legacy compatibility and
  explicit compute escalation.

## RESOLVED IN PHASE 4: StrategyFingerprint was long-term memory

- 规范要求: dependency-aware `RouteFailureRecord` owns route failure semantics; semantic similarity cannot auto-ban.
- PHASE 4 结果: native production writes use exact-context
  RouteFailureRecords. StrategyFingerprint is a deprecated legacy execution
  heuristic; compatibility projections are marked `LEGACY_DERIVED` and the
  original records remain intact.

## RESOLVED IN PHASE 4: repair successor was research ontology

- 规范要求: Audit failure creates evidence/blocker and reopens affected obligations; successor runs are execution lineage only.
- PHASE 4 结果: campaign successors retain the same ResearchMap and
  ResearchObligation binding and are explicitly marked
  `EXECUTION_LINEAGE_ONLY`; each run creates another TacticalSession.

## ARCHITECTURE_DEVIATION: filesystem Truth Plane without authoritative v3 runtime

- 规范要求: Distinct Truth/Research/Execution ownership with SQLite/WAL control state and filesystem artifacts.
- 当前实现: PHASE 3 now has immutable ClaimSnapshot and a filesystem truth
  mutation saga over JSON ProjectStore. PHASE 4 adds a separate filesystem
  Research Plane, while AttemptIntent runtime, SQLite/WAL, outbox, leases, and
  cross-process transactions remain absent.
- 不可直接实现的原因: PHASE 3 explicitly requires the thinnest facade and
  forbids stealing later runtime-plane ownership.
- 采用的临时设计: content-addressed truth artifacts, per-file atomic replace,
  and a narrow in-process reentrant compare-and-transition lock. No database or
  distributed-runtime claim is made.
- 未来迁移条件: later execution-plane phases provide SQLite/WAL CAS, outbox,
  recovery, attempt ownership, and parity tests without changing the PHASE 3
  truth object semantics.

## ARCHITECTURE_DEVIATION: filesystem Research Plane without authoritative v3 runtime

- 规范要求: immutable Research Plane semantics eventually participate in the
  authoritative SQLite/WAL runtime and recovery protocol.
- 当前实现: strict immutable JSON artifacts, atomic per-file replacement, and
  mutable rebuildable projections under `research/`.
- 采用的临时设计: correct semantic ownership and deterministic local
  invariants without claiming cross-process transactions, outbox publication,
  leases, or recovery.
- 未来迁移条件: the later runtime phase provides storage/CAS/recovery parity
  without changing ResearchMap, obligation, Directive, closure, or route record
  semantics.

Legacy checkpoint fingerprints remain compatibility metadata only. Current
PHASE 5 runs carry real ClaimSnapshot, ResearchMap, and governance identity; legacy
checkpoints without either remain `REVALIDATION_REQUIRED`. Runtime state remains
file-backed JSON by explicit scope, and no SQLite/WAL authority was introduced.

## ARCHITECTURE_DEVIATION: filesystem Architecture Governance control state

- 规范要求: PHASE 5 must provide durable review scheduling, immutable reviews,
  bounded probes, independent criticism, authorization, and auditable patch
  application while explicitly not implementing the PHASE 6 runtime.
- 当前实现: immutable typed JSON artifacts plus atomically replaced clock and
  active/pending projections under `research/governance/`. Semantics are
  single-process only; no cross-process lease, CAS, outbox, or reconciliation
  claim is made.
- 最小引入面: `GovernanceController` owns scheduling and gate orchestration but
  owns no mathematical strategy, provider routing, obligation resolution, or
  Truth mutation. `ScopeTransfer` is the minimal coverage seam needed to prove
  no scope disappeared during a destructive patch.
- 未来迁移条件: PHASE 6 moves control projections and effect publication to the
  authoritative SQLite/WAL runtime while retaining the immutable artifact
  schemas and exact authorization semantics.

## BOUNDED PHASE 5 SEAM: explicit evidence invalidation input

- 规范要求: stale review/probe/patch evidence must force governance
  revalidation.
- 当前实现: authorization intersects an explicitly supplied invalidation set
  with all review, probe, patch, and critic evidence; any intersection produces
  `REVALIDATION_REQUIRED` and cannot apply.
- 边界: PHASE 5 does not invent a cross-process invalidation event bus or
  journal. Durable authority/dependency invalidation publication and recovery
  belong to PHASE 6. This does not weaken the authorization gate when current
  invalidation facts are supplied.
