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
PHASE 4 runs carry real ClaimSnapshot and ResearchMap identity; legacy
checkpoints without either remain `REVALIDATION_REQUIRED`. Runtime state remains
file-backed JSON by explicit scope, and no SQLite/WAL authority was introduced.
