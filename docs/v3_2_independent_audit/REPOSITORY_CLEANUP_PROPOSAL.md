# Repository Cleanup Proposal

## Scope

Proposal only. No files were moved, deleted, renamed, or rewritten outside this audit directory. The historical corpus remains unchanged so the evidence chain is preserved.

## Candidate cleanup items

| Item | Evidence / location | Classification | Proposed later action | Safety constraint |
|---|---|---|---|---|
| Canonical product architecture docs | docs/ARCHITECTURE.md, docs/ADR-0001-project-layer.md, docs/v3_2_migration/V3_2_ARCHITECTURE_DECISIONS.md | MERGE_DOCS_LATER | keep one current architecture overview and link phase-specific evidence | retain original hashes/commit references and preserve historical snapshots |
| Phase/migration reports | docs/v3_2_migration/PHASE3_* through PHASE6_*, *_REPORT.md | MOVE_TO_ARCHIVE_LATER | archive by phase/date after a current architecture index exists | no deletion; old reports are provenance for prior findings |
| Candidate self-certification handoff | docs/v3_2_migration/final_debug_handoff/FINAL_CANDIDATE_REPORT.md, CURRENT_STATE.md, TEST_AND_VALIDATION_MATRIX.md | KEEP_CANONICAL | keep as explicitly labelled candidate handoff; add a future independent-result link | never use CLOSED/READY labels as normative authority |
| Explicit denial / prior open finding package | docs/v3_2_migration/pre_root_final_reauthorization/* | KEEP_CANONICAL | retain as immutable historical audit package | it records the pre-37826ff head and must not be overwritten |
| Redundant finding matrices | pre_root_audit/FINDINGS.md, pre_root_reaudit/NEW_FINDINGS.md, pre_root_repair/PRE_ROOT_BLOCKER_REPAIR_MATRIX.md, final handoff OPEN_FINDINGS.md | MERGE_DOCS_LATER | create a versioned finding ledger with source head, disposition class, and evidence type | preserve each source matrix and its original terminology |
| Duplicate adversarial runners | pre_root_audit/run_cross_plane_probes.py, pre_root_reaudit/run_pre_root_reaudit_probes.py, pre_root_repair/run_pre_root_repair_probes.py, pre_root_final_reauthorization/run_final_adversarial_probes.py | NEEDS_HUMAN_DECISION | keep named runners while independent audit depends on exact historical reproduction; later consolidate common harness | do not remove a runner used to reproduce a historical finding |
| Debug reproducer index | final_debug_handoff/DEBUG_REPRODUCERS.md and runner-local instructions | KEEP_CANONICAL | keep one entry index that states the exact commit and expected disposition | label observed output versus certification |
| Phase 7 handoff/implementation duplication | final_debug_handoff/PHASE_7_IMPLEMENTATION_REPORT.md, PHASE_7_SCOPE_LEDGER.md, phase7.py-adjacent migration reports | MERGE_DOCS_LATER | separate normative implementation map, scope ledger, and test evidence | no claim that the ledger is the missing freeze spec |
| Generated runtime data risk | project .gitignore excludes runs/, runtime caches, pycache, logs, virtual environments; tracked projects/demo has only fixture JSON/Markdown | KEEP_CANONICAL | retain ignore rules; audit CI for accidental artifacts | never bulk-delete without path-specific review |
| Tracked demo state | projects/demo/project.json, theorems, sources, steering, failed_routes.json | NEEDS_HUMAN_DECISION | decide whether demo should be a fixture package or separate example corpus | demo values are not production truth; preserve if CI depends on them |
| Provider configuration examples | configs/models.*.example.json and .env.example | KEEP_CANONICAL | document example-only status and required secret handling | no semantic config changes during cleanup |
| Old capability reconciliation | docs/reconciliation/LOCAL_REMOTE_CAPABILITY_RECONCILIATION.md | MOVE_TO_ARCHIVE_LATER | move under a dated repository-history section once current tooling is indexed | retain because it explains consolidation history |
| Self-certifying language in documents | words CERTIFIED, CLOSED, READY in migration/handoff docs | MERGE_DOCS_LATER | add machine-readable evidence class/status fields instead of rewriting historical prose | do not retroactively change historical claims |
| Shell/PowerShell helper duplication | run_math_agent.ps1, scripts/bootstrap.ps1, scripts/bootstrap.sh, scripts/run_benchmark.sh | NEEDS_HUMAN_DECISION | consolidate wrappers only after Windows/POSIX behavior is independently tested | launcher behavior is operational surface; no blind merge |
| Benchmark manifest and output conventions | benchmarks/gemini-observatory-v1.json, benchmark.py, scripts/run_benchmark.sh | KEEP_CANONICAL | keep manifest and runner, document outputs as observations | benchmark output must not be mistaken for theorem authority |
| Package UI versus research layer | openprover/openprover/tui and math_research | KEEP_CANONICAL | document the upstream proof/UI layer versus project layer boundary | physical reorganization is out of scope and may alter import paths |
| Test fixtures that mimic production state | temporary JSON/SQLite setup across openprover/tests | NEEDS_HUMAN_DECISION | give fixtures explicit schema/version labels and public-path wrappers | do not change tests merely to fit current implementation |
| Runtime data living under project root | runtime/control.sqlite3 and runtime/artifacts produced by real projects | KEEP_CANONICAL | keep runtime local to project but document export/retention policy | do not add runtime data to source control |

## Suggested sequencing for a future cleanup pass

1. Preserve and hash historical migration/audit packages.
2. Create a source/evidence-class index.
3. Create a single current architecture map that links, rather than copies, historical documents.
4. Consolidate duplicate probe harness utilities only after all named historical reproducers remain runnable.
5. Separate tracked demo fixtures from any generated project output.
6. Revisit launcher/config duplication with Windows and POSIX CI evidence.

No cleanup item is authorized by this pass.
