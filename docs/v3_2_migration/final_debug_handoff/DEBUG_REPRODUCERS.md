# Debug Reproducers

Run commands from the repository root. The commands use temporary project
roots and do not modify frozen audit records.

## Existing final adversarial probe

The next debug/certification engineer should run:

```powershell
uv run --project openprover --extra test python docs/v3_2_migration/pre_root_final_reauthorization/run_final_adversarial_probes.py
```

The final candidate outcome is:

```text
F005-A1-A14 PASS
F007-RESTART-CONTROLS PASS
NF-003-PARTIAL-BINDING PASS
NF-004-NO-BACKEND-GUARD PASS
F002-TERMINAL-REJECTION PASS
```

The final run also reports `F005-A1-A14 PASS` and
`F007-RESTART-CONTROLS PASS`. These are candidate repair results, not formal
certification.

## X1-X16 repair runner

```powershell
uv run --project openprover --extra test python docs/v3_2_migration/pre_root_repair/run_pre_root_repair_probes.py
```

The final run reports X1 and X7 as `CERTIFIED` in the runner's local evidence
taxonomy, with no blocking probe failures. The candidate report deliberately
does not convert that runner label into an independent certification claim.

## Phase 7 focused coverage

```powershell
uv run --project openprover --extra test pytest -q openprover/tests/math_research/test_pre_root_authority_repairs.py openprover/tests/math_research/test_pre_root_blocker_repairs.py openprover/tests/math_research/test_phase7_implementation.py
```

The focused tests exercise:

- normal synthesis, consolidation, TruthMutation, and PromotionClosure;
- completed-run reload verification;
- recovery from a durable `TRUTH_PROMOTED` checkpoint;
- stale root, open frontier, and failed gate rejection;
- tampered final-proof rejection.

## Relevant integration coverage

```powershell
uv run --project openprover pytest -q openprover/tests/math_research/test_phase4_research_plane_e2e.py::test_r19_production_research_plane_and_separate_truth_mutation_e2e
```

## Full local regression

```powershell
uv run --project openprover pytest -q
```

For the final deterministic local count, the executed command was:

```powershell
uv run --project openprover --extra test pytest -q -p no:cacheprovider
```

It returned `289 passed`.

## Static and platform checks

```powershell
uv run --project openprover --extra dev ruff check openprover
uv run --project openprover --extra dev ruff format --check openprover
uv lock --check --project openprover
uv run --project openprover python -m compileall -q openprover/openprover docs/v3_2_migration/pre_root_final_reauthorization
uv run --project openprover --extra test pytest -q -p no:cacheprovider openprover/tests/test_interrupt_race.py
```

The Windows interrupt check returned `3 passed`. Bash and PowerShell syntax
scans also passed. A real POSIX process-group run remains unavailable on this
host because WSL and Docker are not installed.

## Artifact inspection

After a successful demo run, inspect the project-local records under:

```text
<project>/phase7/root_synthesis/
<project>/phase7/root_synthesis_bodies/
<project>/phase7/final_consolidations/
<project>/phase7/final_proofs/
<project>/phase7/consolidation_reaudits/
<project>/phase7/promotion_closures/
<project>/runs/<run-id>/state.json
```

Verify that the final theorem `proof_file` points to the immutable Phase 7
final proof, not only to the resolution report.
