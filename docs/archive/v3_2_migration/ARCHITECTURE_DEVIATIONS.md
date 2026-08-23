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

## RESOLVED IN PHASE 6: filesystem Truth Plane without authoritative v3 runtime

- 规范要求: Distinct Truth/Research/Execution ownership with SQLite/WAL control state and filesystem artifacts.
- PHASE 6 结果: SQLite/WAL now owns execution control while filesystem Truth
  artifacts retain semantic authority. EffectSlot wraps the existing intent,
  snapshot-bound CAS, prepared recovery evidence, and receipt protocol. A crash
  in the theorem/receipt split is repaired without moving theorem truth into SQL.

## RESOLVED IN PHASE 6: filesystem Research Plane without authoritative v3 runtime

- 规范要求: immutable Research Plane semantics eventually participate in the
  authoritative SQLite/WAL runtime and recovery protocol.
- PHASE 6 结果: strict Research artifacts remain filesystem-authoritative;
  accepted runtime results enter a unique EffectSlot and domain recovery finds
  the existing closure/disposition/map identity instead of creating vN+1.

Legacy checkpoint fingerprints remain compatibility metadata only. Current
runs carry real ClaimSnapshot, ResearchMap, governance, and runtime identity;
legacy checkpoints never fabricate runtime history. SQLite owns current
execution control, while JSON checkpoints remain portable projections.

## RESOLVED IN PHASE 6: filesystem Architecture Governance control state

- 规范要求: PHASE 5 must provide durable review scheduling, immutable reviews,
  bounded probes, independent criticism, authorization, and auditable patch
  application while explicitly not implementing the PHASE 6 runtime.
- PHASE 6 结果: governance artifacts and authorization remain unchanged;
  accepted runtime work uses EffectSlot, and replay recovers the same review
  clock or patch application/map identity. SQL success alone cannot authorize a
  patch or reset a review clock.

## BOUNDED SEAM: explicit evidence invalidation input

- 规范要求: stale review/probe/patch evidence must force governance
  revalidation.
- 当前实现: authorization intersects an explicitly supplied invalidation set
  with all review, probe, patch, and critic evidence; any intersection produces
  `REVALIDATION_REQUIRED` and cannot apply.
- 边界: the runtime journal records execution/control causality; it is not a
  generic domain event bus. Current invalidation facts remain explicit inputs
  to authorization and unknown partial state fails closed.

## BOUNDED PHASE 6 SEAM: external execution is at-least-once

- Provider delivery cannot be made exactly-once across a process crash and an
  external service boundary. Duplicate physical Attempts are retained.
- LogicalJob acceptance and EffectSlot make the semantic consequence
  exactly-once. Billing/provider provenance is never discarded.

## BOUNDED PHASE 6 SEAM: compatibility projections remain

- Planner/pipeline/routing JSON can still express desired work and portable
  resume context, but cannot authorize attempt, lease, outbox, accepted-result,
  or effect transitions.
- In-scope production external-execution entrypoints have no remaining direct
  ownership bypass. Provider adapter unit tests may call transports directly;
  this is test isolation, not a production authority path.
- Each future cross-store effect kind must supply a deterministic domain
  recovery adapter. Unknown effects become `MANUAL_REVIEW_REQUIRED`.

## PENDING: hosted CI

Local Windows evidence is complete, including the interruption test. Hosted
Linux/Windows jobs remain `PENDING_PUSH` because no push was authorized.

## RESOLVED IN PRE-ROOT REPAIR: seven frozen blockers

- Stale SessionClosure now fails closed on exact map/disposition identity and
  has a typed transfer revalidation path.
- Expired lease results retain artifacts but cannot become authoritative.
- Routed semantic jobs persist immutable ClaimSnapshot/ResearchMap bindings and
  validate them at acceptance and EffectSlot preparation.
- Normal orchestrator semantic finalization uses the durable EffectSlot
  coordinator; direct domain calls in adapters and explicit admin/migration
  paths are not normal production semantic bypasses.
- `AFTER_PROVIDER_RESULT` is classified as `UNKNOWN_EXECUTION` and cannot leave
  an outbox permanently `DISPATCHED`.
- Strategic thesis changes require authorized governance; same-model critics
  fail the configured independence policy.

The complete repair matrix and limitations are in
`pre_root_repair/PRE_ROOT_BLOCKER_REPAIR_MATRIX.md`. These changes do not
authorize or begin Phase 7.
