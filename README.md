# Math Research Agent System

这是一个以 **OpenProver 1.0.1** 为证明搜索底座、面向长期自然语言数学证明工程的本地系统。OpenProver 负责 Planner、并行 Worker、Worker Verifier、Whiteboard、Repository、恢复与调用归档；`openprover.math_research` 外层负责 theorem project state、dependency slice、FAILED_ROUTE memory、独立审计和 Archivist 门禁。

系统不会把 OpenProver 生成的 `PROOF.md` 直接视为已证。它先被复制为 `CANDIDATE_PROOF.md`，只有 Counterexample Hunter、Dependency Auditor、Exhaustiveness/Converse Auditor、Boundary Auditor 和 Final Proof Auditor 全部通过，Archivist 才能将目标改为 `PROVED`。

## 已安装内容

- OpenProver 源码：`E:\tool\math\openprover`
- 本地分支：`math-research-custom`
- upstream：`https://github.com/kripner/OpenProver.git`
- Python 虚拟环境：`E:\tool\math\.venv`
- Python 3.13.1、OpenProver editable install、`mcp`、`pytest`、官方 `openai` Python SDK 2.53.0
- Windows PowerShell 入口：`E:\tool\math\run_math_agent.ps1`
- 零费用 demo：`E:\tool\math\projects\demo`
- 真实项目入口：`E:\tool\math\projects\main`

没有安装 Lean、SageMath、PARI/GP、Docker 或 WSL Linux 发行版；第一阶段不需要这些组件。

## 目录结构

```text
E:\tool\math\
  .venv\                    隔离 Python 环境
  openprover\               upstream 源码 + 小范围定制层
  projects\demo\            已通过 mocked smoke test 的小型项目
  projects\main\            私人 Markdown 导入入口
  configs\                  模型角色配置（不含密钥）
  scripts\                  辅助 PowerShell 脚本
  docs\                     审计、架构与接手说明
  logs\                     系统级日志预留
  backups\                  修改/迁移前备份预留
  run_math_agent.ps1         主启动入口
```

私人研究文件在 `projects` 中，位于 OpenProver Git 仓库之外，不会随 upstream 仓库被 push。

## 快速检查

```powershell
E:\tool\math\run_math_agent.ps1 -Command status -Project demo
E:\tool\math\run_math_agent.ps1 -Command status -Project demo -Target demo-odd-sum
```

现有 demo 已完成一次 checkpoint + resume smoke test，目标状态为 `PROVED`，但 `proof_type` 明确记录为 `MOCKED_DEMO`。mock provider 只能把标记为 demo 的项目推进到 `PROVED`；对非 demo 项目，门禁会拒绝 mock 晋级。

## 第一次导入真实 Markdown

1. 将现有 `.md` 文件复制到：

   `E:\tool\math\projects\main\inbox\`

2. 扫描并建立候选索引：

```powershell
E:\tool\math\run_math_agent.ps1 -Command import -Project main
```

导入器只提取标题、来源和保守的内容摘录。所有新记录都为 `UNCLASSIFIED`；文件名含 `proved`、`resolution`、`report` 不会触发任何状态推断。人工审核后再显式分类：

```powershell
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research transition `
  --project E:\tool\math\projects\main `
  --target <import-id> --to OPEN `
  --reason "Human reviewed statement and opened this branch"
```

## 新建 theorem / campaign

```powershell
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research add-theorem `
  --project E:\tool\math\projects\main `
  --id my-target `
  --title "My target theorem" `
  --statement-file E:\tool\math\projects\main\sources\my-target.md `
  --status OPEN `
  --dependencies proved-lemma-1,proved-lemma-2 `
  --branch current-campaign `
  --claim-type iff
```

依赖项必须已经存在于 project graph 中。只有 `PROVED` 依赖会进入 allowed dependency slice；`PARTIAL`、`CONJECTURE`、`UNCLASSIFIED` 等会作为 blocked dependency 触发审核失败。

## 构造局部上下文

默认只读取目标、递归依赖元数据、目标/直接依赖源码和相关 FAILED_ROUTE：

```powershell
E:\tool\math\run_math_agent.ps1 -Command context -Project main -Target my-target
```

只有 Planner 明确需要时才扩大源码检索：

```powershell
E:\tool\math\run_math_agent.ps1 -Command context -Project main -Target my-target -ExpandContext
```

## 运行与 Worker 数量

模型不硬编码在 Python 中。先复制并修改一个示例配置，例如：

```powershell
Copy-Item E:\tool\math\configs\models.claude.example.json E:\tool\math\configs\models.local.json
```

