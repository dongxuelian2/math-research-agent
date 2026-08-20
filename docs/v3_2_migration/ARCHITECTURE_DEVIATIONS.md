# Architecture Deviations

These are explicit pre-existing production deviations retained temporarily; none is presented as v3.2-complete.

## ARCHITECTURE_DEVIATION: Router owns strategy state

- 规范要求: ModelRouter decides provider/model/effort/quota/fallback only.
- 当前实现: `routing.py` stores failure counters, stalled cycles, disagreement escalation, and high-value promotion.
- 不可直接实现的原因: ResearchMap/ResearchObligation and their authoritative store do not yet exist; deleting this state would silently remove validated escalation behavior.
- 采用的临时设计: Preserve typed event→Router compatibility behavior and label it migration debt.
- 未来迁移条件: Durable Research Plane, dependency-aware RouteFailureRecord, scheduler projection, and regression parity exist.

## ARCHITECTURE_DEVIATION: StrategyFingerprint remains long-term memory

- 规范要求: dependency-aware `RouteFailureRecord` owns route failure semantics; semantic similarity cannot auto-ban.
- 当前实现: `StrategyFingerprintStore` freezes a repeated strategy after two failures.
- 不可直接实现的原因: No obligation revision/dependency snapshot identity exists in production.
- 采用的临时设计: Retain unchanged as a compatibility adapter; do not expand its authority.
- 未来迁移条件: Exact obligation refs, dependency snapshots, failure domains, evidence refs, and reopen rules are implemented.

## ARCHITECTURE_DEVIATION: repair successor is research ontology

- 规范要求: Audit failure creates evidence/blocker and reopens affected obligations; successor runs are execution lineage only.
- 当前实现: `campaign.py` models repair as a successor run/world.
- 不可直接实现的原因: ResearchObligation/ResearchMap lifecycle is absent; removal would lose resume and repair behavior.
- 采用的临时设计: Preserve v2 lifecycle without claiming v3 semantics.
- 未来迁移条件: Campaign/Session/CandidateAttempt separation and obligation reopen path have production E2E parity.

## ARCHITECTURE_DEVIATION: no authoritative v3 planes/runtime

- 规范要求: Distinct Truth/Research/Execution ownership with SQLite/WAL control state and filesystem artifacts.
- 当前实现: JSON ProjectStore plus file-backed campaign/routing/pipeline state; no ClaimSnapshot, ResearchMap, AttemptIntent, outbox, or truth saga.
- 不可直接实现的原因: Phase 0/1 are preservation gates and explicitly forbid a wholesale runtime rewrite.
- 采用的临时设计: Narrow hooks/adapters only; existing authority semantics remain unchanged.
- 未来迁移条件: Phase 2 deterministic runtime and Phase 3 Truth schemas/tests pass crash, race, stale, and migration acceptance matrices.
