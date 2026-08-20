# PHASE 3 Production Truth-Path Audit

## Audit boundary

- Audited branch: `codex/v3-2-reconciliation`.
- Audited HEAD: `bea1bf565acd94379218d80b5cfa07d18e2b21ea`.
- Working tree at audit start: clean.
- Governing specification: `Harness_v3_2_合并架构与冻结规范.md`.
- This document was written before any PHASE 3 production-module change.
- Out of scope: ResearchMap, ResearchObligation, Architecture Review,
  Structural Probe, SQLite/WAL, outbox, leases, AttemptIntent runtime,
  provider redesign, and tactical-kernel rewrites.

## Current owners and identities

| Concern | Current production owner | Current identity | Finding |
|---|---|---|---|
| Theorem record and status | `ProjectStore` + `state_machine.validate_transition` | theorem `id`; mutable JSON record | Strict status transitions exist, but identity-critical fields can be overwritten through `update_theorem` |
| Premise | `ProjectStore` premise JSON | premise `id`; provenance file existence | Correctly distinct from a proved theorem, but no typed assertion identity |
| Root statement | theorem `statement`, `claim_type`, `notation_scope` | raw mutable fields | No assertion-domain hash exists |
| Dependency truth | current `dependencies` plus `resolve_dependency` / `validate_proved_dependency` | live records at read time | No immutable deterministic dependency snapshot |
| Foundation/semantic authority | `TrustKernel` registries | registry and item content hashes | Strong source hashes exist, but are not bound into a root ClaimSnapshot |
| Project/external authority | `DependencyAuthorityResolver` | per-audit authority report | Validated during audit, but not revalidated as a snapshot-bound promotion fence |
| Canonical proof/replay authority | `CanonicalArtifactResolver` | body bytes + expected/computed SHA-256 + provenance | Correct P0 authority; revalidated at resume/promotion, but not yet part of a combined ClaimSnapshot |
| Literature authority/applicability | literature/trust records and audit path | artifact/receipt-specific hashes | Fail-closed primitives exist; no root snapshot binding |
| Candidate audit | `AuditCoordinator` + `AuditGate` | audit JSON and gate | Audit input is context/candidate text, not an explicit claim snapshot hash |
| Promotion | `ResearchOrchestrator._finalize` | live theorem id + passed gate | No compare-and-transition against the audited truth state |
| Campaign/checkpoint | `CampaignStore` and orchestrator run `state.json` | campaign/run ids and schema versions | Execution identity is durable; mathematical claim identity is not |

## Existing hash domains

The repository already has useful but separate hashes:

- theorem/registry `content_hash` in `trust_kernel.py`;
- registry hashes and semantic-source byte hashes;
- canonical expected/computed/checkpoint SHA-256;
- replay policy hash;
- checkpoint source and target-policy fingerprints;
- scheduler strategy and scholarly retrieval hashes;
- prompt/context-related artifact hashes in individual subsystems.

These are not interchangeable. There is currently no typed producer for:

```text
assertion_identity_hash
dependency_snapshot_hash
assumption_snapshot_hash
authority_binding_hash
semantic_input_hash
prompt_projection_hash
claim_snapshot_hash
```

The PHASE 3 implementation must introduce explicit domain tags and canonical
field serialization without renaming or weakening existing legacy hashes.

## Old production truth path

```text
ProjectStore.load_theorem(target)
        ↓
ResearchOrchestrator initializes run state
        ↓
ContextBuilder reloads target + dependency records + TrustKernel summary
        ↓
CanonicalArtifactResolver resolves/revalidates declared canonical bodies
        ↓
Planner → Workers → Worker Verifier → candidate
        ↓
AuditCoordinator sends context + candidate to specialist/final auditors
        ↓
DependencyAuthorityResolver emits dependency_report
        ↓
AuditGate PASS
        ↓
ResearchOrchestrator._finalize refreshes only canonical authority
        ↓
write resolution report
        ↓
ProjectStore.update_theorem(proof metadata)
        ↓
ProjectStore.transition(PROVED, gate=gate)
```

The state machine correctly requires `Archivist` plus a passing gate for
`AUDITING → PROVED`, but it does not know which exact statement/dependencies/
assumptions/authorities the gate audited.

## Stale-state guards that exist today

### Start and resume

- Orchestrator construction reloads the theorem by `target_id`.
- Resume requires the stored target and campaign ids to match.
- Current run schema is checked; legacy checkpoints enter conservative
  migration.
- Canonical requirements and prior resolutions persist and actual canonical
  bytes are revalidated.
- Campaign run ids, phases, routing state, and pipeline state are durable.

### Audit and promotion

- Current theorem status transitions are checked by the state machine.
- The audit dependency report checks current project/foundation/semantic/local
  authority at audit time.
- Promotion refreshes canonical proof/replay authority and fails closed on
  missing, mismatched, ambiguous, or noncanonical bodies.

### Missing guards

- No stored root assertion identity is compared at resume.
- No immutable dependency or assumption snapshot is compared at audit or
  promotion.
