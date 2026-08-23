# R-02B Coverage Omission Attack

## Normative question

The v3.2 spec replaces a generic “open coverage” notion with a durable
`CampaignScopeManifest`, immutable `CoverageAnchorDefinition` objects,
`CoverageDisposition`, one-way `CoverageTransfer`, and a
`coverage_resolution_manifest`. Root readiness is complete only when every
root-relevant tracked anchor is currently resolved and every transfer is
accounted for. Closed obligations are not a proof of complete root coverage.

## Current durable model

The current `ResearchMap` contains structural fields, untyped added/removed
scope strings, obligation references, route failures, and evidence references;
it has no coverage-anchor/disposition/transfer references. Current
`ObligationDisposition` has `superseded_by`, but that is an obligation
replacement field, not an anchor-level transfer graph with source/target map
versions and cycle validation. `RootSynthesis.capture` requires
`obligation_ids == closed_obligation_ids` and evidence, but accepts no campaign
scope manifest or coverage resolution manifest.

The read-only `probes/r02_coverage_verifier_inventory.py` confirms the absent
symbols/fields and does not invoke Phase 7. No root synthesis was executed.

## Adversarial cases

| Case | Attack | Result under current durable model | Normative status |
|---|---|---|---|
| C1 | Two root-relevant scope anchors but only one obligation | No anchor relation exists to represent the omission. | `NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL` |
| C2 | Obligation closed but anchor missing | Obligation closure can be recorded without an anchor. | `NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL` |
| C3 | Obligation superseded without coverage transfer | `superseded_by` is not a coverage transfer receipt. | `NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL` |
| C4 | Transfer missing successor | No transfer object/manifest validates successor completeness. | `NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL` |
| C5 | Transfer cycle | No coverage graph exists on which to reject a cycle. | `NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL` |
| C6 | Same evidence reused for unrelated coverage roles | Evidence refs are not role/anchor-bound in a coverage manifest. | `NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL` |
| C7 | All ResearchObligations closed but campaign scope incomplete | Root precondition observes only obligation closure. | `CLOSED_FRONTIER_NOT_PROVEN_COMPLETE_ROOT_COVERAGE` |
| C8 | Map frontier closed but reconstruction obligation missing | No required-obligation/reconstruction coverage manifest exists. | `NOT_REPRESENTABLE_BY_CURRENT_DURABLE_COVERAGE_MODEL` |

## Finding

`COVERAGE_MODEL_STATUS=NONCOMPLIANT`. The issue is structural, not a failed
runtime edge case: the current durable model cannot encode the counterexamples
needed to prove that scope was preserved. The absence of a root synthesis run in
the canonical project is intentional and is not evidence of compliance.
