# Provider Preservation Report

## Authority and scope

- Historical adapter source: local commit `d224491bc75c7499c8670f810e2831511cdf801c`.
- Integration base: remote commit `deefce7eed20e8eaf8b96ff7ad63eadc8c7bcb6c`.
- The current Gemini, Vertex Gemini, and mock implementations were retained.
- Codex CLI and OpenAI adapters were restored behind the current provider
  factory and typed response contract; the current factory was not replaced by
  its historical version.
- No live credentialed or paid-provider request was made.

## Capability matrix

| Provider | Structured output | Native tools | Interruptible | Usage accounting | Reasoning effort | Status |
|---|---:|---:|---:|---:|---:|---|
| Gemini | yes | yes | yes | yes | yes | PRESERVED |
| Vertex Gemini | yes | yes | yes | yes | yes | PRESERVED |
| Codex CLI | yes | no | yes | yes | yes | RESTORED |
| OpenAI | yes | yes | yes | yes | yes | RESTORED |
| mock | yes | no | yes | yes | no | PRESERVED |

The router checks declared capabilities. In particular, a route cannot silently
attach native tools to a provider that does not support them. Legacy role names
are accepted through explicit aliases rather than by changing current role
semantics.

## Production integration

- `load_model_config` validates provider-specific requirements and rejects API
  keys in configuration files.
- `build_provider` constructs all five supported providers and reads secrets
  from the environment.
- OpenAI and Codex CLI calls materialize the same Pydantic JSON-schema contract
  used by the current runtime.
- Provider failures from Gemini, Codex CLI, and OpenAI all enter the existing
  resumable campaign-state path.
- Provider smoke diagnostics report provider-neutral request, process, and
  structured-output evidence.

## Regression evidence

- Focused provider, tool-loop, structured-output, and heterogeneous-routing
  suite: `38 passed in 2.02s`.
- Post-restoration local-safe repository suite: `170 passed in 6.04s`.
- `uv lock --check`: passed; 49 packages resolve, including OpenAI 2.54.0.
- `ruff format --check openprover`, `ruff check openprover`, compileall, and
  `git diff --check`: passed.
- The import-time Windows interrupt subprocess remains excluded locally because
  the managed host returns `WinError 1920` during collection. Hosted CI is
  `PENDING_PUSH`, not a pass.

## Preservation conclusion

Gemini, Vertex Gemini, mock, Codex CLI, and OpenAI are all reachable through the
same validated provider-selection path. The restored adapters preserve their
provider-specific execution behavior without taking ownership away from the
current router, orchestrator, archive, or resume layers.
