# Architecture

## Decision summary

系统采用 composition：不重写 OpenProver 的通用 agent loop。新增 `openprover.math_research` 外层，先构造一个受控的 theorem context，再把它交给原始 `Prover` 产生 candidate，最后执行独立审计和项目归档。

```text
project.json + theorem JSON + failed_routes.json + steering
                         |
                         v
                 ContextBuilder
          (dependency slice / allowed PROVED only)
                         |
                         v
     OpenProver Prover (Planner -> parallel Workers -> Verifiers)
     Whiteboard / Repo / steps / resume / archive remain upstream
                         |
                         v
                 CANDIDATE_PROOF
                         |
          +--------------+--------------+--------------+
          |              |              |              |
 Counterexample    Dependency     Exhaustiveness    Boundary
    Hunter          Auditor          Auditor         Auditor
          +--------------+--------------+--------------+
                         |
                         v
                Final Proof Auditor
                   PASS / FAIL
                         |
            PASS only -> Archivist
                         |
             report + graph + PROVED
```

## Preserved OpenProver components

- `openprover.prover.Prover` planner loop and action dispatch.
- Planner-managed `WHITEBOARD.md` and `repo/` wikilink repository.
- `ThreadPoolExecutor` parallel Worker execution controlled by `max_workers`.
- Automatic independent Worker verifier calls.
- `steps/`, `step_history.json`, `meta.toml`, interruption/resume logic.
- Provider clients and raw call archive/cost/timing fields.
- Optional Lean tools remain isolated in `openprover.lean`.

The outer layer does not monkey-patch planner output or treat its proof as final.

## New components

| Module | Responsibility |
|---|---|
| `math_research/project.py` | JSON project/theorem storage, importer, FAILED_ROUTE and steering |
| `math_research/state_machine.py` | hard lifecycle and Archivist-only PROVED transition |
| `math_research/retrieval.py` | recursive dependency slice, blocked-dependency/cycle detection, minimal source excerpts |
| `math_research/providers.py` | config-driven role/provider factory, role aliases and no-cost mock backend |
| `math_research/openai_provider.py` | official OpenAI SDK / Responses API adapter, usage normalization and bounded retry |
| `math_research/codex_cli_provider.py` | isolated `codex exec` adapter using saved CLI login, stdin prompt transport, JSONL/final-output parsing and process-tree control |
| `math_research/orchestrator.py` | checkpoints, OpenProver composition, auditor fan-out, gate, archive |
| `math_research/audit_prompts.py` | independent auditor contracts |
| `math_research/cli.py` | project/import/context/run/status/steer commands |

## Theorem state machine

Primary research path:

```text
OPEN -> IN_RESEARCH -> CANDIDATE_PROOF -> AUDITING -> PROVED
                                      \-> AUDITING -> REJECTED -> IN_RESEARCH
                    \-> PARTIAL -> IN_RESEARCH
```

Additional classification:

- `UNCLASSIFIED`: importer output; cannot be researched until human classification.
- `CONJECTURE`: explicitly unproved proposition; never included as allowed dependency.
- `FROZEN`: no research until explicit human unfreeze/reclassification.
- `FAILED_ROUTE`: a separate structured route record, not a successful theorem state.

`AUDITING -> PROVED` requires actor `Archivist` and every `AuditGate` boolean true. `PROVED -> IN_RESEARCH` is allowed only for an explicit Human re-audit request.

## Audit gate

The gate requires:

- forward implication;
- converse/reconstruction if applicable;
- exhaustive cases;
- complete parameter ranges;
- boundary/degenerate cases;
- only PROVED dependencies and no graph cycle;
- no counterexample;
- all specialist auditors PASS;
- Final Proof Auditor PASS;
- computational evidence separated from proof.

Any missing/false item yields `REJECTED` plus `FAILURE_REPORT.md`. A worker has no project write path that can bypass this gate.

## Dependency graph and retrieval

One JSON file per theorem is deliberately used instead of a database. It stores:

```json
{
  "id": "target-id",
  "title": "...",
  "status": "OPEN",
  "source_file": "sources/target.md",
  "statement": "...",
  "dependencies": ["lemma-a"],
  "downstream_dependents": [],
  "tags": [],
  "branch": "campaign-x",
  "proof_type": "NATURAL_LANGUAGE",
  "claim_type": "iff",
  "audit_status": "NOT_AUDITED",
  "last_updated": "..."
}
```

`ContextBuilder` performs a DFS from the current target, reports cycles, and divides the dependency closure into:

- allowed: status exactly `PROVED`;
- blocked: every other status.

Default context includes target and direct-dependency source content plus transitive metadata. `--expand` includes all transitive source content. Downstream theorems and unrelated documents are not included.

## FAILED_ROUTE memory

Each route records id, strategy, target, obtained progress, exact failure point, why information was insufficient, recovery conditions, related theorem ids and tags. Retrieval includes only routes intersecting the target dependency slice or tags. The planner may reactivate one only when a recovery condition is newly satisfied and the reason is recorded.

## Data and run format

```text
project/
  project.json
  index.json
  failed_routes.json
  theorems/<id>.json
  sources/*.md
  steering/directives.json
  runs/<run-id>/
    state.json
    context/CONTEXT.md
    context/context.json
    openprover/...
    CANDIDATE_PROOF.md
    audits/*.json
    usage.json
    FAILURE_REPORT.md          # fail only
  reports/<resolution>.md      # pass only, Archivist
```

