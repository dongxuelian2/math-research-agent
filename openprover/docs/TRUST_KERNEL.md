# OpenProver Trust Kernel

The research harness admits proof claims through three explicit authority layers. The layers are deliberately separate: a project theorem cannot be smuggled in as “standard mathematics,” and a project definition cannot be inferred from a filename or package label.

## Layer A — Foundations

The built-in registry is `openprover/math_research/registries/foundations.v1.json`. It is small, project-independent, versioned, and content-hashed. Each item has a stable ID, exact statement, conditions, provenance, proof policy, version, and item hash. The registry itself also has a hash.

The initial set is intentionally limited to the Jacobi-symbol and quadratic-reciprocity facts needed by the GA1-1 certification benchmark:

- `FOUND-NT-JAC-01`: numerator multiplicativity of the Jacobi symbol.
- `FOUND-NT-JAC-02`: the supplementary law for `(-1/n)`.
- `FOUND-NT-JAC-03`: the supplementary law for `(2/n)`.
- `FOUND-NT-QR-01`: quadratic reciprocity for distinct odd primes.
- `FOUND-NT-QR-02`: the reviewed corollary `(5/n)=(n/5)` for positive odd `n` coprime to 5.

Foundation validation rejects project-specific GA/G/A1 markers and replay/solution material. Adding a classical fact requires a new or reviewed registry version; there is no open-ended “standard mathematics allowed” authority.

## Layer B — Semantics

Semantic registries are project-scoped. The default location is `semantics/registry.json` within a project, or an explicit path in `project.json`. Every semantic item binds:

- a stable `SEM-*` ID;
- an exact statement and authority kind (`definition`, `iff`, or `implication`);
- a notation scope and notation version;
- a real source file, section, and SHA-256;
- an item version and content hash.

The GA1 certification bundle is `ga1_certification_semantics.v1.json`. Its `SEM-G-PRIM-01` item is not globally enabled. It is valid only in the scope `G-positive-remainder-content-decomposition/GP3-v1` and is bound to the GP3 source body, including the statement that the primitive unit/core is the `h=1` layer and the equivalence with the preferred primitive condition.

A notation-scope mismatch is a hard authority failure. Package metadata, index summaries, filenames, and generated manifest comments cannot be semantic authority.

## Layer C — Project theorem DAG

Project theorem authority continues to come from `theorems/<id>.json` and the recursive `dependencies` DAG. An external project theorem claim is admissible only when its exact authority ID resolves to a theorem whose lifecycle status is `PROVED`. `index.json` and `downstream_dependents` are derived navigation metadata.

## Local proofs and computational certificates

A lemma fully proved in the candidate is classified `LOCAL_PROOF` and records a candidate location. It needs no registry ID. The Dependency Auditor must still decide whether the proof is complete.

A computation is classified `COMPUTATIONAL_CERTIFICATE` and records a certificate ID. It remains evidence unless the proof supplies a valid finite reduction and reproducible certificate.

## Dependency reports

Dependency Auditor v2 inventories external uses with one of these claim classes:

- `FOUNDATIONAL_THEOREM`
- `SEMANTIC_DEFINITION`
- `PROJECT_THEOREM`
- `LOCAL_PROOF`
- `COMPUTATIONAL_CERTIFICATE`

The deterministic resolver validates authority IDs and emits separate lists for Foundations, Semantics, Project Theorems, Local Proofs, and Computational Certificates. It also records `foundation_ids_used`, missing authorities, and errors. Registry hashes and semantic source hashes are part of the run context.
