# F-005 — Governance Bypass Repair Report

## Finding and cause

`ResearchStoreFacade.revise_map` trusted a caller-supplied revision reason. A
strategic thesis mutation could therefore be presented as `HUMAN_STEERING`
without the ArchitectureReview → Probe (when required) → Patch → Critic →
Authorization chain.

## Repair

The store now compares the new thesis with the current thesis before applying
the revision. Any semantic change is destructive regardless of the free-form
reason. It requires a typed `PatchAuthorization` with status `AUTHORIZED`, an
exact root ClaimSnapshot hash, the current source map id/version/hash, and the
required scope validation. Direct callers fail closed; an authorized patch path
remains available and creates one governed map revision.

## Proof

- GOV-THESIS-BYPASS now reports
  `rejected: ... DESTRUCTIVE_REFRAME_REQUIRES_GOVERNANCE`.
- Existing positive/negative governance tests prove authorized reframes create
  one version and incomplete scope or critic rejection leaves v1 unchanged.
- Same-map non-destructive revisions remain available for ordinary semantic
  obligation updates.

## Status

`F_005_STRATEGIC_THESIS_AUTHORIZATION = CLOSED`
