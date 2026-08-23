# R-02A Audit Identity Overwrite Attack

## Normative question

The audit identity must be provider-returned, parsed, normalized, persisted,
and consumed as the same exact ClaimSnapshot identity. A local run-state hash
cannot be substituted after parsing without an equality check and an explicit
identity provenance record.

## Production path

`audit_protocol.parse_audit_response` and
`normalize_audit_result` type-check `audited_claim_snapshot_hash`, but do not
compare it to the run snapshot. In `AuditCoordinator.run_audits`, after parsing
each specialist response, the code assigns
`data["audited_claim_snapshot_hash"] = claim_snapshot_hash`; the final auditor
path performs the same assignment. `AuditGate` is then built from the local
`claim_snapshot_hash`.

The isolated `probes/r02_audit_identity_probe.py` used the production schema
parser and replayed that exact post-parse assignment. No provider, run
directory, project, root synthesis, or truth mutation was executed.

## Adversarial cases

| Case | Provider-returned identity | Parsed/normalized | Persisted audit | Gate/root-visible | Classification |
|---|---|---|---|---|---|
| A1 correct | current | current | current | current | `SAFE_NORMALIZATION` (observable identity unchanged) |
| A2 wrong | foreign | foreign | current | current | `IDENTITY_OVERWRITE_WITHOUT_CROSSCHECK` |
| A3 omitted | empty/default | empty/default | current | current | `IDENTITY_OVERWRITE_WITHOUT_CROSSCHECK` |
| A4 stale | stale | stale | current | current | `IDENTITY_OVERWRITE_WITHOUT_CROSSCHECK` |
| A5 different theorem | different hash | different hash | current | current | `IDENTITY_OVERWRITE_WITHOUT_CROSSCHECK` |
| A6 malformed | integer | parser rejects; coordinator exception path | current | current | `SCHEMA_PREVENTS_CASE`; exception result is still locally rebound |
| A7 specialist/final conflict | foreign specialist and different foreign final | each preserves its own foreign value | both current | current | `IDENTITY_OVERWRITE_WITHOUT_CROSSCHECK` |

## Finding

`R02_AUDIT_IDENTITY_DEFECT=YES` and status `REPRODUCED_DEFECT`. The current
implementation can make an audit artifact, AuditGate, and root-visible audit
identity appear current even when the provider returned a foreign, stale,
omitted, or conflicting identity. The schema prevents a non-string malformed
identity from parsing, but it does not provide the required identity equality
check or provenance chain.

This finding is independent of whether the provider was mathematically right
or wrong. It is an authority-binding defect. No repair was made in Pass B.
