# Environment and upstream audit

Audit date: 2026-08-08 (Asia/Shanghai)

## Host tools

| Item | Observed state |
|---|---|
| Windows | Registry product `Windows 10 Home China`, display version `25H2`, build `26200.8875`, x64 host |
| PowerShell | 7.6.3 Core |
| Git | 2.46.2+ on Windows |
| GitHub CLI | 2.x+ supported |
| Python | 3.13.x at standard Windows Python install path |
| PATH `python` | launch scripts and bootstrap resolve and use the explicit venv interpreter |
| uv | 0.6.12+ compatible; project bootstrap provides isolated venv/pip installation |
| pip | pip inside `.venv` |
| OpenAI Python SDK | 2.53.0 inside `.venv` |
| Node/npm | Node 22.x+, npm 10.x+ |
| Codex | Bundled Codex app executable resolves under WindowsApps; CLI provider prioritizes npm global CLI or explicit binary |
| WSL | Windows component/command exists, but no Linux distribution is installed |
| Lean/Lake | not installed |
| SageMath | not installed |
| PARI/GP | not installed (`gp` in PowerShell is an unrelated alias) |

Environment variables are checked only for presence, never printed. On 2026-08-08, one minimal real OpenAI Responses request was tested through the provider. The API returned 429 `insufficient_quota`; the provider did not retry and no model output or token usage was returned. The archive was checked and verified free of credentials.

## OpenProver upstream

- Repository: `https://github.com/kripner/OpenProver.git`
- Commit: `e200251b34349ab6c34548d30319abde86cb6bc6`
- Package version: `1.0.1`
- Original branch: `master`
- Local branch: `math-research-custom`
- Local customization commit: `41f8639`
- Remote renamed from `origin` to `upstream`

Current README/source provider support:

- Claude CLI: `sonnet`, `opus` aliases; optional effort levels.
- Mistral Conversations API: Leanstral; `MISTRAL_API_KEY`.
- Z.ai/GLM compatible API: `GLM_API_KEY`.
- OpenRouter: Kimi/MiniMax; `OPENROUTER_API_KEY`.
- Local OpenAI-compatible server through `HFClient`/vLLM.

Upstream commit `e200251b` has no direct OpenAI Responses API provider. The local `math-research-custom` layer now adds one without replacing upstream providers. It uses the official OpenAI Python SDK 2.53.0, disables SDK-internal retry, supplies its own bounded retry/error normalization, and never represents ChatGPT/Codex subscription credentials as an API key.

## Lean coupling

Natural-language `prove` mode requires only a Markdown theorem and an LLM backend. Lean is optional and activated by `--lean-project`, `--lean-theorem`, `--lean-items`, or `--lean-worker-tools`. Lean-specific parsing, checking, MCP and search code is isolated under `openprover/lean`.

## Windows findings

Unmodified upstream failed during import because `openprover.tui.tui` imports Unix-only `termios`/`tty`. Full upstream pytest collection also executed Unix-only `os.killpg` assumptions. Minimal local changes now:

- fall back to upstream `HeadlessTUI` on native Windows;
- use Windows process groups for Claude CLI subprocesses;
- avoid Unix `preexec_fn/resource` for Lean subprocess startup.

The full-screen TUI and `openprover inspect` remain Unix-oriented. Headless proving and the math-research CLI are the supported first-stage Windows path.

## Authentication/manual steps

- GitHub CLI login is required when pushing or managing remote repositories; not needed for local zero-cost smoke testing.
- A real LLM run requires a provider-specific login/key or a local server. Mock smoke testing and OpenAI dry-run need none.
- `OPENAI_API_KEY` is wired to the official Responses provider; when active quota is available, live provider testing can proceed.
