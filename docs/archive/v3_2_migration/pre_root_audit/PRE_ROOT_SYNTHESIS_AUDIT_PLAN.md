# Harness v3.2 Pre-Root-Synthesis Audit Plan

## Scope and authority

This is an independent external audit of frozen Phase 3--6 code on branch
`codex/v3-2-reconciliation`. It covers Truth, Research, Architecture
Governance, and Durable Runtime ownership plus their cross-plane crash and
stale-state boundaries.

Explicit non-goals:

- no Phase 7 implementation or ROOT_SYNTHESIS work;
- no architecture redesign or broad refactor;
- no production-code repair unless a tiny repair is required to observe an
  invariant (none was required);
- no push, PR, hosted workflow, or live-provider workflow.

The supplied harness specification is the acceptance authority. Existing
reports are treated as claims to verify, not as proof.

## Frozen baseline

| Item | Observation |
|---|---|
| Repository audited | `math-research-agent-integration` nested checkout |
| Branch | `codex/v3-2-reconciliation` |
| Starting HEAD | `6ada9282f31ac5cdafb2353dc9c19e2c4fe8aa76` |
| Baseline range | `bdab5cff50227a1504208359539bfe7dba5e7bc2..6ada9282f31ac5cdafb2353dc9c19e2c4fe8aa76` |
| Corrected Phase 6 commit count | `12` (`git rev-list --count`) |
| Starting worktree | clean |
| Push | not performed |

The current checkout contains no matching “13 local commits” text; the audit
record therefore carries the authoritative count and the exact 12-SHA log
from Git rather than editing an absent historical phrase.

## Evidence order

1. Verify Git identity, cleanliness, and history.
2. Read the Phase 3--6 reports and implementation matrix as hypotheses.
3. Trace production entry points and write ownership/bypass results.
4. Inspect SQLite current state, journal, outbox, leases, artifacts, and
   EffectSlot recovery code.
5. Run deterministic cross-plane probes in
   `run_cross_plane_probes.py` against temporary projects.
6. Run the local regression suite and the interrupt-race script.
7. Archive findings, severities, and the minimum repair frontier. Do not fix
   the findings in this audit.

## Capability verdict vocabulary

`CERTIFIED`, `CERTIFIED_WITH_LIMITATION`, `PARTIAL`, `FAILED`,
`UNVERIFIED`, `REGRESSION`, and `ARCHITECTURE_DEVIATION_CONFIRMED` are used
exactly as the harness requires. A passing unit test does not override a
contradictory production-path observation.

## Commands executed

```text
git status --short --branch
git branch --show-current
git rev-parse HEAD
git rev-list --count bdab5cff50227a1504208359539bfe7dba5e7bc2..6ada9282f31ac5cdafb2353dc9c19e2c4fe8aa76
git log --oneline bdab5cf..6ada928
uv run --project openprover python docs/v3_2_migration/pre_root_audit/run_cross_plane_probes.py
uv run --project openprover pytest -q
uv run --project openprover pytest -q <focused Truth/Research/Governance/Runtime/migration slice>
uv run --project openprover python openprover/tests/test_interrupt_race.py
```

## Stop rule

The observed P0/P1 findings in `FINDINGS.md` are sufficient to deny the
pre-root certification. Phase 7 remains unauthorized until the minimum repair
frontier is implemented and the cross-plane probes are rerun.
