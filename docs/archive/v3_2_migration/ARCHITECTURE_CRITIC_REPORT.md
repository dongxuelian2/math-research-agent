# PHASE 5 Architecture Critic Report

## Exact review scope

`ArchitectureCritic` evaluates one exact ArchitecturePatch against its source
ResearchMap, committed ArchitectureReview, bounded probe results, open
obligations, route failures, scope transfers, and root ClaimSnapshot. Typed
verdicts are `APPROVE`, `REJECT`, `REVISE_REQUIRED`,
`INSUFFICIENT_EVIDENCE`, `STALE_REVIEW`, `SCOPE_LOSS`, and
`TRUTH_BOUNDARY_VIOLATION`.

The deterministic evaluator rejects stale bindings, missing transfers,
non-supporting probes, and insufficient critic independence. It does not invent
an alternative ResearchMap and has no map, obligation, or Truth mutation API.

## Independence provenance

`ArchitectureCriticIndependenceReceipt` records review author, patch author,
critic actor, providers, models, same-provider/model/context flags, fresh
context, shared evidence, policy, and policy result. The current minimum policy
requires a different actor and fresh, non-identical context. Same provider or
model is recorded rather than hidden; those facts alone do not override the
actor/context policy.

Even `APPROVE` is non-authoritative. It must be persisted and consumed by a
separate `PatchAuthorization` gate.
