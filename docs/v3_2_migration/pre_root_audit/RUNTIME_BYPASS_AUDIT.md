# Runtime Bypass Audit

## Authority map

| Candidate authority | Production observation | Verdict |
|---|---|---|
| `pipeline_state.json`, `routing_state.json`, run `state.json` | Used as desired-work, routing, checkpoint, and portable compatibility projections. No production write was found that changes SQLite accepted-result, lease, or outbox current state. | `CERTIFIED_WITH_LIMITATION` |
| SQLite `LogicalJob`/`Attempt`/`Outbox` | Normal orchestrator startup creates/reconciles `SQLiteRuntimeBackend`; routed provider calls create AttemptIntent and dispatch through `DurableProviderDispatcher`. | `CERTIFIED_WITH_LIMITATION` |
| Filesystem result/artifact manifests | Bodies are written and hash-registered; reconciliation can adopt orphan manifests. They do not alone select the accepted winner. | `CERTIFIED_WITH_LIMITATION` |
| Provider transport retries | Remain inside a provider client/one routed call; fallback creates another Attempt under the same LogicalJob. | `CERTIFIED_WITH_LIMITATION` |
| Direct provider path in `RoutedLLMClient` | `_execute_route` has an explicit `runtime_backend is None` branch (`routing.py:953-955`). The normal production run, formalization, certification, audit, and literature constructors pass a runtime backend. `build_run_preview` uses a no-runtime router only for read-only route preview. | `PARTIAL` |
| Semantic effect authority | Live orchestrator directly calls Research/Truth/Governance stores; no `RuntimeEffectCoordinator` or `apply_effect_once` call appears in production modules. | `FAILED` |
| Legacy checkpoint import | `import_legacy_checkpoint` records classification/provenance and creates no fabricated attempts/outbox/journal history; D21 passes. | `CERTIFIED_WITH_LIMITATION` |

## Production provider-entry trace

The audited production call sites are:

- orchestrator: runtime backend created at `orchestrator.py:287-315`;
- formalization: runtime backend passed at `formalization.py:66-72`;
- certification: runtime backend passed at `certification.py:119-125`;
- audit coordinator, candidate engine, and literature: clients receive the
  orchestrator’s runtime-backed router;
- provider-smoke CLI: creates a job and calls
  `DurableProviderDispatcher.execute` at `cli.py:173-215`.

The route dispatcher creates a LogicalJob at `routing.py:942-951` and invokes
the durable dispatcher at `:957-975`. However, the created job does not pass
`claim_snapshot_hash`, `research_map_version`, `governance_ref`, or
`directive_context_refs`; the payload carries only method/role/obligation/
branch. This weakens stale authority binding even when dispatch itself is
durable.

## Direct semantic bypass

The live close/finalize path is:

```text
_close_research_session
  -> ResearchStoreFacade.resolve_session_closure
  -> GovernanceController.record_effect
  -> GovernanceController.record_session

_finalize
  -> TruthStoreFacade.compare_and_transition
```

The path is not:

```text
accepted AttemptResult
  -> EffectSlot PREPARED
  -> domain identity recovery
  -> DOMAIN_APPLIED
  -> ACKNOWLEDGED
```

This is an architecture deviation confirmed by static ownership tracing, not
an inference from test count.

## Conclusion

SQLite is the intended and mostly actual execution control plane. It is not
yet the sole production authority for semantic effects, and the optional
no-runtime client seam remains callable by direct callers. The minimum repair
is to make every production semantic effect enter through a runtime-owned,
claim/map-bound effect adapter and to fail closed when no runtime backend is
provided for a provider invocation.
