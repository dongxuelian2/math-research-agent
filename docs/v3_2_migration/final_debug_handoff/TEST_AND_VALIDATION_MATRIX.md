# Test and Validation Matrix

| Check | Result | Evidence / boundary |
|---|---|---|
| Phase 7 focused tests | PASS | `4 passed` in `test_phase7_implementation.py` |
| R19 production research-plane integration | PASS | Existing R19 test passed with Phase 7 artifacts enabled |
| Full local suite | PASS | `287 passed, 1 warning`; historical baseline was 283 passed, so four tests were added |
| Ruff lint | PASS | `uv run --project openprover --extra dev ruff check openprover` passed |
| Ruff format for new Phase 7 files | PASS | `phase7.py` and `test_phase7_implementation.py` are formatter-compliant |
| Ruff global format gate | KNOWN PRE-EXISTING FAILURE / DEFERRED | `11 files would be reformatted, 133 files already formatted`; no global cleanup was attempted |
| Python compilation | PASS | `uv run --project openprover python -m compileall -q openprover` passed |
| `uv lock --check` | PASS | `uv lock --check --project openprover` passed; 49 packages resolved |
| Windows durable restart recovery | PASS | Focused test resumes a completed run and separately recovers from `TRUTH_PROMOTED`; no certification claim |
| Hosted CI | DEFERRED | Explicitly outside this owner-override implementation turn |
| POSIX interruption/certification | DEFERRED | Explicitly outside this owner-override implementation turn |
| Final adversarial reauthorization audit | NOT RUN | Phase 7 must stop before re-audit/certification |
| Push | NOT RUN | Local commits only; owner/next engineer decides whether to push |

The single pytest warning is the existing Windows permission warning for the
repository `.pytest_cache` projection. It did not affect test outcomes.

NF-003 and NF-004 are intentionally not green requirements in this matrix;
their known failing probes remain in the handoff for the next debug campaign.
