# R-01 Public Binding Caller Audit

## Scope and oracle

The normative question is whether public semantic execution can cross the
provider boundary, record an accepted result, or create a semantic effect
before exact binding validation. The matrix in
`R01_PUBLIC_BINDING_CALLER_MATRIX.tsv` covers the requested 14 variants at the
public `RoutedLLMClient.call` seam. The isolated probe is
`probes/r01_public_binding_probe.py`.

The probe used an in-memory `ResearchOrchestrator` validator context and a
counting fake provider. It used a temporary directory only for client working
paths. It did not initialize a project, write a theorem/map/obligation/session,
create a runtime backend, execute root synthesis, or perform TruthMutation.

## Production order

`RoutedLLMClient._execute_route` first checks `require_execution_binding`, then
requires a validator, then invokes the validator. Only after that block does it
call the client factory/provider and, when configured, create the durable
runtime job. The orchestrator validator checks the current root snapshot,
complete map identity, and current obligation/directive/session/governance
dimensions. The truth-only and map-only validators are narrower by design.

## Results

| Group | Result |
|---|---|
| `COMPLETE_CURRENT` | Accepted; one fake provider call and one returned result. No semantic, truth, or research mutation was configured. |
| Missing map/obligation/directive/session | All rejected with `RuntimeConflict`; provider count, accepted-result count, and mutation/effect counts were zero. |
| Stale map/session, wrong root, wrong theorem surrogate, cross-map/session | All rejected before provider. |
| Root-only or map-only on the normal path | Both rejected because the current normal context requires the remaining dimensions. |
| No backend + unbound | Rejected by the binding guard before the no-backend provider branch. |

The existing production regression coverage independently includes
`test_f007_current_domain_rejects_root_only_semantic_binding` and
`test_nf004_no_backend_required_binding_rejects_before_provider` in
`openprover/tests/math_research/test_pre_root_authority_repairs.py`.

## Finding

`R01_PUBLIC_CALLER_MATRIX_COMPLETE=YES` and
`R01_DEFECT_REPRODUCED=NO` for the tested public seam. The binding gate is
confirmed effective for the requested rejection oracle. This is not a claim
that every possible caller or backend path is exhaustively tested; the next
queue retains a broader caller/reconciler matrix.

The `WRONG_THEOREM` row is explicitly recorded because the current binding
schema has no separate `theorem_id` field. The probe uses a wrong root
ClaimSnapshot hash as the available theorem-identity surrogate; it is rejected
by the exact snapshot check. This is a schema observation, not an unreported
acceptance defect.
