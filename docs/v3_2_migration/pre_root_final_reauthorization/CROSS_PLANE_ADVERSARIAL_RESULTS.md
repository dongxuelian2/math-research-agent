# Cross-Plane Adversarial Results

## Independent probe command

```text
uv run --project openprover --extra test python docs/v3_2_migration/pre_root_final_reauthorization/run_final_adversarial_probes.py
```

The probe creates temporary project roots and uses public production entry
points. It does not write production sources or historical evidence.

| Probe | Expected invariant | Actual result | Verdict |
|---|---|---|---|
| `F005-A1-A14` | Untrusted authority representations cannot authorize destructive map mutation. | Ten named raw/clone/serialization/envelope/cross-map/casing/thesis/scope cases rejected; maps unchanged. | `PASS` |
| `F007-RESTART-CONTROLS` | Missing validator blocks; stale validator fences; exact current validator accepts. | All three outcomes observed on separate temporary stores. | `PASS` |
| `NF-003-PARTIAL-BINDING` | A root-only binding cannot authorize a semantic route when current map/directive/session context exists. | Validator returned `True`; runtime accepted result and semantic payload were returned; accepted result ID populated. | `FAIL` |
| `NF-004-NO-BACKEND-GUARD` | `require_execution_binding=True` fails closed even without a backend. | Semantic structured response returned with no runtime binding. | `FAIL` |
| `F002-TERMINAL-REJECTION` | Expired late output is terminal and cannot be consumed across restart, selection, effects, or routed parsing. | All terminality checks passed; no semantic callback or EffectSlot. | `PASS` |

## Cross-plane composition matrix

| Composition | Result | Observation |
|---|---|---|
| C1 forged authority + current binding | `FAIL` overall | F-005 raw forgery is rejected, but a partial current-domain binding is accepted by NF-003. |
| C2 forged authority + standalone semantic route | `FAIL` overall | F-005 forgery is rejected, but NF-004 returns an unbound semantic response. |
| C3 rejected result + restart + forged authority | `PASS` for exercised path | F-002 terminal result remains non-selectable and F-005 mutation attacks remain rejected. |
| C4 rejected result + standalone route | `FAIL` overall | The terminal-result path passes, but the no-backend semantic guard is bypassable. |
| C5 stale replay + authority cloning | `PASS` | Stale complete binding fences; cloned/serialized authority cannot mutate the map. |
| C6 `AFTER_PROVIDER_RESULT` + terminal rejection | `PASS` | Existing recovery evidence plus the independent F-002 terminal probe pass. |
| C7 restart + late result + current-domain binding | `FAIL` overall | Complete-binding late/restart controls pass; partial binding remains accepted by NF-003. |
| C8 wrong-thesis authority + replay | `PASS` | Cross-map, wrong-thesis, and replay variants are rejected without mutation. |
| C9 valid authority + rejected EffectSlot source | `PASS` | Legitimate governance path remains green; rejected result cannot prepare an effect. |
| C10 old authorized payload + new generation | `PASS` for complete binding | Complete binding validator/reconciliation fences the old generation; broader partial binding remains open. |

## State-machine check

The terminal rejection behavior is structurally fail-closed: a rejected result
is recorded as `STALE_FENCED`/non-authoritative, `accept_result()` selects only
authoritative results, and effect preparation requires the accepted authoritative
source. The remaining blocker is validator completeness, not an unobserved
terminal state.

## Old-head integrity check

The old repair base `11826b7c43957c07bd9dc34f01da610bbd431e1b` was checked in a
temporary detached worktree. Its frozen X1 probe reported the old behavior:
the runtime accepted C1 as authoritative without a stale comparison. The
current candidate's complete-binding restart control now fences that case.
This confirms that the audit probes are sensitive to the repaired boundary;
it does not close the newly found partial/standalone variants.
