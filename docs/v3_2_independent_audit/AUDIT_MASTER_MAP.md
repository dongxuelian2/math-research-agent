# Audit Master Map

## Handoff decision

This is a version-controlled map of the actual candidate implementation. It is ready for an independent forensic audit of the implementation and its public/recovery paths. It is not a system certification and cannot establish v3.2 normative compliance because the authoritative freeze specification was not found.

READY_FOR_INDEPENDENT_FORENSIC_AUDIT=YES (implementation-map handoff)
AUTHORITATIVE_V3_2_SPEC_AVAILABLE=NO
V3_2_SPEC_COMPLIANCE=NOT_ESTABLISHED
CURRENT_SELF_CERTIFICATION=NOT_TRUSTED
ROOT_SYNTHESIS=BLOCK_FOR_REAUDIT
TRUTH_PROMOTION=BLOCK_FOR_REAUDIT

## I. Exact Git baseline

- Canonical repository: E:\tool\math\agent\math-research-agent
- Baseline HEAD: 3229aced9fa9bcae41c5ddfea6b6291a6e68d725
- origin/main at precheck: the same SHA
- annotated tag: v3.2-audit-baseline-20260823
- audit branch: audit/v3.2-forensic-20260823
- worktree was clean before audit branch creation
- main was not changed
- audit changes are restricted to docs/v3_2_independent_audit/

See [REPOSITORY_BASELINE.md](REPOSITORY_BASELINE.md) and [REPOSITORY_FILE_INDEX.tsv](REPOSITORY_FILE_INDEX.tsv).

## II. Repository structure

The baseline has 276 tracked files: 132 Python, 7 Bash, 2 PowerShell, 106 Markdown, and 41 tracked tests. The active production research layer is openprover/openprover/math_research/ with 63 modules; its domain tests are in openprover/tests/math_research/ with 38 files. docs/v3_2_migration/ contains 87 retained historical/repair/handoff files, not an authoritative spec.

See [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md).

## III. Production architecture

The implementation has distinct but interacting owners:

1. ProjectStore and state_machine own JSON theorem lifecycle.
2. TruthStoreFacade owns ClaimSnapshot identity, truth validation, mutation intents, and receipts.
3. ResearchStoreFacade owns ResearchMap, obligations, directives, tactical sessions, closures, and governed reframes; ResearchMap is non-authoritative for theorem truth.
4. ModelRouter/RoutedLLMClient and OpenProver adapter own provider transport and typed worker/audit boundary.
5. SQLiteRuntimeBackend, DurableProviderDispatcher, and RuntimeEffectCoordinator own durable execution, result acceptance, leases, and exactly-once effect slots.
6. RuntimeArtifactStore owns content-addressed bodies/manifests.
7. GovernanceController owns clocks, reviews, probes, patches, critics, authorizations, and structural effects.
8. Phase7Store owns root, final consolidation, re-audit, and promotion-closure artifacts.
9. Orchestrator coordinates these owners and is the primary sequencing surface.

See [PRODUCTION_MODULE_MAP.md](PRODUCTION_MODULE_MAP.md).

## IV. Entrypoints and control paths

The meaningful entry paths are:

- core CLI and Windows launcher;
- module dispatch;
- campaign run/resume/stop;
- direct ResearchOrchestrator API;
- formalization lane;
- benchmark/demo/observatory auxiliary paths;
- runtime check/reconcile;
- tests and retained adversarial runners.

Normal semantic routing, governance effects, root/truth mutation, and recovery/resume are separate flows. The highest-risk boundary is whether every public semantic caller uses the correct complete binding validator before provider output can become a runtime result/effect.

See [ENTRYPOINTS_AND_CONTROL_FLOW.md](ENTRYPOINTS_AND_CONTROL_FLOW.md).

## V. Authority model

The authority chain is:

provider response -> strict typed response -> current execution binding -> durable job/attempt/result + artifact/lease checks -> accepted result -> EffectSlot/domain validator -> SessionClosure/ResearchMap/AuditGate -> Phase 7 artifacts -> TruthMutationIntent/current snapshot CAS -> TruthMutationReceipt -> PromotionClosure.

Important non-authorities:

- raw provider prose;
- candidate Markdown alone;
- ResearchMap strategy/frontier alone;
- routing strategy state;
- observatory presentation;
- a test result or handoff label;
- an in-memory governed-reframe capability after process death.

See [STATE_AND_AUTHORITY_MODEL.md](STATE_AND_AUTHORITY_MODEL.md).

## VI. Persistence model

JSON stores and content-addressed files coexist with SQLite/WAL control state. Runtime artifacts are body-first and registered afterward. Runtime reconciliation handles stale leases, unknown execution, orphan manifests, missing artifacts, and pending acceptance. Phase 7 loaders verify hashes/linkage.

