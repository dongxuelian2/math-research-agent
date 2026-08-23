# Authoritative v3.2 Normative Requirements

This index is derived from the external specification pinned in
`../AUTHORITATIVE_SPEC_REFERENCE.md`. It is a requirements index, not a copy
of the specification. `COMPLIANT` means the required invariant is represented
and enforced; `PARTIALLY_COMPLIANT` means only a bounded subset is proven;
`NONCOMPLIANT` means the durable semantic contract is absent or contradicted;
`INSUFFICIENT_EVIDENCE` means the audit did not establish either direction.

## Plane and identity boundary

| ID | Spec sections | Normative requirement | Required durable identity / invariant |
|---|---|---|---|
| N-01 | §0, §1, §2 | Keep Truth, Research, and Execution as separate planes with one owner for every durable semantic fact. | Theorem truth is owned by Truth; maps/obligations/coverage by Research; jobs/attempts/effects by Execution; cross-plane projections are non-authoritative. |
| N-02 | §3, §4 | Separate AssertionIdentity from AuthorityBindingIdentity and bind every truth-sensitive object to an exact ClaimSnapshot. | Domain-separated hashes; identity-critical theorem fields; no stale snapshot may reach execution, audit, root, final audit, or mutation. |
| N-03 | §4.1–§4.4 | ClaimSnapshot is a durable immutable snapshot, not a hash-only convenience. | `claim_snapshot_id`, `theorem_id`, `assertion_text`, `assertion_hash`, `claim_type`, `notation_scope`, `dependency_snapshot_ref`, `captured_status`, `project_record_file_hash`, `captured_at`, `schema_version`. |
| N-04 | §4.5 | External Mutation Guard runs at campaign/session start, resume, root synthesis, final audit, and TruthMutation compare-and-transition. | Exact or explicitly compatible snapshot transition; mismatch is `CLAIM_SNAPSHOT_STALE` and forces revalidation. |

## Truth and Research Plane

| ID | Spec sections | Normative requirement | Required durable identity / invariant |
|---|---|---|---|
| N-05 | §5.2 | Campaign scope is explicit and immutable. | `CampaignScopeManifest`: root snapshot ref, human-required constraints, human-excluded scope, policy version, manifest hash. |
| N-06 | §5.3–§5.6 | Scope is tracked as immutable anchors, dispositions, and one-way transfers. | `CoverageAnchorDefinition`, `CoverageDisposition`, `CoverageTransfer`; transfer graph is old→new only, source-explicit, acyclic, and cannot silently discard root-relevant scope. |
| N-07 | §5.7, §27.1 | Root readiness requires complete current root-relevant coverage. | Every root-relevant anchor is current `RESOLVED`; exclusion/deferment cannot bypass the gate; bookkeeping is not a mathematical audit. |
| N-08 | §6 | ResearchMapVersion is immutable and complete. | Root snapshot, structural map, open obstructions, invariants, parameters, termination mechanisms, coverage refs, obligation refs/dispositions, evidence refs, map hash, schema version. |
| N-09 | §7 | Obligation semantics, disposition, and scheduling control are separate durable objects. | `ObligationSpec` immutable revisions; `ObligationDisposition`; `ObligationControlState`; scheduling projection (`IDLE/BLOCKED/READY/RUNNING/RETRY_PENDING`) is not research truth. |
| N-10 | §8 | Decisions are explicit and reverse-invalidatable. | `DecisionBasis` carries evidence/authority/dependency/policy identity; reverse index maps evidence/authority/snapshot → decisions → maps/obligations/anchors; invalidation reopens or reviews current decisions. |
| N-11 | §9 | ResearchMap patches are authorized by level and cannot smuggle destructive scope changes. | Level A/B/C/D; Level C requires ArchitectProposalReceipt, ArchitectureCriticReceipt, CoverageValidationReceipt, and DecisionBasis; Level D is a separate TruthMutation saga. |

## Execution, trust, and artifact protocol

