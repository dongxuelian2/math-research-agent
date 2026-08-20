# Harness v3.2 Migration Report

## Current phase

`V3_2_CURRENT_PHASE = PHASE 6 DURABLE-RUNTIME BASELINE ESTABLISHED; LOCAL ACCEPTANCE PASS; HOSTED CI PENDING_PUSH`

## Completed capabilities

- Local/remote A/B/C forensic reconciliation against current fetched remote HEAD.
- Preservation classification for all requested capability families.
- Worker/Verifier typed-event production wiring and fail-closed missing-sidecar semantics.
- Typed routing signals: no-progress, failed-route, disagreement, literature request, high-value, structural progress.
- Explicit `PLAN_OVER_CAPACITY` instead of silent task loss.
- Windows uv bootstrap and PowerShell launcher restored; Bash/uv path preserved.
- CI definition aligned with both Ubuntu and Windows.
- Deterministic real production E2E restored, clearly distinct from showcase replay.
- Canonical artifact authority P0: body-bound digest/provenance, resume
  revalidation, scoped blockers, and promotion prerequisite.
- Gemini/Vertex/mock preserved and Codex CLI/OpenAI restored through the current
  provider factory, capabilities, typed response, archive, and resume paths.
- Conservative legacy checkpoint classification, immutable source/provenance,
  provider provenance separation, and canonical-body revalidation are wired to
  the production resume path.
- Typed AssertionIdentity, AuthorityBinding, DependencySnapshot,
  AssumptionSnapshot, and immutable ClaimSnapshot artifacts.
- TruthStoreFacade production integration at run/campaign start, resume,
  context, formalization, audit, lifecycle transition, and promotion.
- Exact specialist/final-audit snapshot binding.
- Intent-first promotion with current-truth reconstruction, typed stale
  comparison, serialized compare-and-transition, blocked evidence, and receipt.
- Immutable, versioned, non-authoritative ResearchMaps with a hard no-scope-loss
  invariant and explicit ClaimSnapshot rebase provenance.
- Durable ResearchObligation semantic revisions and independent research-only
  dispositions; WorkerTask, CandidateAttempt, run, and provider state remain
  execution projections.
- Immutable Directive and TacticalSession bindings integrated into the real
  Planner/Worker/Verifier production route.
- SessionClosure raw retention, typed evidence projection, deterministic
  Evidence-to-Obligation gate, and ResearchMap revision.
- Dependency-aware RouteFailureRecord, reverse invalidation index, legacy
  StrategyFingerprint adapter, and production ModelRouter ownership cleanup.
- Campaign checkpoints carry map/version/open frontier/directive/session/root
  bindings; legacy checkpoints without a map require revalidation.
- Evidence-bound StructuralEffect classification separates activity, tactical
  progress, and structural progress without an aggregate score.
- Durable logical ArchitectureReview clocks enforce mandatory, repeated-route,
  long-blocked, tactical-without-structural, map-change, repair-loop,
  literature, and human triggers. Only a committed typed review resets them.
- Immutable twelve-dimension ArchitectureReviews and bounded StructuralProbe
  plan/results are exact-root/map governance artifacts, not WorkerTasks.
- Exact-patch ArchitectureCritics retain actor/provider/model/context
  independence provenance and cannot mutate Research or Truth state.
- ArchitecturePatch, ScopeTransfer, PatchAuthorization, and immutable
  application history guard destructive ResearchMap reframes; every old
  obligation remains with an explicit disposition.
- Run/campaign checkpoints preserve governance due state and active/pending
  control identity. Legacy checkpoints require a current governance review and
  never fabricate a past review.
- Literature mechanism conflicts signal governance through the review clock;
  ModelRouter remains a compute allocator.
- Project-local SQLite schema 2 with WAL, foreign keys, FULL synchronous
  durability, transactional schema migration, integrity inspection, and strict
  control-plane-only ownership.
- RuntimeBackend, stable LogicalJob identity, immutable physical
  AttemptIntent, transactional outbox, typed attempt state machine,
  append-only transition journal, lease/heartbeat, generation fencing, and
  durable cancellation.
- Filesystem-first, hash-verified result artifacts with idempotent DB ingestion,
  one accepted result across duplicate successes, and deterministic
  reconciliation for every modeled DB/filesystem split.
- EffectSlot cross-store sagas for real Research closure, Truth promotion, and
  Architecture Governance effects. Domain replay recovers existing identities
  and never treats execution failure or lease expiry as mathematical failure.
- Truth promotion now persists prepared recovery evidence before theorem CAS;
  a crash after theorem transition and before receipt is validated and repaired
  without a second status transition.
- Shared Windows/POSIX process-tree control and a durable cancel/complete race
  policy; the formerly blocked interruption test runs on Windows without skips.
- Runtime ownership is wired through orchestrator, routed providers,
  formalization, certification, and provider-smoke production paths. Startup
  migrates/reconciles before Truth, Research, and governance resume checks.

## Partial capabilities

- OpenProver public lifecycle hooks: Worker/Verifier batch hooks exist; the complete v3.2 `_run_one_step()/step()/StepOutcome` facade is not implemented.
- CI: definitions and exact local commands pass; hosted GitHub jobs were not run because this branch was not pushed.
- External provider dispatch remains deliberately at-least-once; semantic
  effects are exactly-once through accepted-result identity and EffectSlot.
- Planner/pipeline/routing JSON remains a portable compatibility and desired-
  work projection. It is not current authority for attempts, leases, outbox,
  accepted results, or effects.
- A domain-specific recovery adapter is required for any new cross-store
  semantic effect; unknown partial state fails to manual review.

## Unstarted capabilities

- VerifierIndependenceReceipt.
- Full CoverageAnchor/Transfer and richer DecisionBasis.
- ROOT_SYNTHESIS and consolidation re-audit.

## Blockers

No external blocker prevents PHASE 6 local completion. Hosted Linux/Windows CI
cannot be claimed because no push was authorized. The previous Windows
interrupt blocker is retired: its three-test file passes locally.

## Next safe migration frontier

1. Stop for user audit of the frozen PHASE 6 local commits and evidence.
2. After explicit authorization, the exact next safe frontier is PHASE 7:
   ROOT_SYNTHESIS × Final Consolidation × Promotion Closure.
3. Do not infer PHASE 7 completion from the durable-runtime baseline.

## Preservation statement

No validated capability was intentionally deleted. No theorem registry or mathematical result was mutated outside isolated test fixtures. No remote push was performed.
