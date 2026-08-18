# Package map

The product architecture is documented in
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

The important boundaries are:

- `openprover/math_research/schemas.py` — strict provider/state contracts.
- `openprover/math_research/gemini_provider.py` — Gemini Developer API and
  Vertex Gemini transport.
- `openprover/math_research/candidate_engine.py` — candidate execution.
- `openprover/math_research/audit_coordinator.py` — independent audits and the
  final gate.
- `openprover/math_research/pipelines.py` — asynchronous DAG scheduling.
- `openprover/math_research/openprover_adapter.py` — the public engine policy
  boundary.
- `openprover/math_research/observatory.py` — the local Web UI and JSON API.

All state-changing provider calls use a Pydantic response schema. The state
machine consumes validated fields and enum values; it does not inspect model
prose.
