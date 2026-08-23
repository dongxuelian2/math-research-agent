# v3.2 Pre-Root Independent Re-Audit Plan

Audit date: 2026-08-20.  Repository under audit:
`C:\Users\29848\Documents\ChatGPT\math\math-research-agent-integration`.

## Scope and authority

This is a targeted closure audit of F-001 through F-007.  The frozen
`pre_root_audit/` package and the repair-author `pre_root_repair/` package were
read but not edited.  Repair reports are treated as claims; closure is based
on production code, caller tracing, deterministic probes, SQLite state, and
the local regression suite.

No Phase 7 implementation, architecture redesign, broad refactor, hosted
push, or LLM-heavy campaign was performed.  The only added executable
instrumentation is `run_pre_root_reaudit_probes.py` in this directory.

## Baseline

| Item | Observed value |
|---|---|
| Branch | `codex/v3-2-reconciliation` |
| Failed-audit ancestor | `f48269c8a929a67b90eff56af4a200f2ed757c61` |
| Audited starting HEAD | `d6e45778fd9f64d290712408cf15b78aa8c70d1c` |
| Repair commits after failed ancestor | `8` |
| Starting working tree | clean |
| Starting relation to `origin/main` | ahead by 42; not pushed |

The workspace parent was a separate dirty repository.  It was left untouched;
the nested repository above is the one matching the frozen branch and HEAD
requirements.

## Evidence sequence

1. Read `FINDINGS.md`, `PRE_ROOT_SYNTHESIS_CERTIFICATION.md`, and
   `CROSS_PLANE_TEST_MATRIX.md`.
2. Read the complete repair matrix, regression evidence, seven repair reports,
   repair JSON, and repair runner.
3. Ran the frozen runner unchanged and the repair runner without `--output`.
4. Traced orchestrator ownership through `RoutedLLMClient`, durable runtime,
   `RuntimeReconciler`, `RuntimeEffectCoordinator`, and domain stores.
5. Ran deterministic audit-only probes for the compound stale case, restart
   recovery, forged thesis authorization, late payload handling, and a
   three-obligation no-scope stale replay.
6. Ran focused and full local-safe tests plus tooling and script syntax checks.

The frozen executable runner contains executable probes for X1, X2, X4,
unknown-execution recovery, and the two governance checks; it does not contain
sixteen literal functions.  Therefore the exact X1-X16 bookkeeping below
separates the repair runner's mapped X coverage from the additional independent
attacks and does not claim that the frozen file executed absent X functions.

## Closure rule

An F-001..F-007 item is `CLOSED` only when the original production ownership
path is fenced, a negative case is observed, a positive case remains valid,
and the relevant cross-plane/recovery path is covered.  Any open P0/P1 or a
root-relevant stale-authority gap keeps both certification gates denied.