`*.local.json` 已被 `.gitignore` 排除。不要把 API key 写进配置；provider 从环境变量或其 CLI 登录读取凭据。

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command run `
  -Project main `
  -Target my-target `
  -WorkerCount 3 `
  -Config configs\models.local.json
```

当前配置支持的 provider 类型：

- `openai`：官方 OpenAI Python SDK + Responses API；只从 `OPENAI_API_KEY` 读取凭据。
- `codex_cli`：每次调用启动一次官方 `codex exec`，复用 `codex login` 保存的 ChatGPT/Codex 登录；不读取或转存 token，也不使用 `OPENAI_API_KEY`。
- `claude_cli`：OpenProver 原生 Claude CLI backend；使用 Claude CLI 自身的 effort 设置。
- `mistral`：需要 `MISTRAL_API_KEY`。
- `glm`：需要 `GLM_API_KEY`。
- `openrouter`：需要 `OPENROUTER_API_KEY`。
- `local_openai_compatible`：本地 vLLM/OpenAI-compatible 服务。
- `mock`：仅 demo/test，无外部调用。

本地定制层已增量加入 OpenAI Responses API 和 Codex CLI 两条相互隔离的 provider 路径，未改写 OpenProver 的通用 Planner/Worker loop。其他 provider 保持原样。

## OpenAI Responses API / GPT-5.6 Sol

示例配置：`E:\tool\math\configs\models.openai.example.json`。模型标识不是写死在 Python 逻辑中的；示例当前使用官方模型 ID `gpt-5.6-sol`。GPT-5.6 Sol 当前接受的 `reasoning.effort` 是 `none`、`low`、`medium`、`high`、`xhigh`、`max`。实现使用 Responses API，并保留 system/developer/user 角色、流式文本、OpenAI function tool call、超时、有界重试和 API usage。

官方资料：

- [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [Responses API migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [OpenAI SDKs](https://developers.openai.com/api/docs/libraries)

`OPENAI_API_KEY` 与 ChatGPT/Codex 登录或订阅不是同一种凭据。系统不会抓取 Codex token、浏览器 cookie 或私有协议，也不会把 ChatGPT 登录转换成 API key。配置文件中出现 `api_key` 会被拒绝；key 只从进程环境读取，日志只记录存在性，绝不记录值。

先做完全只读的 dry-run；它不会创建 run、改变 theorem 状态或发送请求：

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command run -Project demo -Target demo-odd-sum `
  -WorkerCount 1 -Config configs\models.openai.example.json -DryRun
```

dry-run 会输出目标、依赖切片、WorkerCount，以及 Planner、Worker、Counterexample Hunter、其余 Auditors、Final Proof Auditor 各自的 provider/model/reasoning/timeout/retry 配置。

只验证 provider 的最小命令如下。该命令强制 `max_retries=0`、最多 32 个输出 token，所以网络层严格只有一次请求；成功时必须精确返回 `OPENAI_PROVIDER_OK`：

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command provider-smoke `
  -Config configs\models.openai.example.json `
  -Role auditor -Expect OPENAI_PROVIDER_OK
```

2026-08-08 本机实测已发出一次真实请求，但 API 返回 HTTP 429 `insufficient_quota`。错误被结构化为 `quota_exceeded`，`retry_count=0`，没有重试；因此当前不能声称成功模型输出或 API usage。安全日志在 `E:\tool\math\logs\provider-smoke\`，已验证不含 key。

额度可用后，运行真实 Agent 的命令为：

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command run -Project main -Target <target-id> `
  -WorkerCount 1 -Config configs\models.openai.example.json
