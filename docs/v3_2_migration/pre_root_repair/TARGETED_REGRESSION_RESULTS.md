# Targeted Regression Results

All commands were run from the repository root on Windows.

| Check | Result |
|---|---|
| `test_pre_root_authority_repairs.py` | `8 passed` |
| `test_durable_runtime.py` | `20 passed` |
| governance/research targeted slice | `17 passed` |
| cross-plane/recovery targeted slice | `68 passed` |
| X1 restart witness (`RX-RESTART-STALE-DOMAIN`) | `PASS`; `accepted_result_id_after_restart=null`, `effect_slots=0` |
| X7 restart/standalone slice | `4 passed` |
| `RX-COMPOUND-STALE` | `PASS`; `STALE_FENCED`, no accepted result/effect slot |
| `FAULT-AFTER-PROVIDER-RESULT` | `PASS`; `UNKNOWN_EXECUTION`, `DEAD_LETTER`, manual review |
| stale closure / explicit transfer / EffectSlot slice | `3 passed` |
| Windows interrupt race | `3 passed` |
| full suite | `283 passed, 1 warning` |

The one warning is the pre-existing pytest cache permission warning under the
workspace `.pytest_cache` path.

## Static and repository checks

```text
ruff                         PASS
compileall                   PASS
uv lock --check              PASS
git diff --check             PASS
bash -n                      PASS
PowerShell parser            PASS
```

The old audit evidence remains historical. New repair tests are in
`openprover/tests/math_research/test_pre_root_authority_repairs.py`; the
runtime recovery helper expectations were updated to pass an explicit trusted
validator rather than relying on permissive validator omission.