| ID | Spec sections | Normative requirement | Required durable identity / invariant |
|---|---|---|---|
| N-12 | §14–§17 | Runtime objects are durable and distinct. | Logical Job, AttemptIntent, Lease/`lease_epoch`, ResultArtifact, AcceptedEffect; late results remain ingestible artifacts but cannot become old executor state. |
| N-13 | §17.7–§17.9 | Commit-time semantic acceptance is a fence, not a provider-success flag. | Exact current semantic input or policy-compatible reuse; binding, root, obligation, directive, coverage, dependency, authority, and trust-policy identity revalidated before effect. |
| N-14 | §17.10–§18 | EffectBundle/effect slots are atomic and idempotent. | Deterministic unique effect slots; SQLite transaction; semantic effects cannot be applied twice; truth mutation remains separately authorized. |
| N-15 | §17.11, §18 | Artifacts are committed crash-safely and reconciliation is explicit. | temp write, fsync, atomic rename, directory fsync, registration; recovery never fabricates missing artifacts or accepted effects. |
| N-16 | §22 | ResultTrustKernel is the sole trust acceptance facade. | Worker result → verifier → scope/dependency → authority/applicability → replay/leak/policy → durable `TrustReceipt`; statuses are `RAW`, `VERIFIED_LOCAL`, `PROVISIONAL`, `REJECTED`, `PROMOTION_ELIGIBLE`. |
| N-17 | §23.1 | Trust policy identity is machine-compatible, not a single free-form hash. | `TrustPolicyRef`: policy version/fingerprint, schema hash, required checks, implementation hashes, critical-config hash; unknown compatibility fails closed. |
| N-18 | §23.3 | Mathematical verifier independence is a durable, policy-checked fact. | `VerifierIndependenceReceipt` fields: `worker_model`, `verifier_model`, `same_provider`, `same_model`, `fresh_context`, `worker_hidden_reasoning_exposed`, `shared_prompt_family`, `shared_source_artifacts`, `independence_policy`, `policy_satisfied`. |
| N-19 | §12, §37 | SessionClosure is a typed projection and ambiguity cannot become fake evidence. | Raw artifacts, typed evidence, failed routes, notes, new-obligation proposals, unresolved items, provider/runtime provenance, exact session/map/obligation/root identity. |

## TruthMutation, root, and final authority

| ID | Spec sections | Normative requirement | Required durable identity / invariant |
|---|---|---|---|
| N-20 | §21.1–§21.5 | Truth promotion is an intent → compare-and-transition → receipt → reconciliation saga. | Intent has mutation id, theorem id, expected snapshot, old/target status, audit receipt hash, mutation policy fingerprint; ProjectStore atomically writes `v3_truth_binding` with status. |
| N-21 | §21.5–§21.7 | Recovery may close a promotion only when the complete binding matches. | Binding includes mutation id, ClaimSnapshot id, assertion hash, audit receipt hash, policy fingerprint; partial status writes are conflicts, not success. |
| N-22 | §27.1–§27.2 | RootSynthesisManifest independently establishes authority boundary. | Exact fields: `root_claim_snapshot_ref`, `map_version`, `campaign_scope_manifest_ref`, `coverage_resolution_manifest`, `required_obligation_refs`, `evidence_refs`, `dependency_refs`, `authority_receipts`, `applicability_receipts`, `trust_policy_ref`, `manifest_hash`. |
| N-23 | §27.3 | Synthesis prompt provenance is source-bound. | `SynthesisPromptPackage` carries `source_manifest_hash`, projection-builder version, and projection hash; Final Audit uses the exact manifest. |
| N-24 | §28 | Final Audit is counterexample, dependency, exhaustiveness/converse, boundary, and final-proof audit. | Failure creates evidence/blocker/reopen; it cannot repair the world or substitute a later handoff identity. |
| N-25 | §28, §46 | Promoted proof is immutable and consolidation rewrites require a new final audit. | Immutable promoted proof hash; consolidation re-audit is bound to the exact root/final artifacts; body rewrite creates a new audited proof. |

## Freeze and regression obligations

Sections §34–§36, §39–§40, §44, and §46 make the above schema-versioned,
fail-closed, restart-safe, and regression-testable. In particular, the freeze
checklist explicitly names coverage anchors/dispositions/transfers, the
TruthMutation binding marker, verifier independence, the exact root manifest,
source-manifest hashing, immutable promoted proof, and consolidation re-audit.

The completed production traceability is in
`SPEC_TO_CODE_TRACEABILITY.md`; the attack evidence is in the R-01/R-02
documents in this directory.
