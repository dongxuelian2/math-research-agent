# PRE-ROOT-SYNTHESIS CERTIFICATION

## Final verdict

```text
PRE_ROOT_SYNTHESIS_CERTIFIED = NO
PHASE_7_AUTHORIZED = NO
AUDITOR_MODIFIED_PRODUCTION_CODE = NO
```

The denial is evidence-based. P0 stale-authority failures were reproduced in
X2 and X4; P1 runtime-recovery and ownership failures were also reproduced or
confirmed by production-path tracing. This package does not implement Phase 7
or repair the findings.

## Git and scope

| Item | Value |
|---|---|
| Starting branch | `codex/v3-2-reconciliation` |
| Starting HEAD | `6ada9282f31ac5cdafb2353dc9c19e2c4fe8aa76` |
| Corrected Phase 6 range count | `12` |
| Ending HEAD | documentation-only audit commit recorded in the final handoff |
| Auditor commits | local documentation/probe commit; no push |
| Production code modified | `NO` |
| Hosted CI | `PENDING_PUSH` / not run; no push authorized |

## Capability certification matrix

| Capability | Verdict |
|---|---|
| Truth plane ownership | `CERTIFIED_WITH_LIMITATION` |
| Research plane ownership | `FAILED` |
| Architecture governance ownership | `PARTIAL` |
| Execution control ownership | `PARTIAL` |
| SQLite single control authority | `CERTIFIED_WITH_LIMITATION` for execution; `FAILED` for semantic effects |
| Production provider intent gate | `CERTIFIED_WITH_LIMITATION` |
| Claim stale recovery guard | `PARTIAL` |
| Canonical authority recovery guard | `CERTIFIED_WITH_LIMITATION` |
| Late-result fencing | `FAILED` |
| Research effect exactly once | `PARTIAL` |
| Truth effect exactly once | `PARTIAL` |
| Governance effect exactly once | `PARTIAL` |
| Cross-store saga recoverability | `PARTIAL` |
| No-scope-loss under crash/replay | `FAILED` |
| Old Session × new Map isolation | `FAILED` |
| ArchitectureReview replay safety | `CERTIFIED_WITH_LIMITATION` |
| ArchitecturePatch replay safety | `CERTIFIED_WITH_LIMITATION` |
| Outbox at-least-once safety | `FAILED` for unknown DISPATCHED execution; otherwise limited |
| Multi-attempt single acceptance | `CERTIFIED_WITH_LIMITATION` |
| Project isolation | `CERTIFIED` |
| Cross-platform interruption | `CERTIFIED_WITH_LIMITATION` |
| Planner not Research architect | `CERTIFIED_WITH_LIMITATION` |
| Worker not Truth authority | `CERTIFIED_WITH_LIMITATION` |
| Router compute-only | `CERTIFIED_WITH_LIMITATION` |
| StrategyFingerprint compatibility-only | `CERTIFIED_WITH_LIMITATION` |
| Successor run execution-only | `CERTIFIED_WITH_LIMITATION` |

## Evidence reviewed

The audit reviewed the supplied Phase 3--6 merge specification, all relevant
reports under `docs/v3_2_migration/`, the implementation matrix, Git history,
production source ownership, SQLite schema/current-state code, artifact and
reconciler code, provider routing, orchestrator finalization, Truth/Research/
Governance stores, and the existing deterministic tests.

## Tests run

- full local-safe suite: `268 passed`;
- focused Truth/Research/Governance/Runtime/migration slice: `80 passed`;
- Windows interrupt-race script: PASS, including actual subprocess tree
  termination and the 500-trial worker race;
- POSIX process-group branch: deterministic monkeypatch/unit path PASS;
  POSIX host execution was not performed;
- new cross-plane probe runner: X2, X4, `AFTER_PROVIDER_RESULT`, thesis
  bypass, same-model policy FAIL; X1 PARTIAL as recorded in the matrix.

## Blocking findings

### P0

- `F-001`: stale SessionClosure re-resolves a superseded O1 in a newer map.
- `F-002`: expired lease result is ingested as authoritative.

### P1

- `F-003`: production semantic effects bypass EffectSlot ownership.
- `F-004`: `AFTER_PROVIDER_RESULT` strands a `DISPATCHED` outbox.
- `F-005`: strategic thesis mutation bypasses ArchitecturePatch authorization.
- `F-006`: same-model critic fallback is marked independent.
- `F-007`: routed attempts omit claim/map/governance bindings.

No separate P2/P3 finding changes the verdict.

## Minimum repair frontier before Phase 7

1. Fence result ingestion on lease expiry, not only token/generation/state;
   retain late artifacts as non-authoritative.
2. Add exact ClaimSnapshot and ResearchMap version/hash compatibility to
   runtime acceptance/effect preparation and routed AttemptIntent payloads.
3. Route production Truth, Research, and Governance semantic effects through
   durable EffectSlot adapters with deterministic domain recovery.
4. Add a `DISPATCHED` unknown-execution recovery policy that adopts a manifest,
   creates a stable retry/manual-review action, or otherwise never strands the
   logical job.
5. Enforce ArchitecturePatch authorization for all strategic-thesis/destructive
   changes and reject stale closures or superseded targets.
6. Make same-model fallback explicitly `policy_satisfied=false` under the
   harness rule, then rerun X1--X16, full local regression, and hosted CI after
   an authorized push.

Until these are closed and independently rerun, the exact Phase 7 starting
baseline is intentionally not authorized.
