# Canonical Artifact Authority P0 Report

## Outcome

Canonical proof/replay dependencies now have a production fail-closed boundary:
the runtime supplies the actual locally resolved body with SHA-256-bound
provenance, or blocks only the dependent obligation with a typed authority
reason. A manifest, filename, stored hash, summary, theorem extract, or model
reconstruction is not a resolved body.

## Production path

1. `ReplayPolicy.from_manifest` parses explicit `canonical_source_requirements`
   and configured local roots. Purpose must be declared as `proof_authority`,
   `replay_authority`, or `contextual_only`; filenames do not imply purpose.
2. `CanonicalArtifactResolver` searches project `sources/`, `inbox/`, declared
   roots, approved run materializations, and an immutable run-local
   content-addressed cache. It does not use public web or model memory.
3. The resolver reads actual bytes and emits `RESOLVED_CANONICAL`,
   `MISSING_CANONICAL`, `HASH_MISMATCH`, `AMBIGUOUS_CANONICAL`, or
   `NONCANONICAL_ONLY` with body/hash/path/authority/obligation provenance.
4. `ResearchOrchestrator` persists resolution records in run state and
   `canonical_authority/resolution.json`, places body plus provenance in the
   Planner/Worker/upstream-Verifier/Auditor context, and binds the same record
   into proof and verification task payloads.
5. `AsyncDAGScheduler` applies scoped `BLOCKED_AUTHORITY_*` states. Independent
   obligations remain dispatchable. Its close path and the final Archivist
   promotion path both fail closed if declared authority provenance is absent.
6. New and resumed runs re-resolve current bytes or reuse a verified immutable
   content-addressed body. A checkpoint's prior computed digest pins
   revalidation even when no manifest expected digest was supplied.

## Blocking semantics

| Resolution | Dependent proof/replay obligation |
|---|---|
| `MISSING_CANONICAL` | `BLOCKED_AUTHORITY_SOURCE_UNAVAILABLE` |
| `HASH_MISMATCH` | `BLOCKED_AUTHORITY_HASH_MISMATCH` |
| `AMBIGUOUS_CANONICAL` | `BLOCKED_AUTHORITY_AMBIGUOUS` |
| `NONCANONICAL_ONLY` | `BLOCKED_AUTHORITY_NONCANONICAL_ONLY` |

These are authority/infrastructure conditions, not mathematical failure and
not theorem rejection. The mathematical `AuditGate` was not changed.

## Regression evidence

`test_canonical_artifact_authority.py` covers A–H plus two production-path
cases: correct body/hash and downstream payload parity; extract-only blocking;
hash mismatch; deterministic hash disambiguation and ambiguity; manifest-only
absence; independent branch continuation; checkpoint immutable reuse and
mutation detection; and the original formula-confusion class where
`w = p - J*l*5^s` must win over the non-authoritative `w = p - J*l*C` extract.

- Canonical authority suite: `10 passed`.
- Existing replay, async pipeline, and retrieval suites: `23 passed`.

## Deliberate boundary

This P0 adds no TruthStoreFacade, ClaimSnapshot, ResearchMap,
ResearchObligation ontology, Architecture Review subsystem, or SQLite/WAL
runtime. The existing JSON/file runtime remains an explicitly documented
migration boundary.
