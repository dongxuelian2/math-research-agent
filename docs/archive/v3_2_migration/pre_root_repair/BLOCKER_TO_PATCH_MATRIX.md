# Blocker-to-Patch Matrix

| Finding | Old failure | Repair location | Adversarial evidence | Legitimate evidence |
|---|---|---|---|---|
| F-002 / NF-001 | Fenced provider body remained parseable and could be promoted by pipeline logic. | `runtime_dispatch.py`, `routing.py`, `runtime_model.py` | `test_f002_rejected_provider_payload_is_terminal_and_non_consumable`; `test_f002_routed_client_cannot_parse_late_rejected_payload` | `test_durable_runtime.py` and full suite: valid accepted results/recovery remain green. |
| F-005 | Caller-forged typed `AUTHORIZED` object changed `strategic_thesis`. | `research_store.py` | `test_f005_forged_authorization_cannot_mutate_destructive_map` (thesis and scope variants) | `test_production_governance_e2e` remains green through the durable controller path. |
| F-007 / NF-002 | Restart accepted stale C1/v1 result; formalization/certification routers had no binding. | `runtime_reconciler.py`, `runtime_backend.py`, `orchestrator.py`, `formalization.py`, `certification.py`, `routing.py` | `test_f007_restart_does_not_accept_without_binding_validator`; `test_f007_restart_fences_mismatched_binding`; unbound standalone router test | `test_f007_configured_standalone_router_accepts_valid_binding`; validator-backed restart acceptance; full suite. |

## Binding lifecycle after repair

```text
trusted run/domain context
        -> CrossPlaneExecutionBinding
        -> LogicalJob / Attempt / Result / EffectSlot
        -> validator at acceptance, reconciliation, and effect preparation
        -> semantic consumer only after accepted result
```

Validator-less reconciliation records manual review and does not select a
pending result. A missing standalone binding is an explicit runtime conflict;
there is no synthetic `AUTHORIZED` fallback.
