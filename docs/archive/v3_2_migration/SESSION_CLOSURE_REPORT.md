# Session Closure Report

## Result

`SESSION_CLOSURE = PASS`

## Production chain

```text
ClaimSnapshot
→ ResearchMap / ResearchObligation
→ immutable Directive
→ TacticalSession execution binding
→ Planner / Workers / Worker Verifier / Candidate / Audits
→ immutable SessionClosure
→ validated EvidenceProjection
→ deterministic obligation-resolution decision
→ new immutable ResearchMap version
```

The Planner receives `Directive.tactical_context()`, not the mutable full map.
Worker context can only narrow the Directive's evidence and scope. Planner or
Worker output has no map-write capability.

## Raw retention

Session closure copies raw candidate, worker/verifier outputs, typed events,
audits, runtime events/state, usage, and available provider provenance into the
session's immutable raw-artifact directory. Each reference records original
path, retained path, producer, kind, and byte SHA-256. Evidence projection is a
separate typed list; retaining prose never makes it trusted evidence.

## Resolution gate

The deterministic gate returns a typed status, not a boolean:

```text
RESOLUTION_ACCEPTED
INSUFFICIENT_EVIDENCE
STALE_EVIDENCE
AUTHORITY_BLOCKED
AUDIT_FAILED
SCOPE_MISMATCH
```

Acceptance requires an exact obligation/root binding, retained candidate,
Verifier PASS, Audit PASS, and trusted authority. Worker prose and a candidate
alone return `INSUFFICIENT_EVIDENCE`. Accepted resolution changes only the
Research Plane. The production R19 E2E then performs a separate PHASE 3
TruthMutation intent/compare/receipt before theorem promotion.

## Invalidation

A deterministic reverse-reference projection maps evidence ids/hashes,
artifact hashes, ClaimSnapshots, authority refs, dependency snapshots,
assumption snapshots, and authority contexts to affected obligations, maps,
and route failures. It is a minimal filesystem index, not a graph database or
authority source.

## Evidence

- R6, R7, R17 in `test_session_closure.py`.
- R13–R14 in `test_directive_projection.py`.
- R18–R20 in route/e2e suites.
- Real production mock path reaches three Workers, Worker Verifiers, Candidate,
  specialist/final audits, `RESOLUTION_ACCEPTED`, ResearchMap v2, then separate
  TruthMutation.
