# New Findings From the Independent Re-Audit

## NF-001 — P1 — rejected late provider payload remains consumable

Related original finding: F-002.

`DurableProviderDispatcher.execute` correctly records an expired result as
non-authoritative and returns runtime metadata saying `accepted=False` and
`authoritative=False`.  The returned provider payload is nevertheless kept by
`RoutedLLMClient`; `_pipeline_llm_handler` parses the structured body without
checking those flags.  `pipelines.py` then treats `success`/`high_value` as
normal pipeline input and can schedule verification or close an obligation.

Independent probe: `RX-LATE-PAYLOAD-AUTHORITY`.

Observed result:

```text
runtime_accepted = false
runtime_authoritative = false
parsed_high_value = true
parsed_success = true
effect_slots = 0 at the probe boundary
```

The zero slots do not clear the finding: the problem is authority entering the
semantic pipeline before the later EffectSlot boundary.  Minimum repair
frontier: every routed semantic consumer must terminally reject or quarantine a
response whose runtime authority is false; the provider body must not be
promoted into worker events, scheduler state, Research effects, or Truth gates.

## NF-002 — P1 — restart reconciler accepts stale persisted authority

Related original finding: F-007 and X1.

`RuntimeReconciler._accept_pending_results` selects a durable successful result
without a domain binding validator.  `ResearchOrchestrator` invokes recovery
before current map/governance objects are initialized.  A C1-bound result
recorded before drift was accepted after restart when the current project had a
new ClaimSnapshot and map version.  Probe: `RX-RESTART-STALE-DOMAIN`.

Observed result:

```text
persisted map version = 1
current map version = 2
persisted root != current root
result authoritative before restart = true
accepted_result_id after restart = the stale result id
```

Minimum repair frontier: recovery must either load and validate current domain
authority before selecting a winner or leave pending results unaccepted until a
domain-aware validator is available.  Binding persistence alone is not a
current-authority check.

## Classification

Both findings are P1/P0-root-relevant because they can feed stale information
into Research/Truth/Governance ownership.  They are not production fixes in
this audit; they remain documented blockers.
