# RouteFailure Migration Report

## Result

`ROUTE_FAILURE_RECORD = PASS`

Native failed-route memory now belongs to immutable `RouteFailureRecord`
objects under the Research Plane. Every record binds an exact map version,
obligation semantic revision, root ClaimSnapshot, dependency snapshot,
assumption snapshot, authority context, failure condition/domain, evidence, and
typed reopen conditions.

## Deterministic eligibility

Eligibility returns one of:

```text
FAILURE_STILL_APPLIES
REOPENABLE
REVALIDATE
```

An explicitly declared dependency, assumption, authority, or verified-lemma
change can make a route `REOPENABLE`. An undeclared context change yields
`REVALIDATE`; unchanged exact context retains the historical failure. Old
records are never erased when eligibility changes.

## StrategyFingerprint compatibility

`StrategyFingerprintStore` remains readable and callable for legacy clients,
but is documented as a deprecated execution heuristic. The compatibility
adapter creates a `LEGACY_DERIVED` RouteFailureRecord with a source reference
to `strategy_fingerprints.json`; it does not pretend the legacy record had
native dependency/assumption/authority precision. Existing files are not
deleted.

New production audit failures use a typed FailureMap adapter to create native
RouteFailureRecords. FailureMap prose cannot directly mutate a ResearchMap.

## ModelRouter ownership

The production CandidateEngine no longer sends worker failure, disagreement,
stall, or candidate-existence signals into Router strategy state. Merely
resolving a provider/model route creates no per-obligation state. Router still
owns provider/model/reasoning/budget/fallback and explicit compute escalation.
Its old mutation methods remain deprecated compatibility methods for schema-2
checkpoints and direct legacy callers; they are not the production research
memory path.

## Remaining legacy ownership

- Repair context may read old frozen fingerprints as historical hints.
- Direct legacy callers and old routing checkpoints can still exercise the
  deprecated compute-escalation methods.
- Legacy `failed_routes.json` and steering fields remain readable context and
  are not rewritten or deleted.

R10, R11, R15, and R18 cover isolation, reopen, compatibility provenance, and
reverse-reference invalidation.
