# PHASE 3 ClaimSnapshot Report

## Result

`CLAIM_SNAPSHOT_IMMUTABLE = PASS`

`ClaimSnapshot` is a frozen typed object persisted at
`truth/claim_snapshots/<claim_snapshot_hash>.json`. A changed truth state creates
a new content-addressed artifact. Existing content is never rewritten; a path
collision with different bytes is an integrity error.

## Captured truth

Each snapshot binds:

- theorem id and full `AssertionIdentity`;
- deterministic recursive `DependencySnapshot`;
- `AssumptionSnapshot` covering current premises, configured local assumptions,
  notation scope, and semantic-registry identity;
- typed project/premise/registry/canonical `AuthorityBinding` objects;
- replay/project/trust-policy fingerprint;
- combined semantic-input hash;
- captured theorem status and project-record evidence hash.

Premises remain `PREMISE` with active/provenance semantics. They are never
collapsed into `PROVED THEOREM` or a generic trusted boolean.

## Canonical authority integration

The existing P0 resolver remains authoritative. ClaimSnapshot consumes its
resolved computed body SHA-256, status, source classification, and authority
record. Missing, mismatched, ambiguous, or noncanonical authority reconstructs
as unresolved/changed truth and cannot pass promotion comparison. Operational
cache paths and checkpoint-copy locators are deliberately excluded from the
binding hash so an unchanged canonical body remains stable across resume.

## Comparison semantics

| Result | Disposition |
|---|---|
| `MATCH` | `COMPATIBLE` |
| `ASSERTION_CHANGED` | `HARD_STALE` |
| dependency/assumption/authority/trust/semantic/status change | `REVALIDATION_REQUIRED` |
| unresolved required authority | `BLOCKED` |
| unknown schema/current reconstruction | `REVALIDATION_REQUIRED` |

Unknown compatibility is never accepted implicitly.

## Production integration

- New run/campaign: capture and persist the root snapshot.
- Resume: load the stored snapshot and reconstruct/compare current truth.
- Legacy resume without sufficient truth snapshot: `REVALIDATION_REQUIRED`.
- Context/prompt: carries snapshot/assertion hashes plus a separate projection hash.
- Formalization: binds its result/certificate to snapshot and assertion hashes.
- Before audit: validates that the run snapshot still matches current truth.
- Specialist/final audits: persist `audited_claim_snapshot_hash`.
- Promotion: accepts only the exact snapshot named by the final gate.

T8 proves unchanged resume is accepted. T9 proves a changed root assertion is
checkpointed as `BLOCKED_CLAIM_SNAPSHOT_STALE` before candidate continuation.

## Deliberate coverage boundary

PHASE 3 captures assumptions already present in the production path. It does
not invent a new assumption ontology. Literature applicability continues to be
validated by its existing trust/audit path; a full ResultTrustKernel rewrite is
out of scope. The root-synthesis validator is only a future fail-closed seam.

