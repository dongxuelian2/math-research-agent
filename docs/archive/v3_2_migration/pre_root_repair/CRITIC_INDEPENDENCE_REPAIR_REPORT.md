# F-006 — Critic Independence Repair Report

## Finding and cause

The critic receipt recorded different actors and fresh context but could still
mark a same-model fallback as policy-satisfied. That contradicted the frozen
`DIFFERENT_MODEL` policy and allowed an invalid critic to support destructive
authorization.

## Repair

`ArchitectureCriticIndependenceReceipt` now records the policy and requires a
different actor, fresh context, no shared context, and a different model when
the configured policy is `DIFFERENT_MODEL`. Same provider is not independently
rejected by this rule; same model is. `PatchAuthorization` consumes the receipt
and cannot authorize when `policy_satisfied=false`.

## Proof

- GOV-SAME-MODEL-FALLBACK reports `same_model=True; policy_satisfied=False`.
- `test_same_model_critic_fallback_is_not_independent` and destructive
  authorization negative tests remain green.
- A different-model, fresh-context critic continues to satisfy the policy when
  the other required properties pass.

## Status

`F_006_SAME_MODEL_INDEPENDENCE = CLOSED`
