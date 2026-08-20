# Local × Remote Capability Reconciliation

## Forensic baseline

- Repository: `dongxuelian2/math-research-agent`
- Local view A: `d224491bc75c7499c8670f810e2831511cdf801c` (`E:\tool\math`, clean `main`)
- Remote view B: `deefce7eed20e8eaf8b96ff7ad63eadc8c7bcb6c` (`origin/main`, fetched 2026-08-20)
- Merge base C: `d224491bc75c7499c8670f810e2831511cdf801c`
- Relation: A = C; B is four commits ahead. There is no local-only commit or uncommitted file to merge, but B deletes capabilities present and tested in A.
- Integration branch: `codex/v3-2-reconciliation`, based on B. No push was performed.
- Diff C→B: 151 files, +10,246/-22,881.

The labels below compare A and B before this branch's repairs. “Validated” means a production-path regression, provider contract test, or executable bootstrap existed—not merely a demo.

## Capability preservation matrix

| Capability | A (local/base) | B (remote) | Classification | Evidence / disposition |
|---|---|---|---|---|
| Planner core | Mature OpenProver loop | Core retained, large implementation edit | BOTH_DIFFERENT | Preserve B hook seam and the Planner algorithm; do not rewrite. |
| Parallel Workers | Present and tested | Present | BOTH_DIFFERENT | Threaded spawn remains production path. |
| Worker Verifier | Present and tested | Present; typed-event consumer disconnected | BOTH_DIFFERENT | Kernel retained; sidecar wiring repaired on this branch. |
| worker role scheduling | No dedicated public scheduler | `RoleScheduler` and assignment artifact | REMOTE_ONLY | KEEP. |
| heterogeneous routing | Broad provider/model configs | Typed role/tier router, narrower providers | MERGE_REQUIRED | Preserve B control plane; restore A provider adapters. |
| routing escalation | Earlier heterogeneous/fallback logic | Failure counters, disagreement and tier escalation | BOTH_DIFFERENT | SALVAGE_PRIMITIVE; strategy ownership must later leave Router. |
| OpenAI provider | Implementation + contract tests | Deleted | REGRESSED_REMOTE | Restore behind common Provider interface; do not replace Gemini. |
| Codex provider | CLI implementation + contract tests | Deleted | REGRESSED_REMOTE | Restore as compatibility adapter. |
| Gemini provider | Absent | Gemini structured output/tool loop | REMOTE_ONLY | KEEP. |
| Vertex Gemini | Absent | Present | REMOTE_ONLY | KEEP. |
| Claude/Mistral/GLM/OpenRouter/local | Implementations/configs/smokes | Deleted | REGRESSED_REMOTE | Preserve as optional legacy adapters only after contract revalidation. |
| mock provider | Deterministic production fixture | Reworked typed mock | BOTH_DIFFERENT | KEEP B; recover capability-level E2E. |
| candidate generation | Monolithic orchestrator path | `CandidateEngine` over OpenProver | BOTH_DIFFERENT | KEEP B adapter boundary. |
| specialist audits | Present | `AuditCoordinator` refactor | BOTH_DIFFERENT | KEEP B and production E2E. |
| final audit / hard gate | Present and tested | Present, strict typed schema | BOTH_DIFFERENT | KEEP B; fail closed. |
| secondary verification | Present with dedicated regression | Implementation retained, old regression deleted | REGRESSED_REMOTE | Restore capability-level regression and add independence receipt later. |
| `FAILED_ROUTE` | Durable project memory | Retained plus typed worker event | BOTH_DIFFERENT | Preserve; later migrate long-horizon ownership to `RouteFailureRecord`. |
| strategy memory | `StrategyFingerprint` | Still `StrategyFingerprint` | BOTH_EQUIVALENT | Compatibility only; v3.2 replacement is required. |
| campaign lifecycle | v2 candidate/repair campaign | Refactored v2 campaign | BOTH_DIFFERENT | Preserve runtime compatibility, migrate research ontology later. |
| repair successor | Present and tested | Present | BOTH_EQUIVALENT | DEPRECATED_WITH_EVIDENCE as research ontology; retain execution lineage temporarily. |
| literature search/retrieval | Broad pipeline and live smokes | Reworked pipeline | BOTH_DIFFERENT | SALVAGE verified search/acquisition primitives. |
| literature authority | Fail-closed trust checks | Mostly preserved and typed | BOTH_DIFFERENT | KEEP B invariants. |
| applicability | Separate applicability records/tests | Preserved | BOTH_DIFFERENT | KEEP B invariants. |
| canonical artifact authority | Partial manifest/replay logic | Literature artifact hashes improved; project proof/replay body authority unresolved | MERGE_REQUIRED | P0 remains: body + SHA-256 + authority provenance or dependent branch blocks. |
| checkpoint/resume | Broad v2 resume regressions | Current schema strict; legacy resume coverage deleted | REGRESSED_REMOTE | Add explicit migration classification; never silently upgrade/delete. |
| budget | Conservative budget logic | Retained/modified | BOTH_DIFFERENT | SALVAGE atomic reservation semantics. |
| provider quota handling | Multiple provider behavior | Gemini-specific retry/usage plus mock | MERGE_REQUIRED | Preserve resumable resource failure taxonomy. |
| Windows bootstrap | `bootstrap.ps1` + launcher | Deleted | REGRESSED_REMOTE | Restored using shared uv/Python core on this branch. |
| Bash bootstrap | Absent | `bootstrap.sh` | REMOTE_ONLY | KEEP. |
| CLI | Broad provider/campaign CLI | Slimmed, Gemini/demo additions | MERGE_REQUIRED | Preserve current CLI and PowerShell wrapper; restore provider-agnostic smoke later. |
| GitHub CI | Windows path | Ubuntu/uv fixed in `4c66098` | BOTH_DIFFERENT | Old audit's broken-CI finding is stale; Windows job added on this branch. |
| Lean tools | Broad scripts/tools | Core retained and formalization tool loop added; scripts removed | MERGE_REQUIRED | KEEP formalization; revalidate deleted benchmark helpers before any restoration. |
| Formalization lane | Absent | Gemini→Lean tool-backed lane | REMOTE_ONLY | KEEP. |
| Observatory | Absent | Durable artifact UI | REMOTE_ONLY | KEEP; never treat it as E2E evidence. |
| benchmark | Older scripts | Gemini Observatory benchmark | BOTH_DIFFERENT | MERGE_REQUIRED at capability level. |
| showcase | No equivalent | Deterministic replay demo | REMOTE_ONLY | KEEP as demo, label non-E2E. |
| production E2E | Mock lifecycle, provider, repair and UTF-8 tests | Several deleted; showcase added | REGRESSED_REMOTE | Real deterministic Planner→3 Workers→Verifier→Audits→Gate regression restored on this branch. |
| regression tests | Broad provider/Windows/resume suite | Strong Gemini/schema/literature tests, many deletions | MERGE_REQUIRED | Union by invariant, not by blindly restoring filenames. |

