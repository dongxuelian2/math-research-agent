# Harness v3.2 Migration Report

## Current phase

`V3_2_CURRENT_PHASE = PHASE 1 IMPLEMENTED / LOCAL ACCEPTANCE PASS; HOSTED CI PENDING; PHASE 2 NOT STARTED`

## Completed capabilities

- Local/remote A/B/C forensic reconciliation against current fetched remote HEAD.
- Preservation classification for all requested capability families.
- Worker/Verifier typed-event production wiring and fail-closed missing-sidecar semantics.
- Typed routing signals: no-progress, failed-route, disagreement, literature request, high-value, structural progress.
- Explicit `PLAN_OVER_CAPACITY` instead of silent task loss.
- Windows uv bootstrap and PowerShell launcher restored; Bash/uv path preserved.
- CI definition aligned with both Ubuntu and Windows.
- Deterministic real production E2E restored, clearly distinct from showcase replay.

## Partial capabilities

- OpenProver public lifecycle hooks: Worker/Verifier batch hooks exist; the complete v3.2 `_run_one_step()/step()/StepOutcome` facade is not implemented.
- Provider abstraction: Gemini/Vertex/mock are current; Codex/OpenAI and optional legacy providers remain absent.
- CI: definitions and exact local commands pass; hosted GitHub jobs were not run because this branch was not pushed.

## Unstarted capabilities

- Explicit legacy checkpoint migration classes and provenance.
- Canonical project artifact body resolver and promotion guard.
- VerifierIndependenceReceipt.
- TruthStoreFacade, ClaimSnapshot, identity hash-domain separation, TruthMutation saga.
- ResearchMap, CoverageAnchor/Transfer, ResearchObligation, DecisionBasis, reverse invalidation.
- Mandatory Architecture Review, Structural Probe, Architecture Critic authorization.
- SQLite/WAL authoritative runtime, outbox, AttemptIntent, lease, reconciliation, effect slots.
- Pure ModelRouter, Campaign/Session ownership migration, ROOT_SYNTHESIS, consolidation re-audit.

## Blockers

No external blocker prevents continued work. The deliberate phase gate prevents combining provider/checkpoint/canonical-authority restoration with Research/Execution ownership rewrites before preservation evidence is stable.

## Next safe migration frontier

1. Restore Codex and OpenAI adapters behind the current Provider interface and revive their capability-level contracts.
2. Add checkpoint classification with immutable migration provenance.
3. Implement the canonical proof/replay artifact resolver, scoped authority blocking, handoff provenance, resume revalidation, and promotion guard.
4. Only then start Truth facade/ClaimSnapshot. Do not start ResearchMap or SQLite in the same patch.

## Preservation statement

No validated capability was intentionally deleted. No theorem registry or mathematical result was mutated outside isolated test fixtures. No remote push was performed.
