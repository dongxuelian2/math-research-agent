# Root Synthesis Normative Diff

This is a static contract comparison only. `Phase7Store.synthesize_root` was
not called, no root artifact was created, and no truth promotion was attempted.

## Required manifest fields

| Spec field | Current object / field | Classification | Evidence and consequence |
|---|---|---|---|
| `root_claim_snapshot_ref` | `RootSynthesis.root_claim_snapshot_hash` | SEMANTIC_EQUIVALENT | Exact root hash is required and checked, but the durable ref/id form is not the specified field. |
| `map_version` | `research_map_id`, `research_map_version`, `research_map_hash` | SEMANTIC_EQUIVALENT | Exact map tuple is bound; coverage manifest is not part of the tuple. |
| `campaign_scope_manifest_ref` | none | MISSING | No CampaignScopeManifest exists in CampaignStore or RootSynthesis. |
| `coverage_resolution_manifest` | none | MISSING | No anchor/disposition/transfer graph or resolution manifest exists. |
| `required_obligation_refs` | `obligation_ids`, `closed_obligation_ids` | SEMANTIC_EQUIVALENT | Root capture requires all current obligation IDs to be closed, but it does not prove completeness against scope anchors or reconstruction obligations. |
| `evidence_refs` | `evidence_ids`, `audit_artifact_refs`, session closure evidence | SEMANTIC_EQUIVALENT | Evidence is referenced and hashed, but not partitioned by coverage/authority/applicability receipt. |
| `dependency_refs` | no root-level field; map/ClaimSnapshot have dependency hashes | AMBIGUOUS | Dependency identity exists in subordinate objects but is not independently enumerated in the root manifest. |
| `authority_receipts` | canonical resolution/evidence refs outside RootSynthesis | MISSING | No root-owned authority receipt list or exact linkage. |
| `applicability_receipts` | applicability checks outside RootSynthesis | MISSING | No root-owned applicability receipt list or exact linkage. |
| `trust_policy_ref` | ClaimSnapshot/TruthMutation `trust_policy_fingerprint` | DERIVED_BUT_NOT_DURABLE | A fingerprint is carried in some objects; no structured TrustPolicyRef or compatibility validator is bound to root. |
| `manifest_hash` | `synthesis_hash` / body hash | SEMANTIC_EQUIVALENT | Current immutable record/body hashes protect the current shape, not the exact normative manifest contents. |

## Adjacent required boundary

The spec also requires `SynthesisPromptPackage` with
`source_manifest_hash`, `projection_builder_version`, and `projection_hash`.
Current context/candidate/audit artifacts are written and hashed in places, but
there is no source-manifest package or validator that proves Final Audit used
the exact root source projection. Classification: `MISSING`.

## Gate consequence

Current `RootSynthesis.capture` proves a closed obligation frontier by comparing
`obligation_ids` to `closed_obligation_ids`. It does not independently establish
scope, coverage transfer resolution, authority receipt completeness,
applicability receipt completeness, or machine trust-policy identity. Therefore
`ROOT_SYNTHESIS_MANIFEST_STATUS=NONCOMPLIANT` for the v3.2 exact authority
boundary, even though the existing Phase 7 object has useful root/map/audit
hash checks.