## Deleted code is not deletion authority

The following A files carried validated capabilities and therefore cannot be considered obsolete merely because B removed them: `openai_provider.py`, `codex_cli_provider.py`, provider configs, Windows scripts, `test_mock_pipeline.py`, `test_secondary_verification.py`, provider tests, checkpoint/heterogeneous tests, pilot readiness tests, and UTF-8 tests. Restoration must be adapter-based and pass current typed contracts.

## Phase-1 reconciliation result

- Worker and Verifier raw bodies now produce strict validated event sidecars through public lifecycle hooks.
- Missing/invalid event footer or missing sidecar becomes `ERROR`; no default `COMPLETED` remains.
- `NO_PROGRESS`, `FAILED_ROUTE`, disagreement, literature request, high-value, and progress signals have deterministic policy tests.
- Silent task truncation is replaced by `PLAN_OVER_CAPACITY` and explicit replan.
- Windows bootstrap and launcher are restored using the same uv/Python implementation path as CI.
- Current remote Ubuntu/uv CI is preserved and a Windows bootstrap/launcher job is added.
- A real deterministic production-path E2E (not showcase replay) covers Planner→3 Workers→Worker Verifier→Candidate→specialist Auditors→Final Gate→PROVED in an isolated fixture.

No provider, checkpoint, canonical-authority, Truth Plane, Research Plane, or durable Execution Plane migration is claimed complete by this document.
