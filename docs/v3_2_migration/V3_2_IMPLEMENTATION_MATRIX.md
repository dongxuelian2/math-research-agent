# Harness v3.2 Implementation Matrix

| Capability | Spec requirement | Before | After | Status | Production path | Tests | Migration | Remaining gap |
|---|---|---|---|---|---|---|---|---|
| OpenProver Tactical Kernel | Preserve Planner→Workers→Verifier | Present | Preserved | PRESERVED | `CandidateEngine→Prover` | production E2E | none | Full `StepOutcome/step()` seam not started |
| Typed Worker events | Validated event before state consumer; missing fails closed | Consumer expected absent sidecars; missing→COMPLETED | Strict footer→hook→sidecar→policy; missing/invalid→ERROR | IMPLEMENTED | Worker/Verifier batch hooks | 5 focused tests + E2E | old raw artifacts remain readable, never inferred trusted | Provider-native structured envelope may be added later |
| Worker/Verifier disagreement | Typed escalation | Sidecars absent | Typed disagreement reaches Router | IMPLEMENTED | `ResearchPolicy._record_worker_events` | focused policy test | none | independence receipt not implemented |
| Planner capacity | No silent task loss | `tasks[:max_workers]` | `PLAN_OVER_CAPACITY` replan | IMPLEMENTED | `Prover._handle_spawn` | regression suite | none | durable ResearchObligation frontier not implemented |
| Windows runtime | Supported | Deleted remotely | uv-based bootstrap + launcher | IMPLEMENTED | PowerShell→uv→same Python module | local bootstrap/status + CI job | replaces old root-venv assumption | full Windows CI result awaits GitHub run |
| Bash runtime | Supported | Present remotely | Preserved | PRESERVED | `scripts/bootstrap.sh` | CI definition | none | local Bash not executed on Windows host |
| GitHub CI | Valid current paths | Ubuntu/uv already fixed at remote HEAD | Preserved + Windows job | IMPLEMENTED | `.github/workflows/ci.yml` | local command parity | none | hosted CI not run locally |
| Production Agent E2E | Planner→≥3 Workers→Verifier→Candidate→Audits→Gate | Removed capability-level regression | Restored deterministic real route | IMPLEMENTED | `ResearchOrchestrator` | E2E PASS | isolated fixture | live paid-provider E2E intentionally not run |
| Gemini/Vertex | Preserve | Present | Preserved | PRESERVED | common routing/provider factory | existing tests | none | live credentials not used |
| Codex/OpenAI | Preserve validated adapters | Deleted remotely | Not restored in this phase | REGRESSED | none on B | old tests only in A | adapter restoration required | next safe frontier |
| Legacy checkpoint classification | DIRECT_IMPORT/REVALIDATION_REQUIRED/ARCHIVE_ONLY/INCOMPATIBLE | Strict rejection/new run | unchanged | NOT_STARTED | v2 file checkpoints | existing current-schema tests | design documented | phase 2 |
| Canonical project artifact authority | body+hash+provenance or scoped block | partial/no proof-replay resolver | unchanged | NOT_STARTED | no complete production path | no complete E2E | conservative re-resolution required | P0 phase 2 |
| Truth facade / ClaimSnapshot | immutable root identity | absent | absent | NOT_STARTED | ProjectStore direct | none | v2 knowledge only | phase 3 |
| ResearchMap / ResearchObligation | durable long-horizon state | absent | absent | NOT_STARTED | v2 campaign/task ontology | none | do not migrate runtime ontology | phase 4+ |
| Architecture Review / Structural Probe | mandatory review clock and bounded probe | absent | absent | NOT_STARTED | none | none | new v3 objects | phase 5 |
| RuntimeBackend / SQLite / outbox / attempts | deterministic recoverable seam | absent | absent | NOT_STARTED | JSON/filesystem runtime | v2 async tests only | salvage primitives | phase 6 |
| ModelRouter strategy ownership | Router selects compute only | Router owns failures/stall/escalation | unchanged | DEVIATED | `routing.py` | current routing tests | compatibility adapter required | migrate after Research Plane exists |
| Campaign/session ownership | CandidateAttempt not research ontology | successor repair world | unchanged | DEVIATED | `campaign.py` | v2 lifecycle tests partially removed | retain execution lineage only | phase 7 |
| Root synthesis/final consolidation | exact manifest; immutable promoted proof | absent | absent | NOT_STARTED | final audit over candidate | audit tests | new v3 path | phase 8 |
