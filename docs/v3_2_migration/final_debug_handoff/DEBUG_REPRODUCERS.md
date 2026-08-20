# Debug Reproducers

Run commands from the repository root. Use a clean local environment and do
not edit the expected NF-003/NF-004 outcomes.

## Existing final adversarial probe

The next debug/certification engineer should run:

```powershell
uv run --project openprover --extra test python docs/v3_2_migration/pre_root_final_reauthorization/run_final_adversarial_probes.py
```

The preserved expected outcome is:

```text
F005-A1-A14 PASS
F007-RESTART-CONTROLS PASS
NF-003-PARTIAL-BINDING FAIL
NF-004-NO-BACKEND-GUARD FAIL
F002-TERMINAL-REJECTION PASS
```

This probe was not rerun as a Phase 7 certification action. Its known failing
NF-003/NF-004 expectations must remain visible to the next engineer.

## Phase 7 focused coverage

```powershell
uv run --project openprover pytest -q openprover/tests/math_research/test_phase7_implementation.py
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