Material recovery limits are:

- ProjectStore/CampaignStore/governance projections use JSON replacement/in-process synchronization, not a demonstrated cross-process transaction;
- live providers/futures/processes and the trusted reframe capability are reconstructed or discarded;
- truth promotion spans theorem JSON, SQLite effect state, ClaimSnapshot files, receipts, and Phase 7 closure files.

See [PERSISTENCE_AND_RECOVERY.md](PERSISTENCE_AND_RECOVERY.md).

## VII. Phase 7, root, and promotion

Phase7Store.synthesize_root requires compatible current identity, exact passed gate/hash, current ResearchMap/root/session, closed obligations, validated retained evidence, candidate and audit refs, and no open frontier. consolidate requires unchanged candidate bytes and a passed re-audit. Truth promotion is then a snapshot-bound TruthMutationIntent/CAS/receipt path followed by PromotionClosure.

No Phase 7 or truth-promotion function was executed during this audit.

See [PHASE7_ROOT_PROMOTION_MAP.md](PHASE7_ROOT_PROMOTION_MAP.md).

## VIII. Tests

Existing coverage is strongest around modeled single-process behaviors: stale/late runtime results, governance authorization, cross-plane bindings, truth identity/mutation, map/session closure, Phase 7 tamper/recovery, typed sidecars, and Windows interruption. Most tests use temporary JSON/SQLite stores and mock providers.

Known gaps are public-caller completeness, cross-process/file-power-loss recovery, explicit coverage accounting, mathematical verifier independence receipts, hosted/POSIX interrupt evidence, and the exact external specification.

See [TEST_SURFACE_MAP.md](TEST_SURFACE_MAP.md).

## IX. Historical findings

The final pre-root reauthorization at the earlier head 3e2b6f4 denied authorization because F-007 remained open through NF-003/NF-004. Commit 37826ff later added candidate repairs and public regressions. The final handoff records candidate PASS/REPAIRED/CLOSED labels but keeps formal certification flags NO. This audit records all five requested findings as NOT_YET_REAUDITED.

See [HISTORICAL_FINDING_RECONCILIATION.md](HISTORICAL_FINDING_RECONCILIATION.md).

## X. Specification availability

No authoritative Harness_v3_2_合并架构与冻结规范.md was found in the canonical repository/workspace. Candidate architecture decisions, matrices, migration reports, tests, and final handoff are classified separately and are not promoted to normative status.

See [SPEC_TO_CODE_TRACEABILITY_PREP.md](SPEC_TO_CODE_TRACEABILITY_PREP.md).

## XI. Preliminary risks

Highest-ranked implementation questions:

1. complete binding validator order and caller coverage;
2. audit identity overwrite and absence of an explicit coverage ledger;
3. multi-store truth-promotion atomicity;
4. phase/status/projection drift;
5. exact RootSynthesis coverage/manifest completeness;
6. mathematical verifier independence receipt;
7. reconstruction/in-memory authority and canonical-source availability;
8. self-certifying evidence provenance;
9. POSIX/process behavior;
10. late-result consumer-wide terminality.

See [PRELIMINARY_PROBLEM_LOCALIZATION.md](PRELIMINARY_PROBLEM_LOCALIZATION.md). Repository consolidation proposals are intentionally separate and non-mutating in [REPOSITORY_CLEANUP_PROPOSAL.md](REPOSITORY_CLEANUP_PROPOSAL.md).

## XII. Recommended independent audit order

1. Freeze the audit runner to the tagged baseline and supply/hash the missing authoritative spec.
2. Attack public semantic binding paths: core run, campaign run/resume, formalization, runtime reconcile, map/governance effects, and no-backend routing. Cover complete, partial, stale, root-only, map-only, and cross-session variants.
3. Attack audit identity and coverage: mutate/omit returned snapshot identities, obligations, evidence roles, provider/model provenance, and final verifier independence.
4. Inject crashes and concurrent processes through runtime/artifact/effect/theorem/receipt/Phase 7 boundaries; compare every persisted projection.
5. Attack Phase 7 root and final manifest completeness without promoting truth; verify no unresolved scope is hidden by a closed frontier.
6. Attack governance authorization and cross-map/cross-thesis replay after restart.
7. Run the exact F-002/F-005/F-007/NF-003/NF-004 historical/repaired reproducers on this head.
8. Run hosted Linux/POSIX and Windows process evidence at the same head.
9. Only after the above, perform any separately authorized root/promotion audit.

The order prioritizes authority and truth-corruption risk over the easiest or most numerous tests.
