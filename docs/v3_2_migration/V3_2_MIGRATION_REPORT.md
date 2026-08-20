# Harness v3.2 Migration Report

## Current phase

`V3_2_CURRENT_PHASE = PRE-TRUTH-PLANE MIGRATION BASELINE IMPLEMENTED; LOCAL ACCEPTANCE PASS; HOSTED CI PENDING_PUSH`

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

## Partial capabilities

- OpenProver public lifecycle hooks: Worker/Verifier batch hooks exist; the complete v3.2 `_run_one_step()/step()/StepOutcome` facade is not implemented.
- CI: definitions and exact local commands pass; hosted GitHub jobs were not run because this branch was not pushed.

## Unstarted capabilities

- VerifierIndependenceReceipt.
- TruthStoreFacade, ClaimSnapshot, identity hash-domain separation, TruthMutation saga.
- ResearchMap, CoverageAnchor/Transfer, ResearchObligation, DecisionBasis, reverse invalidation.
- Mandatory Architecture Review, Structural Probe, Architecture Critic authorization.
- SQLite/WAL authoritative runtime, outbox, AttemptIntent, lease, reconciliation, effect slots.
- Pure ModelRouter, Campaign/Session ownership migration, ROOT_SYNTHESIS, consolidation re-audit.

## Blockers

No external blocker prevents continued work. The deliberate phase gate prevents combining provider/checkpoint/canonical-authority restoration with Research/Execution ownership rewrites before preservation evidence is stable.

## Next safe migration frontier

1. Re-run the complete local-safe regression and freeze the checkpoint phase in
   its own local commit.
2. Stop at the requested boundary for user audit. Truth facade/ClaimSnapshot,
   ResearchMap, ResearchObligation, Architecture Review, and SQLite remain
   explicitly not started.

## Preservation statement

No validated capability was intentionally deleted. No theorem registry or mathematical result was mutated outside isolated test fixtures. No remote push was performed.
