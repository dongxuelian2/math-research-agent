# Final Independent Pre-Root Reauthorization Report

## Decision

```text
FINAL_REAUTHORIZATION = DENIED
PRE_ROOT_SYNTHESIS_CERTIFIED = NO
PHASE_7_AUTHORIZED = NO
PHASE_7_STARTING_HEAD = NOT AUTHORIZED
```

The repaired candidate is not authorized to cross the pre-root gate. F-002
and F-005 close under the independent probes, but F-007 remains open because
two production entry-point variants accept authority that is incomplete for
the current research domain. The hosted-CI and POSIX gates are also incomplete,
and the CI-equivalent Ruff formatting check fails on 11 files.

This is an audit-only result. No production source was changed during this
reaudit, no Phase 7 action was performed, and nothing was pushed.

## Repository identity

```text
AUDIT_DATE = 2026-08-20
BRANCH = codex/v3-2-reconciliation
AUDITED_PRODUCTION_HEAD = 3e2b6f481651d5a6b3b776c5d728cea6b93e2869
REPAIR_BASE = 11826b7c43957c07bd9dc34f01da610bbd431e1b
PRODUCTION_CHANGES_DURING_AUDIT = NO
WORKTREE_STATUS_AT_PRODUCTION_AUDIT = CLEAN
PUSHED = NO
```

`AUDITED_PRODUCTION_HEAD` is the exact clean candidate whose production code
was tested. The evidence-only files in this directory are a descendant record
of that code state and do not change the production disposition.

## Finding disposition

| Finding | Final disposition | Independent basis |
|---|---|---|
| F-001 | `CLOSED` | Frozen stale-closure, compound-stale, and no-scope replay evidence remains green. |
| F-002 | `CLOSED` | `F002-TERMINAL-REJECTION` shows late results are fenced, bodyless at routed consumption, non-selectable after restart, and unable to create an effect. |
| F-003 | `CLOSED` | Existing production EffectSlot and semantic-finalization regressions remain green. |
| F-004 | `CLOSED` | Existing `AFTER_PROVIDER_RESULT` recovery evidence remains green. |
| F-005 | `CLOSED` | `F005-A1-A14` rejects raw, cloned, serialized, nested, cross-map, casing, thesis, and invalid-scope authority attacks without map mutation; legitimate governed paths remain green. |
| F-006 | `CLOSED` | Same-model critic independence and authorized different-model path remain green. |
| F-007 | `OPEN` | `NF-003` accepts a root-only binding when map/directive/session context is current; `NF-004` bypasses the binding guard on a no-backend semantic router. |
| NF-003 | `OPEN` | Partial binding is treated as a wildcard by the current-domain validator. |
| NF-004 | `OPEN` | `require_execution_binding=True` is checked only inside the runtime-backend branch. |

The positive F-007 restart control is real but incomplete: missing validator
blocks, a stale validator fences, and a complete valid binding accepts. That
does not close the partial-binding and standalone variants required by the
pre-root contract.

## Blocker 1: partial current-domain binding

### Exact reproducer

```text
uv run --project openprover --extra test python docs/v3_2_migration/pre_root_final_reauthorization/run_final_adversarial_probes.py
```

Probe: `NF-003-PARTIAL-BINDING`.

The probe initializes a normal `ResearchOrchestrator`, obtains its current
binding containing root snapshot, research-map, directive, and tactical-session
identity, then replaces the router binding with a root-only
`CrossPlaneExecutionBinding`. It calls the normal `RoutedLLMClient` path with a
mock provider returning a semantic success payload.

### Invariant and actual result

```text
EXPECTED = root-only binding is rejected when current domain context is richer
           than the supplied binding; no semantic payload is consumable
ACTUAL   = validator_value=True; runtime accepted=True; semantic payload returned;
           accepted_result_id was populated
```

The relevant implementation is
`openprover/openprover/math_research/orchestrator.py:876-903`. The validator
checks map and governance fields only when the supplied field is non-null, so
omitted fields become wildcards. The normal route then proceeds through
`openprover/openprover/math_research/routing.py:933-1007`.

### Minimal repair direction

When the current domain contains a map, obligation, directive, session, or
governance identity, missing corresponding fields must fail closed rather than
act as wildcards. If a root-only binding is intentionally needed for a narrow
root-only operation, that operation must use an explicit adapter and must not
be accepted by the normal semantic research router, restart reconciler, result
acceptance, or effect-preparation path.

## Blocker 2: no-backend binding guard bypass

### Exact reproducer

The same command above, probe `NF-004-NO-BACKEND-GUARD`, constructs
`ModelRouter(require_execution_binding=True)` without a runtime backend and
calls `RoutedLLMClient` with a semantic mock provider.

### Invariant and actual result

```text
EXPECTED = require_execution_binding=True rejects before provider semantic
           output can be returned, even when the backend is absent
ACTUAL   = semantic response returned with structured authorized/high_value
           content and no runtime binding
```

The guard at `openprover/openprover/math_research/routing.py:944-952` is
conditional on `self.router.runtime_backend is not None`. The no-backend path
therefore reaches the provider directly. This is distinct from the intentional
transport-only provider-smoke exception: the reproducer uses the semantic
`RoutedLLMClient` and explicitly requests binding enforcement.

### Minimal repair direction

Evaluate `require_execution_binding` before branching on backend presence and
reject when the binding or validator is unavailable. Any transport diagnostic
mode should be an explicit non-semantic API that cannot return a semantic
success payload through the normal routed client.

## Gate summary

| Gate | Result | Evidence |
|---|---|---|
| Full local suite | `PASS` | `283 passed, 1 warning` |
| Focused repair slice | `PASS` | `104 passed, 1 warning` |
| Targeted regression slices | `PASS` | `2`, `26`, and `24` tests passed in the recorded slices |
| F-005 adversarial matrix | `PASS` | `F005-A1-A14` |
| F-007 complete-binding restart controls | `PASS` | `F007-RESTART-CONTROLS` |
| F-002 terminal rejection | `PASS` | `F002-TERMINAL-REJECTION` |
| Ruff lint | `PASS` | `All checks passed!` |
| Ruff format check | `FAIL` | `11 files would be reformatted, 131 already formatted` |
| Compileall | `PASS` | Production and audit probe sources compile |
| Lockfile check | `PASS` | `uv lock --check --project openprover` |
| Shell syntax checks | `PASS` | 7 Bash files and 2 PowerShell files, excluding inaccessible pytest cache paths |
| Windows interrupt race | `PASS` | Actual subprocess-tree and multiworker checks passed |
| Hosted CI | `PENDING` | No push or workflow dispatch was authorized |
| POSIX interrupt host run | `REQUIRES_HOSTED_CI` | Windows cannot certify POSIX process behavior |

The formatting failure is a gate failure, not a reason to mutate production
code during this audit. The repair-agent formatting state is preserved for
owner-directed follow-up.

## Required disposition

The repository must remain pre-root and Phase 7 must remain unauthorized until
the owner receives a new evidence package showing:

1. `NF-003` and `NF-004` rejected by production tests at the exact public
   entry points above.
2. X1 and X7 re-run and certified across complete, partial, restart, and
   standalone variants.
3. Ruff formatting passes, hosted Linux CI is green, and the POSIX interrupt
   check is host-executed.
4. The final probes are re-run against the exact final production HEAD and a
   separate explicit Phase 7 authorization is granted.
