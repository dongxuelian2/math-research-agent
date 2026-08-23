# PHASE 3 Truth Identity Report

## Result

`ASSERTION_IDENTITY = PASS`

PHASE 3 introduces typed, schema-versioned, domain-separated identities in
`truth_identity.py`. Unknown schemas and unknown durable fields fail closed.

## AssertionIdentity semantics

`AssertionIdentity` answers only what is mathematically asserted. Its hash is
computed from conservative canonical forms of:

- `canonical_statement`;
- `claim_type`;
- `notation_scope`.

`assertion_kind` and `stable_id` remain typed provenance/classification fields,
but do not make two differently named copies of the same assertion
mathematically different. Source path/bytes, provider, model, run, timestamp,
theorem status, audit status, and authority are excluded.

Canonicalization is deliberately limited to Unicode NFC, newline normalization,
trailing whitespace removal, outer blank-line removal, and deterministic JSON.
It does not guess mathematical equivalence or alpha-equivalence.

## Hash domains

The following new producers are explicit and non-interchangeable:

| Domain | Meaning |
|---|---|
| `assertion_identity_hash` | canonical mathematical assertion |
| `source_artifact_sha256` | exact artifact bytes |
| `dependency_snapshot_hash` | sorted dependency truth closure |
| `assumption_snapshot_hash` | current premises/local/semantic assumptions |
| `authority_binding_hash` | exact typed authority-binding set |
| `trust_policy_fingerprint` | active trust inputs and replay policy |
| `semantic_input_hash` | combined truth inputs |
| `prompt_projection_hash` | rendered context/prompt projection only |
| `claim_snapshot_hash` | immutable root truth snapshot |

All semantic hashes use a typed domain envelope. The source-artifact function
remains an exact byte SHA-256 and is never treated as assertion identity.

## AuthorityBinding

Bindings retain authority kind, id, assertion hash, authority content hash,
status, and real provenance. Project theorems, premises, foundation registries,
semantic registries, and canonical sources are distinct kinds. Canonical
bindings use the resolver's computed body hash and authority record; filenames
and resolved cache locations remain locators rather than truth identity.

## Provider neutrality

No ClaimSnapshot identity field contains a provider or model. Switching among
Gemini, Vertex, Codex, OpenAI, or Mock cannot change assertion identity.
Provider execution provenance remains in its existing execution artifacts.

## Regression mapping

- T1: same assertion/different bytes keeps assertion hash and changes artifact hash.
- T2: same filename/changed statement changes assertion identity.
- T3: dependency mutation changes the dependency snapshot.
- T4: assumption mutation changes the assumption snapshot.
- T5: authority mutation changes/rejects the authority binding.
- T6: trust-policy mutation requires revalidation.
- T7: prompt projection changes without changing assertion or ClaimSnapshot identity.