- No active trust-policy fingerprint is compared with the audited policy.
- Final-audit artifacts do not declare `audited_claim_snapshot_hash`.
- The theorem can change between audit PASS and promotion without the gate
  noticing.
- Proof metadata is written through generic `ProjectStore.update_theorem`
  immediately before transition.
- No durable mutation intent exists before a truth write, and no receipt proves
  the exact before/after records after it.

## Direct answers required by PHASE 3

### What is theorem identity now?

Operationally it is the mutable theorem `id` plus whatever fields happen to be
loaded from `theorems/<id>.json`. There is no immutable mathematical assertion
identity. A stable id does not prove that its statement is unchanged.

### Who owns status now?

`ProjectStore.transition`, guarded by `state_machine.validate_transition`, owns
the theorem lifecycle. `ResearchOrchestrator` is the main production caller.

### How is statement hash generated now?

It is not. Existing content/source hashes belong to other domains and cannot be
reused as assertion identity.

### Does a dependency snapshot exist?

No. Context building and audits read a live dependency slice. The canonical
authority subsystem persists its own exact requirements/resolutions, but there
is no root-wide dependency snapshot.

### Does an authority snapshot exist?

Partially. Canonical authority has body-bound persisted resolutions, and audit
creates a dependency authority report. Neither is yet unified into immutable
typed AuthorityBindings owned by a ClaimSnapshot.

### What stale guard exists between Final Audit and Promotion?

Only canonical-body revalidation and ordinary status-transition validation.
Root assertion, dependency, assumption, and trust-policy races are unguarded.

### Does resume prove that the root target is unchanged?

No. It proves the target id/campaign/schema match and revalidates canonical
bytes. The same theorem id may now contain a different statement.

## Direct truth reads and writes

### Production writes requiring PHASE 3 routing

- `ResearchOrchestrator._finalize` calls `ProjectStore.update_theorem` for proof
  metadata and then `ProjectStore.transition(PROVED)`.
- Orchestrator start/audit failure/re-audit paths call
  `ProjectStore.transition` for `IN_RESEARCH`, `CANDIDATE_PROOF`, `AUDITING`,
  `PARTIAL`, and `REJECTED`.
- Human/import setup uses `add_theorem`; this is identity creation, not
  promotion, and remains a compatibility entry point.
- `ProjectStore.update_theorem` remains a public legacy escape hatch and can
  change identity-critical fields. New truth-sensitive PHASE 3 code must not
  use it.

### Production reads to migrate first

- orchestrator root capture/resume/finalization;
- campaign creation and resume validation;
- audit root/dependency/authority capture;
- formalization root binding;
- dependency authority resolution when used for promotion;
- future root-synthesis validation seam.

Legacy CLI display, observatory, benchmark, retrieval projection, and general
non-truth metadata reads may continue through `ProjectStore` during PHASE 3.
Their outputs must not become promotion authority.

## Required PHASE 3 seam

```text
ProjectStore
    ↑ compatibility storage/status machine
TruthStoreFacade
    ├── capture AssertionIdentity
    ├── capture DependencySnapshot / AssumptionSnapshot / AuthorityBindings
    ├── append immutable ClaimSnapshot
    ├── compare stored snapshot with reconstructed current truth
    ├── validate execution/audit/promotion
    └── compare-and-transition via durable Intent → transition → Receipt
```

`TruthStoreFacade` must remain thin: filesystem artifacts under `truth/` plus
the existing ProjectStore. It must not introduce Research Plane strategy or a
new runtime database.

## Planned new production truth path

```text
campaign/run start
        ↓
TruthStoreFacade.capture_claim_snapshot
        ↓
persist immutable snapshot; store exact snapshot hash in run/campaign
        ↓
context/candidate/formalization/audits carry claim_snapshot_hash
        ↓
before audit: reconstruct + compare
        ↓
Final Audit result binds audited_claim_snapshot_hash
        ↓
TruthMutationIntent
        ↓
reload and reconstruct current truth
        ↓
typed snapshot comparison + authority validation
        ↓
MATCH only: ProjectStore transition through facade
        ↓
TruthMutationReceipt with before/after record hashes
```

Resume uses the same comparison. Assertion changes hard-block; dependency,
assumption, authority, trust-policy, semantic-input, or unknown compatibility
requires revalidation unless a stricter authority failure blocks outright.

## Invariants for implementation

1. Statement identity, authority identity, and artifact identity remain
   separate.
2. Snapshot files are append-only/content-addressed; an existing different body
   is an integrity error.
3. Execution/provider/UI metadata is excluded from assertion and claim identity.
4. Premises remain typed `PREMISE`, never collapsed into proved theorems.
5. Unknown schemas and compatibility fail closed.
6. Canonical proof/replay authority must contain revalidated actual bytes.
7. No theorem promotion can bypass snapshot comparison.
8. Audit PASS alone is not mutation authority.
9. Research state never becomes theorem truth.
10. PHASE 4+ structures remain `NOT_STARTED`.
