# Pre-Root Production Repair Report

This is repair-agent evidence for the candidate independent re-audit. It is
not an independent certification and does not authorize Phase 7.

## Scope and outcome

The repair started from `11826b7c43957c07bd9dc34f01da610bbd431e1b` on
`codex/v3-2-reconciliation`. Production repair code was complete at
`f9f9a93` before this evidence-only commit.

| Gate | Result | Production invariant |
|---|---|---|
| F-002 | `YES` | A rejected or non-winning runtime result is returned only as terminal runtime metadata; the provider body is retained only in the audit artifact. Routed clients raise before semantic parsing. |
| F-005 | `YES` | Destructive map writes require an issued governed-reframe capability, a durable `AUTHORIZED` record, and an exact patch/review/critic/probe/root/map/thesis/scope/ref-chain match. |
| F-007 | `YES` | Reconciliation requires a supplied binding validator; startup reconciles conservatively before domain context and revalidates after context initialization. Formalization and certification routers are root-bound and validator-bound. |
| NF-001 | `PASS` | Late fenced payload cannot reach the routed semantic consumer. |
| NF-002 | `PASS` | Restart and standalone paths preserve or revalidate bindings; missing/stale bindings fail closed. |
| X1 | `PASS` | Restarted C1/v1 result remains unaccepted until a current validator accepts it; stale current context leaves it fenced. |
| X7 | `PASS` | Standalone semantic routing rejects missing binding and accepts a valid bound context; restart validation is covered. |

## Required handoff flags

```text
FORGED_AUTHORITY_REJECTED = YES
LEGITIMATE_AUTHORITY_PRESERVED = YES
RESTART_AUTHORITY_BINDING_PRESERVED_OR_REVALIDATED = YES
STANDALONE_ROUTER_AUTHORITY_FAILS_CLOSED = YES
REJECTED_PAYLOAD_TERMINALITY = YES
LATE_REJECTED_PAYLOAD_SEMANTIC_CONSUMPTION = ZERO
RX_COMPOUND_STALE = PASS
NO_SCOPE_STALE_REPLAY = PASS
AFTER_PROVIDER_RESULT_RECOVERY = PASS
FULL_SUITE = PASS
STATIC_CHECKS = PASS
```

The frozen audit files under `pre_root_reaudit/` were not rewritten. The
frozen synthetic no-scope probe constructs a non-durable authorization and is
therefore intentionally rejected by the repaired production contract; the
production-equivalent stale-closure/explicit-transfer regression remains
green in `test_f001_stale_closure_isolated_and_explicit_transfer_revalidates`.

## Explicit non-certification

```text
READY_FOR_INDEPENDENT_REAUDIT = YES
PRE_ROOT_SYNTHESIS_CERTIFIED = NO
PHASE_7_AUTHORIZED = NO
PHASE_7_STARTING_HEAD = NOT AUTHORIZED
```

Hosted CI was not triggered because no push was authorized. POSIX interruption
was not host-executed on Windows and remains `REQUIRES_HOSTED_CI`.
