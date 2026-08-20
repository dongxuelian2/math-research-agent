# Legacy Checkpoint Migration Report

## Production boundary

`ResearchOrchestrator` inspects a resumed `state.json` before accepting it as a
current run. Schema 2 continues through the existing path. Every other readable
schema enters `LegacyCheckpointMigrator`; malformed JSON is retained as
archive-only evidence.

The source is never rewritten. Migration creates:

- an immutable byte-for-byte source snapshot;
- immutable migration provenance;
- for resumable classifications, an immutable migrated-state artifact and a
  separate mutable schema-2 runtime copy;
- content-addressed canonical bodies only when their actual embedded bytes
  match the recorded SHA-256.

Migration records are keyed by source-byte hash and a target/campaign/policy
context hash, so repeated migration is idempotent and different target contexts
cannot collide.

## Classification

| Classification | Rule | Production effect |
|---|---|---|
| `DIRECT_IMPORT` | All assertion, artifact, dependency, authority, verifier, trust-policy, and runtime checks are explicitly true; declared artifact hashes are recomputed | Creates a current-schema run artifact; does not promote theorem truth |
| `REVALIDATION_REQUIRED` | Any compatibility is old, false-but-recoverable, or unknown; this is the default for unknown schemas | Restarts from `CREATED` with `LEGACY_VERIFIED` or `LEGACY_EVIDENCE` metadata |
| `ARCHIVE_ONLY` | Runtime ontology, mathematical bodies, assertion identity, trust provenance, or semantic usability is unrecoverable | Retains source, metadata, original schema, and report; cannot drive production |
| `INCOMPATIBLE` | A known target/campaign/declared semantic conflict exists | Retains evidence and refuses resume |

`INCOMPATIBLE` is not used merely because a schema is unknown.

## Provenance

Every result records the source artifact path, source byte hash, source schema,
target schema, known source policy fingerprint, target policy fingerprint,
classification, reason, UTC migration timestamp, implementation version,
immutable source snapshot, and migrated checkpoint path when applicable.

Execution-provider provenance and evidence/verifier-provider provenance are
preserved separately. An adapter or model identity change does not erase the
mathematical artifact, but legacy verifier independence is not silently carried
forward.

## Canonical authority interaction

Legacy proof/replay authority that contains only a filename, hash, or summary
forces `REVALIDATION_REQUIRED`. The migration adapter emits a typed canonical
source requirement and the current resolver must recover actual bytes. If it
cannot, the existing obligation-scoped authority blocker prevents the dependent
branch from producing a candidate; a hash-only record never restores authority.

## Trust boundary

Legacy `PROVED` or trusted-evidence labels become non-current
`LEGACY_VERIFIED`; other imported material becomes `LEGACY_EVIDENCE`. Migration
does not mutate the theorem registry or create `TrustedEvidence`, `PROVED`, a
ClaimSnapshot, TruthStoreFacade, ResearchMap, ResearchObligation, Architecture
Review, or SQLite control state.

## Regression evidence

- Checkpoint classification, immutability, provider separation, unknown-schema,
  hash-only authority, and production canonical-resume suite:
  `8 passed in 1.73s`.
- Combined checkpoint + canonical authority + heterogeneous routing slice:
  `23 passed in 2.41s` before the additional archive-body case was added; all
  eight checkpoint cases subsequently passed.
- The final repository-wide local-safe suite is recorded in
  `REGRESSION_EVIDENCE.md`: `178 passed in 6.62s`.
