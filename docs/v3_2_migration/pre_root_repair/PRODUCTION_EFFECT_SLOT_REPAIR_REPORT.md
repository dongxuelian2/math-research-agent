# F-003 — Production EffectSlot Repair Report

## Finding and cause

`RuntimeEffectCoordinator` existed, but normal orchestrator finalization still
called Research, Governance, and Truth domain mutations directly. Adapter tests
therefore proved exactly-once behavior without proving the production call path.

## Repair

Normal semantic finalization now follows:

`accepted internal result → binding validation → unique EffectSlot → domain
adapter → durable domain identity/receipt → EffectSlot acknowledgement`.

Stable domain identities, not timestamps or attempt IDs alone, form the
idempotency keys. The production path wraps SessionClosure application,
ResearchMap resolution, StructuralEffect, governance review signals/session
records/route-failure records, route-failure Research effects, and TruthMutation.
The coordinator remains an exactly-once protocol: it does not decide theorem
truth, obligation validity, or patch authorization.

Recovery first looks for the exact domain identity. A crash after domain apply
and before slot ACK therefore acknowledges the existing identity instead of
creating a second map revision, receipt, clock increment, or effect.

## Call-graph audit

The normal `ResearchOrchestrator` semantic finalization call sites now all enter
`RuntimeEffectCoordinator`. Direct domain calls found by the static search are:

- domain calls inside `runtime_effects.py`, which are the intentionally bounded
  adapters owned by the coordinator;
- `campaign.py`'s `governance.signal_review`, an explicit campaign resume/admin
  checkpoint path, not normal orchestrator semantic finalization;
- `orchestrator._transition_truth`, which is an intermediate lifecycle status
  update on a failure path, not final TruthMutation promotion; final promotion
  uses `apply_truth_transition`.

No normal production semantic effect bypass remains. Unit tests, migration
compatibility, and explicit admin/domain APIs remain allowed and are listed
above rather than silently treated as normal execution.

## Proof

- The production EffectSlot regression checks `APPLY_SESSION_CLOSURE`,
  `COMMIT_STRUCTURAL_EFFECT`, `RECORD_GOVERNANCE_SESSION`, and
  `APPLY_TRUTH_MUTATION` in one real orchestrator run.
- `test_f003_domain_apply_before_ack_reconciles_by_effect_identity` injects
  `AFTER_DOMAIN_APPLY_BEFORE_ACK` and recovers one existing map effect.
- The X5, X6, X8, and X9 reruns are `CERTIFIED`.

## Status

`F_003_PRODUCTION_EFFECT_SLOT = CLOSED`
