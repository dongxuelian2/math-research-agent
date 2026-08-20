# Open Findings and Frozen Audit Facts

This file is a handoff projection. It does not replace or edit the historical
audit records.

## NF-003 — Partial current-domain binding

**Status: OPEN.**

The normal semantic route can accept an execution binding containing only root
identity while the current domain contains richer authority dimensions. Missing
required dimensions can therefore behave as wildcards instead of failing
closed. The Phase 7 implementation does not tighten this validator or change
its semantics.

## NF-004 — No-backend semantic route bypass

**Status: OPEN.**

With `require_execution_binding=True` and no runtime backend, the normal
semantic `RoutedLLMClient` route can bypass execution-binding enforcement and
return semantic provider output. The Phase 7 implementation does not move or
redesign this guard.

## F-007 — Complete binding / restart control

**Status: OPEN.**

F-007 remains open because NF-003 and NF-004 remain open. The Phase 7 resume
path adds only the persistence and recovery needed for Phase 7 artifacts; it is
not a certification of the broader F-007 contract.

## Historical closed findings

```text
F-002 = CLOSED
F-005 = CLOSED
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
