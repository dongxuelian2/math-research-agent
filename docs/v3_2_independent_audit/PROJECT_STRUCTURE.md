# Project Structure

## Reading the tree

This is a human-useful tree of the tracked baseline. It intentionally collapses repeated files; the exhaustive baseline file list is [REPOSITORY_FILE_INDEX.tsv](REPOSITORY_FILE_INDEX.tsv).

~~~text
.
├── .env.example                         environment-variable template
├── .github/
│   └── workflows/ci.yml                 Ubuntu + Windows CI contract
├── .gitignore                           generated/runtime exclusions
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── README.md                            repository/product overview
├── pytest.ini                            test configuration
├── run_math_agent.ps1                   Windows command dispatcher
├── benchmarks/
│   └── gemini-observatory-v1.json       benchmark case manifest
├── configs/
│   ├── models.codex.example.json        provider/model example
│   ├── models.gemini.example.json
│   ├── models.mock.json
│   └── models.openai.example.json
├── docs/
│   ├── ADR-0001-project-layer.md
│   ├── ARCHITECTURE.md
│   ├── reconciliation/                  retained local/remote capability report
│   └── v3_2_migration/                  historical migration, audit, repair, and handoff corpus
├── openprover/
│   ├── pyproject.toml                    package metadata and scripts
│   ├── uv.lock                           locked dependencies
│   ├── openprover/
│   │   ├── prover.py                     planner/worker proof engine
│   │   ├── prompts.py                    core prompt material
│   │   ├── tui/                          interactive/headless UI support
│   │   └── math_research/                active project/runtime layer
│   └── tests/
│       ├── math_research/                38 domain tests
│       └── test_*.py                     package/launcher/interrupt tests
├── projects/
│   └── demo/                             tracked sample project state
│       ├── project.json
│       ├── index.json
│       ├── theorems/
│       ├── sources/
│       ├── steering/
│       └── failed_routes.json
└── scripts/
    ├── bootstrap.ps1
    ├── bootstrap.sh
    └── run_benchmark.sh
~~~

## Top-level ownership and authority classification

| Subtree | Purpose | Ownership | Runtime or documentation | Active or historical | Authoritative or derived | Likely entry points |
|---|---|---|---|---|---|---|
| .github/ | CI checks | repository automation | runtime/tooling | active | operational policy, not theorem authority | workflow runner |
| benchmarks/ | measured case manifest | benchmark lane | data | active | input/derived outcomes, not truth authority | benchmark |
| configs/ | provider/model role examples | provider configuration | configuration | active examples | routing input, not semantic authority | load_model_config |
| docs/ | architecture and migration record | maintainers/auditors | documentation | mixed | historical/candidate unless separately authoritative | none |
| openprover/openprover/ | package and research runtime | production code | runtime | active | code authority is constrained by stores/gates | console scripts, module API |
| openprover/tests/ | behavioral/regression evidence | test suite | test | active | evidence only | pytest |
| projects/demo/ | sample JSON project | demo fixture | data | active fixture | project data; not repository architecture authority | demo, CLI |
| scripts/ | bootstrap/benchmark helpers | repository tooling | tooling | active | operational | shell/PowerShell |
| root metadata | license, ignore, launcher, README | repository | metadata/tooling | active | administrative | launcher/CI |

## Special subtree: openprover/openprover/math_research/

This is the active project-layer and research-harness implementation, not a documentation mirror. It contains 63 tracked Python modules. The material responsibilities are:

- project JSON truth and lifecycle: project.py, state_machine.py, truth_identity.py, claim_snapshot.py, truth_store.py, truth_mutation.py;
- research frontier and tactical plane: research_map.py, research_obligation.py, research_store.py, research_evidence.py, directive.py, campaign.py, route_failure.py;
- provider/routing and typed response boundary: providers.py, provider-specific modules, routing.py, schemas.py, audit_protocol.py, openprover_adapter.py;
- durable execution plane: runtime_model.py, runtime_backend.py, runtime_bindings.py, runtime_dispatch.py, runtime_effects.py, runtime_artifacts.py, runtime_reconciler.py;
- orchestration and proof/audit pipeline: orchestrator.py, candidate_engine.py, audit_coordinator.py, pipelines.py, scheduler.py, pipeline_primitives.py;
- architecture governance: governance.py, architecture_review.py, structural_probe.py, architecture_patch.py, architecture_critic.py, structural_effect.py;
- Phase 7 and auxiliary lanes: phase7.py, formalization.py, certification.py, benchmark.py, observatory.py, showcase_demo.py, migration.py, checkpoint_migration.py.

Ownership is distributed deliberately: the orchestrator coordinates; ProjectStore owns theorem lifecycle; TruthStoreFacade owns snapshot/mutation validation; ResearchStoreFacade owns map/obligation projections; SQLite owns runtime execution state; Phase7Store owns root/consolidation/closure artifacts; governance owns review-clock and patch objects. The map is descriptive, not a claim that the boundaries are independently verified.

## Special subtree: openprover/tests/math_research/

These 38 tests are mixed unit, integration, adversarial, and production-wiring tests. Their names cover architecture governance, canonical authority, checkpoint migration, durable runtime, routing, research maps, session closure, truth identity/mutation, Phase 7, worker-event wiring, and historical repair findings. They exercise temporary project directories and mock providers extensively. They are evidence at their seams, not an independent architecture oracle.

## Special subtree: docs/v3_2_migration/

The 87 files are a chronological/hierarchical evidence corpus: phase 3–6 audit reports, pre-root audit and re-audit packages, pre-root repair reports, final reauthorization records, and final debug handoff materials. It contains mutually different evidence classes:

- historical audit observations, including prior open findings;
- implementer repair reports and test/probe results;
- candidate self-certification and final handoff claims;
- explicit denials such as the pre-root reauthorization report;
- Phase 7 implementation and scope ledgers.

The directory is retained for provenance. It must not be treated as the authoritative v3.2 freeze specification merely because it uses words such as CERTIFIED, CLOSED, or READY.

## Runtime data boundary

The source tree contains code and tracked demo inputs. A real project writes mutable runtime state below the project root, including runs/, research/, truth/, runtime/, campaigns/, and phase7/; these are generally excluded from source control by .gitignore. The tracked projects/demo/ files are fixtures, not a live runtime database.
