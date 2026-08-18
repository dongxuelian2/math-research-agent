# Codex handoff

另一个 Codex session 接手 `E:\tool\math` 时，按以下顺序读取：

1. `E:\tool\math\README.md`
2. `E:\tool\math\docs\ARCHITECTURE.md`
3. `E:\tool\math\docs\ENVIRONMENT_AUDIT.md`
4. `E:\tool\math\openprover\README.md` 和 `DOCS.md`
5. `E:\tool\math\openprover\openprover\math_research\`
6. 当前 project 的 `project.json`、`index.json`、`steering/directives.json`
7. 当前 target 的 `theorems/<id>.json` 和最近 run 的 `state.json`

## Source-control facts

- OpenProver upstream commit at installation: `e200251b34349ab6c34548d30319abde86cb6bc6`.
- Project-layer commit: `41f8639` (`Add durable math research project layer`).
- OpenAI provider commit: `09a432d` (`Add OpenAI provider for math research agents`).
- The Codex CLI provider is the later local commit with message `Add Codex CLI provider for subscription-backed agents`.
- Windows npm CLI discovery commit: `c9e6094` (`Use npm Codex CLI on Windows`).
- Remote name is `upstream`, not `origin`.
- Local customization branch is `math-research-custom`.
- Never push `E:\tool\math\projects` or private Markdown to a public remote.
- GitHub CLI / remote authentication: ensure valid GitHub login is configured when interacting with remotes.

Before editing, run:

```powershell
git -c safe.directory=E:/tool/math/openprover -C E:\tool\math\openprover status --short --branch
E:\tool\math\run_math_agent.ps1 -Command status -Project main
```

## Files not to casually change

- `openprover/prover.py` and `prompts.py`: upstream core; project behavior should stay in `math_research` unless a real upstream extension point is missing.
- theorem status/history and `index.json`: update through `ProjectStore`/CLI, not ad hoc search-and-replace.
- `runs/*/state.json`: checkpoint authority; preserve when resuming.
- `audits/gate.json`: immutable evidence for an Archivist decision.
- `configs/*.local.json`, `.env`, projects, logs: local/private; never add secrets to Git.

## Invariants

- OpenProver `PROOF.md` is only a candidate.
- Only `Archivist` plus a complete `AuditGate` may write `PROVED`.
- Only status `PROVED` is an allowed dependency.
- Mock provider cannot promote a non-demo project.
- Importer always writes `UNCLASSIFIED` and ignores status-like filenames.
- Computational searches are evidence unless accompanied by a justified finite reduction/certificate.
- `provider: openai` uses the official Responses API and only `OPENAI_API_KEY`; never repurpose Codex/ChatGPT credentials.
- OpenAI retry is bounded in the provider. Authentication/model/request/quota failures are terminal, and exhausted transient retries must not be retried forever by the outer loop.
- `provider: codex_cli` is isolated from `openai`: it starts official `codex exec`, uses only the CLI's saved login and removes API-key variables from the child environment. Never inspect or copy Codex auth files.
- Codex subprocess calls use argv + stdin (`shell=False`), read-only role directories under the current run, JSONL plus final-message output, no interactive approvals and bounded explicit-transient retry only. A Windows npm `codex.cmd` is translated to Node plus the package's `bin/codex.js`; never interpolate prompts into `cmd.exe` or PowerShell.
- Codex subscription usage is never reported as API USD cost: `billing_mode=chatgpt_codex_subscription`, `cost_usd=null`, and usage is `null` unless the CLI emits it.

## OpenAI provider facts

- Official SDK installed in the project venv: `openai 2.53.0`; dependency is `openai>=2.53.0,<3`.
- Model remains config-driven. The checked example uses `gpt-5.6-sol` and only its documented effort values: `none`, `low`, `medium`, `high`, `xhigh`, `max`.
- Main files: `openprover/math_research/openai_provider.py`, `providers.py`, `orchestrator.py`, `cli.py`, and `configs/models.openai.example.json`.
- 2026-08-08 real one-request smoke reached the API but returned 429 `insufficient_quota`; no retry and no usage were available. Do not rerun repeatedly. Resolve account billing/quota first.
- A full Agent smoke was deliberately not run: it is a multi-call workflow and the provider smoke already established an account-side quota blocker.

## Codex CLI provider facts

- Main adapter: `openprover/math_research/codex_cli_provider.py`; config: `configs/models.codex.example.json`.
- `model: null` and `reasoning_effort: null` intentionally defer to the installed CLI/workspace. Do not substitute the OpenAI API example's model or effort values automatically.
- Supported explicit CLI reasoning config values are separately validated as `minimal`, `low`, `medium`, `high`, `xhigh`.
- Each successful logical call is one `codex exec` process. A run isolates role cwd at `runs/<run-id>/codex/<role>/call-N/attempt-N`.
- Prompt roles are serialized into a stable UTF-8 envelope and sent through stdin using the `-` sentinel. `--json` provides JSONL metadata/usage and `--output-last-message` provides final text.
- `provider-smoke` for Codex creates only logs, forces `max_retries=0`, requires exactly one process and never constructs the OpenAI Responses client.
- Windows install is official `@openai/codex@0.147.0` under standard npm global prefix (e.g. `C:\Users\<user>\AppData\Roaming\npm`); CLI reports `codex-cli 0.147.0`, and official `codex login status` reports `Logged in using ChatGPT`.
- Resolution priority is explicit valid CLI, PATH `codex`/`codex.cmd`, npm global CLI, then ordinary per-user locations. Desktop/WindowsApps paths are never launched; if no valid CLI remains the structured error is `windowsapps_packaged_executable_unsupported`.
- A direct, ephemeral read-only CLI smoke returned `CODEX_DIRECT_OK`. The one-call provider smoke returned `CODEX_CLI_PROVIDER_OK` with one start/process, zero retries, `api_requests=0`, CLI-reported usage and subscription billing metadata: `logs/provider-smoke/codex-cli-provider-smoke-20260808-162007.json`.
- The OpenAI Responses API still returns 429 `insufficient_quota`; the successful Codex CLI response therefore verifies that the ChatGPT subscription route is independent of API quota. Do not describe subscription use as API credit and do not inspect auth storage.
- The one-process acceptance command is:

```powershell
E:\tool\math\run_math_agent.ps1 -Command provider-smoke -Config configs\models.codex.example.json -Role auditor -Expect CODEX_CLI_PROVIDER_OK
```

- The acceptance command now passes. Do not run the multi-call demo/full Agent without a separate explicit request; if later authorized, start with WorkerCount 1:

```powershell
E:\tool\math\run_math_agent.ps1 -Command run -Project demo -Target demo-odd-sum -WorkerCount 1 -Config configs\models.codex.example.json
```

## Verification commands

```powershell
E:\tool\math\.venv\Scripts\python.exe -m pytest -q E:\tool\math\openprover\tests\math_research
E:\tool\math\run_math_agent.ps1 -Command status -Project demo -Target demo-odd-sum
E:\tool\math\run_math_agent.ps1 -Command run -Project demo -Target demo-odd-sum -WorkerCount 1 -Config configs\models.openai.example.json -DryRun
E:\tool\math\run_math_agent.ps1 -Command run -Project demo -Target demo-odd-sum -WorkerCount 1 -Config configs\models.codex.example.json -DryRun
```

Expected current test counts: research layer `34 passed`, Windows-compatible upstream subset `24 passed`, total `58 passed`.

For another demo run, first issue an explicit re-audit directive; never reset the theorem JSON by hand.
