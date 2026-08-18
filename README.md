# Math Research Agent

Gemini-native mathematical research infrastructure with strict typed audits,
durable failed-route repair, and a live Research Observatory.

The product path is intentionally small:

```text
Gemini planner / workers
        ↓
typed JSON schema validation
        ↓
candidate + dependency DAG
        ↓
counterexample / boundary / dependency audits
        ↓
Audit Gate
   ┌────┴────┐
FAIL       PASS
  ↓          ↓
FAILED_ROUTE  PROVED
  ↓
repair successor → re-audit
```

## One-command start

Run the commands below from a Bash shell.

```bash
git clone <repository-url>
cd math-research-agent
bash scripts/bootstrap.sh
```

The script uses `uv`, creates the project environment, generates the showcase
artifacts, and starts the Observatory at <http://127.0.0.1:8765>. The local
showcase is zero-cost and does not require credentials.

For live Gemini runs, export the key before starting:

```bash
export GEMINI_API_KEY="…"
bash scripts/bootstrap.sh
```

Vertex Gemini is also supported with `GOOGLE_CLOUD_PROJECT` and an access token
or local Application Default Credentials.

## The showcase

The old odd-sum example remains useful as a unit-test fixture, but it is not
the product demo. The Observatory showcase uses this bounded claim:

> For every integer `n` with `0 <= n <= 39`, `n² + n + 41` is prime.

The first candidate silently upgrades the finite claim to “for every `n >= 0`”.
The Counterexample Hunter finds `n = 41 → 41²`; the Dependency Auditor also
finds the unproved universal lemma. The system stores `FAILED_ROUTE`, launches
a repair successor, restores the boundary, attaches a finite certificate, and
re-audits the repaired candidate to `PROVED`.

Open the UI and watch the graph, audit gate, failure map, provenance hash,
formalization lane, agent routing, and usage counters update from durable
artifacts.

To generate the replay without starting the server:

```bash
cd openprover
uv run python -m openprover.math_research demo \
  --project ../projects/observatory-demo
```

To serve an existing project:

```bash
cd openprover
uv run python -m openprover.math_research observatory \
  --project ../projects/observatory-demo
```

To opt a completed candidate into the compiler-backed formalization lane,
point it at a Lean project. This writes formalization/formal_status.json and
never changes the natural-language theorem status by itself.

    export LEAN_PROJECT_DIR="/path/to/lean-project"
    uv run python -m openprover.math_research formalize --project ../projects/observatory-demo --target bounded-euler-polynomial --config ../configs/models.gemini.example.json --run ../projects/observatory-demo/runs/<run-id>

## Gemini routing

`configs/models.gemini.example.json` is the only recommended live
configuration. It deliberately routes roles by task:

- strategic planning and final review use Gemini Pro;
- constructive research and counterexample search use Gemini Pro;
- high-volume verification and literature discovery use Gemini Flash;
- formalization has a dedicated Gemini tool lane;
- the independent auditor uses a fresh Gemini context and a separate call
  lineage.

The point is independent context and task specialization, not merely a larger
agent count. Every active role has an explicit route in the configuration.

## Trust boundary

Auditor responses must be complete JSON documents validated against
`AuditResultSchema`. Providers receive `response_schema=AuditResultSchema`;
the result is parsed into a typed object before it reaches the gate. There is
no substring extraction, Markdown fence recovery, or prose-based state
transition.

Worker control events use `WorkerEventSchema`. `NO_PROGRESS` is an enum value,
not text guessed from a transcript. Invalid or missing sidecars cannot create
progress, failure, or literature events.

The upstream proving engine is accessed through a public `research_policy`
hook. The research layer no longer subclasses or reaches into its private
control loop. Candidate search, audit coordination, pipeline primitives, and
the Web UI are separate modules.

## Tests and benchmarks

```bash
cd openprover
uv sync --extra dev
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
```

The benchmark directory is reserved for measured runs. The repository does
not publish invented accuracy numbers: run the benchmark manifest with a
configured Gemini key and archive the raw typed results before comparing
systems.

```bash
export GEMINI_API_KEY="…"
BENCHMARK_OUTPUT="$PWD/benchmark-results/$(date +%Y%m%d-%H%M%S)" \
  bash scripts/run_benchmark.sh
```

This executes the 31-case manifest as fresh projects and writes
`results.jsonl` plus `summary.json`. A failed or interrupted case is recorded
as observed evidence; it is never converted into a success by the runner.

## Layout

- `openprover/openprover/math_research/gemini_provider.py` — native Gemini and Vertex Gemini transport.
- `openprover/openprover/math_research/gemini_tools.py` — Gemini function declarations and Lean bridge.
- `openprover/openprover/math_research/formalization.py` — explicit compiler-backed formalization lane.
- `openprover/openprover/math_research/schemas.py` — strict provider/state contracts.
- `openprover/openprover/math_research/candidate_engine.py` — candidate execution boundary.
- `openprover/openprover/math_research/audit_coordinator.py` — independent audit orchestration.
- `openprover/openprover/math_research/pipeline_primitives.py` — small DAG/runtime primitives.
- `openprover/openprover/math_research/showcase_demo.py` — deterministic hidden-defect replay.
- `openprover/openprover/math_research/observatory.py` — dependency-free Web UI and API.
- `scripts/bootstrap.sh` — Bash + `uv` one-command initialization.

## License

MIT. The proving core retains its upstream attribution in
`THIRD_PARTY_NOTICES.md` and `openprover/LICENSE`.