```

这不是低成本单请求：完整流程通常包含多次 Planner/Worker/Verifier、四个 specialist auditor 和 Final Auditor 调用。先用 dry-run 核对上下文；只有在接受费用后再运行。

## Codex CLI / ChatGPT 订阅路径

示例配置：`E:\tool\math\configs\models.codex.example.json`。这条路径与上面的 `openai` provider 并存：

Windows 推荐使用官方 npm CLI；npm global prefix 必须在用户 PATH 中，并应排在 WindowsApps 之前。安装和基础检查：

```powershell
npm install -g @openai/codex
where.exe codex
Get-Command codex -All
codex --version
codex login status
```

正常 npm 安装会暴露类似 `C:\Users\<user>\AppData\Roaming\npm\codex.cmd` 的入口。桌面应用 `...\WindowsApps\OpenAI.Codex_*\...\codex.exe` 不是 provider backend；发现逻辑会跳过它，若只有该候选则返回 `windowsapps_packaged_executable_unsupported`。Windows npm `.cmd` 不经 `shell=True`：provider 直接启动同包的 Node entrypoint `node_modules\@openai\codex\bin\codex.js`，prompt 仍只通过 stdin 传递。

```text
Math Research Agent -> codex_cli provider -> codex exec -> saved codex login -> model output
```

它不调用 OpenAI Python SDK，不要求 API quota，也不会读取 `auth.json`、浏览器 cookie 或任何 token。子进程环境显式移除 `OPENAI_API_KEY`、`CODEX_API_KEY` 和 `OPENAI_BASE_URL`；认证完全交给官方 CLI 的已保存登录。官方 CLI 支持 ChatGPT 订阅登录和 API-key 登录两种方式，本配置只按 `codex login` 的现有登录运行，账务元数据固定记录为 `chatgpt_codex_subscription`，`cost_usd` 固定为 `null`，不会把订阅用量估算成 API 美元费用。

示例把 `model` 和 `reasoning_effort` 都设为 `null`，让当前安装的 CLI/ChatGPT workspace 选择默认值，并在返回元数据中区分 requested/resolved model。若要显式覆盖，Codex CLI 当前配置参考列出的 reasoning 值是 `minimal`、`low`、`medium`、`high`、`xhigh`；这是独立于 Responses API provider 的校验集合。模型名不从 API 示例照搬，先以本机 `codex exec --help` 和一次 provider smoke 验证。

每个逻辑调用把 system/developer/user 历史稳定序列化为一个 UTF-8 JSON envelope，通过 stdin 交给末尾的 `-`，不会把数学 prompt 放进 argv。进程使用参数数组且 `shell=False`，并组合 `--json` 与 `--output-last-message`：JSONL 用于状态/usage，final-message 文件作为最终文本权威来源。运行目录隔离在：

`projects\<project>\runs\<run-id>\codex\<role>\call-<n>\attempt-<n>\`

默认使用 `read-only` sandbox、`approval_policy="never"`、ephemeral session，并忽略项目 rules 与用户 config；仍复用 Codex 的认证存储。超时或取消时终止该调用的进程树。只有明确的 timeout、rate limit 或可识别的瞬态网络/5xx 失败才按配置做有界重试；认证、模型、reasoning、订阅用量上限和格式错误不盲重试。结构化错误类型包括 `codex_not_found`、`windowsapps_packaged_executable_unsupported`、`not_authenticated`、`invalid_model`、`unsupported_reasoning_effort`、`timeout`、`process_failed`、`rate_limited`、`usage_limit_reached`、`malformed_output`、`cancelled`、`unknown_codex_error`。

官方参考：

- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive/)
- [Codex CLI command reference](https://developers.openai.com/codex/cli/reference/)
- [Codex authentication](https://developers.openai.com/codex/auth/)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference/)

先做不启动 Codex 的 dry-run；输出包含 executable、provider、role、requested model/reasoning、计划 working directory、target 和 context 字符/UTF-8 字节数，不显示认证内容：

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command run -Project demo -Target demo-odd-sum `
  -WorkerCount 1 -Config configs\models.codex.example.json -DryRun
```

首次真实验证只运行一个 Auditor 调用、严格启动一个 Codex 进程，不运行 Planner/Worker/audit pipeline，也不改变 project/theorem：

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command provider-smoke `
  -Config configs\models.codex.example.json `
  -Role auditor -Expect CODEX_CLI_PROVIDER_OK
```

2026-08-08 已修复缺失的 npm global directory，安装官方 `@openai/codex@0.147.0`，并确认 `codex-cli 0.147.0`、`Logged in using ChatGPT`。直接 CLI smoke 实际返回 `CODEX_DIRECT_OK`。随后上面的 provider smoke 实际返回 `CODEX_CLI_PROVIDER_OK`：`logical_calls=1`、`process_start_attempts=1`、`codex_processes=1`、`api_requests=0`、`retry_count=0`、`billing_mode=chatgpt_codex_subscription`。可审计摘要为 `logs\provider-smoke\codex-cli-provider-smoke-20260808-162007.json`。这证明 Codex CLI 订阅路径独立于仍返回 429 `insufficient_quota` 的 Responses API 路径工作；不要把它表述成 API credit。

只有 provider smoke 成功后才考虑完整 demo。它会有多次订阅调用，所以不是验收的默认步骤：

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command run -Project demo -Target demo-odd-sum `
  -WorkerCount 1 -Config configs\models.codex.example.json
```

## Resume / checkpoint

每个 run 的 `state.json` 记录 `CONTEXT_READY`、`CANDIDATE_READY`、`AUDITS_READY`、`COMPLETE` checkpoint。恢复最近一次相同 target 的 run：