All formats are UTF-8 and human-readable. JSON writes are atomic within a filesystem using a temporary sibling and replace.

## Model roles

Legacy configs may continue to map `planner`, `worker`, `cheap_auditor`, and `final_auditor`. New configs can use `counterexample` for Counterexample Hunter and `auditor` as the fallback for Dependency, Exhaustiveness and Boundary Auditors; an exact specialist role key takes precedence. These are configuration aliases for the existing roles, not new agents.

Model IDs remain in JSON. The OpenAI example assigns `gpt-5.6-sol` to Planner, Worker, Counterexample Hunter, the remaining specialist auditors and Final Proof Auditor, with role-specific reasoning effort and output limits.

Role configs may mix providers. For example, Planner can remain on `openai` while Worker or Auditors use `codex_cli`; factory resolution happens independently per role and neither provider delegates to the other.

## Official OpenAI provider

`provider: openai` uses the official Python SDK and `client.responses.create`, not Chat Completions. It reads only `OPENAI_API_KEY`; direct credentials in config are rejected. A request carries system/developer/user roles as Responses input items, `reasoning.effort`, `max_output_tokens`, `store`, timeout, and optional function tools. Streaming consumes official response events and returns the same `result` / `finish_reason` / `tool_calls` shape expected by OpenProver.

The adapter normalizes API-reported usage into:

```text
input_tokens / output_tokens / reasoning_tokens
cached_tokens / cache_write_tokens / total_tokens / api_reported
```

The API does not include an invoice-cost field in an ordinary response, so the adapter records `cost_api_reported=false` instead of estimating USD as if it were API-reported.

The official SDK's internal retry is disabled. The adapter applies its own configurable, exponential, bounded retry to connection/timeouts, transient 429, 408/409 and server 5xx. Authentication, permissions, invalid request/model and quota/billing failures do not retry. The raised error contains provider, error type, HTTP status, role, model, retry count and a safe explanation. Once bounded retries are exhausted, the outer OpenProver transient detector does not start a second unbounded retry loop.

`run --dry-run` resolves config and dependency context without creating a run, changing theorem state, creating a client or sending a request. `provider-smoke` is intentionally different: it makes exactly one non-retried minimal Responses request and archives a secret-safe result.

## Codex CLI subscription provider

`provider: codex_cli` is a sibling of, not a replacement for, `provider: openai`. It never imports credentials from the OpenAI API provider. Each LLM call starts one official non-interactive process using an argv list (`shell=False`):

```text
codex exec --json --output-last-message <file>
  --ephemeral --ignore-user-config --ignore-rules
  --sandbox read-only --skip-git-repo-check
  --cd <run>/codex/<role>/call-N/attempt-N
  --config approval_policy="never" [--model ...]
  [--config model_reasoning_effort="..."] -
```

The prompt is not an argument. The adapter serializes chat-shaped system/developer/user/assistant/tool history to a versioned UTF-8 JSON envelope and sends it through stdin. When a JSON schema is requested, it writes a per-attempt schema file and uses the documented `--output-schema` switch. JSONL stdout supplies event status, thread metadata and usage; `--output-last-message` supplies the authoritative final assistant text. Invalid JSONL, failed events, or a missing/empty final file fail closed.

The child environment removes `OPENAI_API_KEY`, `CODEX_API_KEY` and `OPENAI_BASE_URL`. Authentication remains wholly inside the official CLI's saved `codex login` state; the adapter neither opens nor parses credential storage. Model is nullable because a ChatGPT workspace's CLI default is not assumed to equal an API model ID. CLI reasoning validation is a separate set (`minimal`, `low`, `medium`, `high`, `xhigh`), and a null value defers to the CLI default.

Successful results normalize `provider`, requested/resolved `model`, requested `reasoning_effort`, `duration_ms`, retry count, status, output mode and secret-safe raw metadata. A documented `turn.completed` usage event becomes input/output/reasoning/cached/total counts with `cli_reported=true`; absent CLI usage remains `null`. Billing is `chatgpt_codex_subscription`, API request count is zero and USD cost is always `null` rather than estimated.

Structured terminal errors distinguish executable detection, login, model, reasoning, timeout, process, rate/usage limits, malformed output, cancellation and unknown runtime failures. Only explicit timeout, rate limit, recognized network or 5xx failures can consume the configured bounded retry budget. Timeout/cancellation terminates the owned process tree. Exhausted retryable failures are marked so the outer OpenProver loop does not create an unbounded second retry path.

`codex_cli` run directories live under the current durable run rather than the repository root:

```text
runs/<run-id>/
  archive/<role>/             # readable OpenProver call archive
  codex/<role>/
    call-001/attempt-01/      # CLI cwd, final message, optional schema
```

Codex dry-run reports the resolved executable, provider/role, requested model/reasoning, planned role work directory, target and context size without starting the CLI or checking/exposing authentication. Codex provider-smoke forces zero retries and accepts success only when exactly one Codex process returns the expected sentinel; it does not create a project store or run the agent pipeline.

## Windows compatibility changes

- On Windows, importing `openprover.tui` falls back to existing `HeadlessTUI` instead of failing on `termios`.
- Claude subprocess process-group creation/termination is OS-specific.
- Lean subprocess setup avoids Unix `preexec_fn/resource` on Windows. The Windows path does not provide the Unix address-space cap.

The full-screen TUI and upstream inspect browser remain Unix-oriented; this is intentionally not a broad TUI rewrite.
