# TruthMutation Normative Diff

This document compares the v3.2 TruthMutation saga to the current static
implementation. It does not execute promotion, mutate a canonical theorem, or
perform destructive crash testing.

## Saga comparison

| Spec saga element | Current implementation | Classification | Evidence |
|---|---|---|---|
| Durable intent before mutation | `TruthStoreFacade.compare_and_transition` builds and writes `TruthMutationIntent` before compare/transition. | SEMANTIC_EQUIVALENT | `truth_store.py:242–304`; `TruthMutationIntent` includes claim/assertion/audit/policy hashes. |
| Expected ClaimSnapshot ref | `claim_snapshot_hash` and `assertion_identity_hash` in intent. | SEMANTIC_EQUIVALENT | Exact hash binding exists; spec calls for a durable snapshot ref/id plus audit receipt hash. |
| Expected old/target status | `from_status` / `requested_to_status`. | EXACT | `TruthMutationIntent.capture` and prepared record enforce the values. |
| Audit receipt binding | `audited_claim_snapshot_hash` and `audit_artifacts`. | PARTIALLY_COMPLIANT | Gate identity and artifact hashes are carried, but there is no normative `audit_receipt_hash` object. |
| Mutation policy identity | `trust_policy_fingerprint`. | PARTIALLY_COMPLIANT | A single fingerprint is captured; structured `TrustPolicyRef` compatibility is absent. |
| Compare-and-transition | `ProjectStore.compare_and_transition` checks status and identity-critical fields under an in-process lock before `_transition_locked`. | SEMANTIC_EQUIVALENT | `project.py:407–491`; it prevents identity field metadata updates. |
| Atomic `v3_truth_binding` marker | No `v3_truth_binding` write or validator was found. | MISSING / CONFIRMED_NONCOMPLIANT | `_transition_locked` writes status/history and metadata only; static inventory has zero marker hits. |
| Receipt after transition | `TruthMutationReceipt` is captured and written after theorem transition. | PARTIALLY_COMPLIANT | Durable receipt exists, but it is not written in the same theorem JSON replace as the required binding marker. |
| Reconciliation/recovery | `_recover_prepared_mutation` checks status/history/actor/reason/metadata and recaptures snapshot, then creates a receipt. | PARTIALLY_COMPLIANT | Recovery exists, but it cannot verify the required complete binding marker. |
| Crash boundary | `_write_json` uses temp replace; truth lock is process-local. | NEEDS_DEEP_RECOVERY_AUDIT | No fsync/dir-fsync proof in `ProjectStore`; cross-process and theorem-write/receipt-write crash semantics need isolated deterministic checks. |

## Required crash audit queue

The next recovery audit must isolate and deterministically exercise at least:

1. intent committed before compare;
2. prepared record committed before theorem replace;
3. theorem status replace before receipt write;
4. receipt write interrupted or duplicated;
5. restart with status-only `PROVED` and no complete binding;
6. restart with complete binding but missing receipt;
7. concurrent compare from two processes;
8. stale snapshot/old audit receipt at every resume boundary.

The acceptance rule is exact: only a complete `v3_truth_binding` matching the
intent, ClaimSnapshot, assertion hash, audit receipt, and policy may reconcile
to `PROVED`; every partial case must remain blocked or be explicitly
revalidated.

## Finding

`TRUTH_MUTATION_STATUS=NEEDS_DEEP_RECOVERY_AUDIT`. The current implementation
has a meaningful intent/prepare/compare/receipt structure, but the required
ProjectStore v3 binding marker and cross-store crash proof are not present.
No truth promotion was executed in Pass B.
