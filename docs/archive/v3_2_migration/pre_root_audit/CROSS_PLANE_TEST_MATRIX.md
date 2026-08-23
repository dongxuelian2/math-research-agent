# Cross-Plane Adversarial Test Matrix

The new runner is
`docs/v3_2_migration/pre_root_audit/run_cross_plane_probes.py`. It uses mock
providers and temporary project roots; no live provider or project artifact is
changed.

| ID | Adversarial boundary | Expected | Observation/evidence | Verdict |
|---|---|---|---|---|
| X1 | stale ClaimSnapshot C1 result after current claim C2 | retain provenance, block current semantic authority | runtime accepted a C1-bound result as authoritative; no SQLite stale comparison occurred. Domain gates still need to revalidate. | `PARTIAL` |
| X2 | old TacticalSession/Closure against reframed map | reject, revalidate, or explicit transfer | v1 closure resolved O1 after v3 marked O1 `SUPERSEDED` and transferred to O2; map advanced v3→v4. | `FAILED` |
| X3 | canonical authority changes while audit result is pending | retain artifact, block promotion authority | canonical artifact/body/hash and promotion-guard tests pass; runtime itself does not perform the authority comparison. | `CERTIFIED_WITH_LIMITATION` |
| X4 | expired generation-1 lease submits high-value result | retain artifact, fence result/effect | `ingestion_state=INGESTED`, `authoritative=True`, attempt became `RESULT_RECORDED`. | `FAILED` |
| X5 | duplicate SessionClosure and review clock | one resolution/map revision and no clock double count | RuntimeEffectCoordinator replay test passes; live orchestrator does not use the coordinator. | `PARTIAL` |
| X6 | ArchitectureReview replay after runtime restart | one review identity and one reset | Governance replay/idempotence tests pass; the production call graph has no EffectSlot wrapper. | `CERTIFIED_WITH_LIMITATION` |
| X7 | authorized ArchitecturePatch replay | no vN+2 duplicate | exact application identity and target-map replay tests pass. | `CERTIFIED_WITH_LIMITATION` |
| X8 | TruthMutation PREPARED/receipt split | deterministic single transition and receipt | prepared recovery and truth-mutation tests pass; production finalization is direct facade, not runtime-slot owned. | `PARTIAL` |
| X9 | ResearchMap domain commit before runtime acknowledgement | recover exact map, no duplicate revision | adapter test exists, but production orchestrator bypasses it and X2 exposes missing map binding. | `PARTIAL` |
| X10 | destructive transfer crash/scope loss | old map or complete exact authorized map | explicit O1/O2→N1/N2 transfer and negative-scope tests pass; no actual process crash was injected inside filesystem patch application. | `CERTIFIED_WITH_LIMITATION` |
| X11 | Gemini/Codex multiple successes | stable LogicalJob, deterministic winner, one effect | D2/D9/D25 and heterogeneous routing tests pass; external delivery remains at-least-once. | `CERTIFIED_WITH_LIMITATION` |
| X12 | provider accepted, dispatcher crashes before acknowledgement | stable identity and safe redispatch/effect | PENDING/CLAIMED and artifact split tests pass; `AFTER_PROVIDER_RESULT` leaves DISPATCHED stranded. | `FAILED` |
| X13 | cancellation/completion/stale result race | one deterministic current terminal truth | D14 and runtime race coverage pass; lease-expiry gap remains open. | `CERTIFIED_WITH_LIMITATION` |
| X14 | restart while review is due | due state survives; success cannot reset clock | restart/governance/checkpoint tests pass; no crash was injected between review artifact and clock write. | `CERTIFIED_WITH_LIMITATION` |
| X15 | legacy checkpoint without new bindings | no fabricated history; revalidation required | D21 and checkpoint migration tests pass; runtime records `MIGRATED_FROM_LEGACY_CHECKPOINT`. | `CERTIFIED_WITH_LIMITATION` |
| X16 | two isolated projects with same local IDs | no cross-project lease/result/effect/artifact authority | D22 and isolation tests pass. | `CERTIFIED` |

The matrix is not a “268 passed” sign-off: X2 and X4 are direct P0-class
failures, and X12 is a P1 recovery failure.
