# Open Findings and Frozen Audit Facts

This file is a handoff projection. It does not replace or edit the historical
audit records.

## NF-003 — Partial current-domain binding

**Candidate status: REPAIRED_PENDING_INDEPENDENT_CERTIFICATION.**

`ResearchOrchestrator._validate_execution_binding()` now fails closed when the
current domain has a ResearchMap or obligation/directive/session dimension and
the supplied binding omits it. The normal `RoutedLLMClient` path validates this
before call creation/provider invocation. Map-scoped governance effects use an
explicit complete-map adapter, while truth mutation keeps its explicit
root-only adapter; neither is a wildcard in the normal semantic route.

Evidence: `NF-003-PARTIAL-BINDING` PASS, the new production regression in
`test_pre_root_authority_repairs.py`, the full local suite, and the X1/X7 repair
runner.

## NF-004 — No-backend semantic route bypass

**Candidate status: REPAIRED_PENDING_INDEPENDENT_CERTIFICATION.**

`RoutedLLMClient._execute_route()` now evaluates `require_execution_binding`
before any runtime-backend branch, requires both a binding and validator, and
rejects a validator result other than `True` before the provider is acquired.
Transport-only diagnostics remain outside the normal semantic route.

Evidence: `NF-004-NO-BACKEND-GUARD` PASS and the new provider-not-called
regression in `test_pre_root_authority_repairs.py`.

## F-007 — Complete binding / restart control

**Candidate status: REPAIR_CANDIDATE_CLOSED.**

Complete, partial, stale/restart, and no-backend semantic variants now pass the
candidate probes and local regressions. This is a local repair disposition, not
an independent final audit or formal certification.

## Historical closed findings

```text
F-002 = CLOSED and revalidated locally
F-005 = CLOSED and revalidated locally
```

Terminal rejection and forged-authority defenses are reused as existing
production mechanisms. Their historical dispositions remain unchanged.

## Implementation observation

`IMPLEMENTATION_OBSERVATION-001` is resolved, not an open formal finding: the
research-root and later audited ClaimSnapshot are now stored as separate,
explicit Phase 7 identities. A later engineer may inspect the corresponding
tests and artifacts, but must not relabel this implementation detail as an
audit closure.

## Certification freeze

```text
PRE_ROOT_SYNTHESIS_CERTIFIED = NO
PHASE_7_FORMALLY_AUTHORIZED = NO
FINAL_SYSTEM_CERTIFIED = NO
```