```powershell
E:\tool\math\run_math_agent.ps1 `
  -Command run -Project main -Target my-target `
  -WorkerCount 3 -Config configs\models.local.json `
  -Resume latest
```

也可以把 `-Resume` 指向 `projects\<project>\runs` 下的具体目录名。OpenProver 自身的 step/whiteboard/repo state 保存在 run 的 `openprover` 子目录中。

## Human steering

```powershell
# 冻结一个分支
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research steer `
  --project E:\tool\math\projects\main --freeze-branch old-branch

# 禁止本轮路线
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research steer `
  --project E:\tool\math\projects\main --prohibit-route naive-congruence

# 添加允许使用的 lemma 提示
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research steer `
  --project E:\tool\math\projects\main --add-lemma lemma-new

# 对已证 theorem 发起显式 re-audit（下一次 run 会重新打开研究状态）
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research steer `
  --project E:\tool\math\projects\main --reaudit
```

结构化记录失败路线：

```powershell
E:\tool\math\.venv\Scripts\python.exe -m openprover.math_research failed-route `
  --project E:\tool\math\projects\main `
  --id valuation-route-v1 --strategy valuation --target my-target `
  --obtained "Controlled v_p of the first factor" `
  --failure-point "Second factor remains uncontrolled" `
  --insufficiency "No coprimality lemma" `
  --recovery-conditions "Resume if lemma-coprime becomes PROVED" `
  --theorems my-target
```

## Logs 与输出

每次 run 位于：

`projects\<project>\runs\<target>-<timestamp>\`

主要文件：

- `state.json`：外层 checkpoint、状态、gate、failure reasons。
- `context/CONTEXT.md` / `context.json`：dependency slice。
- `openprover/WHITEBOARD.md`、`repo/`、`steps/`、`archive/`：OpenProver 原生状态。
- `CANDIDATE_PROOF.md`：候选证明，不代表已证。
- `audits/*.json`：所有独立 audit 和 `gate.json`。
- `usage.json`：按角色统计调用、请求数、provider retry、耗时和 usage。OpenAI usage 包含 input/output/reasoning/cached/cache-write/total tokens 及 `api_reported`；Responses API 不直接报告美元成本，因此不会伪造 `cost_usd`。
- Codex CLI usage 仅在 `turn.completed` JSONL 事件实际提供时记录 input/output/reasoning/cached/total tokens 和 `cli_reported=true`；否则是 `null`。同时区分 `codex_process_count` 与 `api_request_count=0`，billing mode 为 `chatgpt_codex_subscription`，`cost_usd=null`。
- `FAILURE_REPORT.md`：审核失败时保留的结构化原因。

只有 gate PASS 后，Archivist 才在 `projects\<project>\reports\` 写 resolution Markdown 并更新 theorem/index/branch status。

## 更新 upstream

```powershell
E:\tool\math\scripts\update_upstream.ps1
```

脚本只执行 `git fetch upstream`，不会自动合并或覆盖本地定制。审阅差异后再在 `math-research-custom` 分支手工 merge/rebase。

## 测试

```powershell
E:\tool\math\.venv\Scripts\python.exe -m pytest -q E:\tool\math\openprover\tests\math_research
```

当前研究层 `29 passed`，可跨平台 upstream subset `24 passed`，合计 `53 passed`。上游完整 test collection 含 Unix-only `termios` / `os.killpg` 测试文件，在 native Windows 仍不能原样全量运行。

## 当前未实现/限制

- Windows 下 OpenProver full-screen TUI 与 `openprover inspect` 仍是 Unix-oriented；研究层使用已存在的 `HeadlessTUI`。人工 steering 通过 JSON/CLI，在 checkpoint 生效；不能在 native Windows headless core 中即时取消某一个已运行的 OpenProver Worker。
- Lean/SageMath/PARI/GP 未安装；接口保留但没有声称 formal verification。
- LLM audit 是模型审核，不是形式验证。
- 没有自动从长 Markdown 中断言 theorem 边界、依赖或 PROVED 状态；migration 需要人工确认。
- 本机 OpenAI API 账号当前返回 `insufficient_quota`；provider 已接通并正确停止，但需要账户侧可用额度后才能完成成功输出/usage smoke。
- WindowsApps 桌面应用 executable 被明确禁止作为 provider backend；官方 npm Codex CLI 已完成 direct/provider 单调用验收。完整多 Agent Codex run 仍未执行（本轮范围刻意限制为 provider 通电），并且订阅剩余用量不做猜测。
