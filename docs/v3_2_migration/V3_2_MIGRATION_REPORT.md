# Harness v3.2 Migration Report

## Current phase

`V3_2_CURRENT_PHASE = PHASE 4 RESEARCH-PLANE FOUNDATION BASELINE ESTABLISHED; LOCAL ACCEPTANCE PASS; HOSTED CI PENDING_PUSH`

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

## Partial capabilities

- OpenProver public lifecycle hooks: Worker/Verifier batch hooks exist; the complete v3.2 `_run_one_step()/step()/StepOutcome` facade is not implemented.
- CI: definitions and exact local commands pass; hosted GitHub jobs were not run because this branch was not pushed.
- PHASE 3 filesystem saga is process-serialized, not a cross-process/database
  transaction; that guarantee belongs to the later SQLite/WAL phase.

## Unstarted capabilities

- VerifierIndependenceReceipt.
- Full CoverageAnchor/Transfer and richer DecisionBasis.
- Mandatory Architecture Review, Structural Probe, Architecture Critic authorization.
- SQLite/WAL authoritative runtime, outbox, AttemptIntent, lease, reconciliation, effect slots.
- ROOT_SYNTHESIS and consolidation re-audit.

## Blockers

No external blocker prevents PHASE 4 completion. The managed Windows interrupt
subprocess remains an environment-blocked excluded test exactly as documented
at the baseline; it does not weaken the deterministic in-process truth-race tests.

## Next safe migration frontier

1. Freeze PHASE 4 local-safe evidence in its own local commit.
2. Stop for user audit. The exact next safe frontier is PHASE 5 Architecture
   Review × Structural Probe × Architecture Critic. SQLite/WAL remains a later,
   explicitly unstarted runtime phase.

## Preservation statement

No validated capability was intentionally deleted. No theorem registry or mathematical result was mutated outside isolated test fixtures. No remote push was performed.
