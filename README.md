# Math Research Agent

Math Research Agent 是一个 Proof-first 数学证明工作台。当前运行时已经硬切换为
TypeScript：`backend` 是唯一证明核心，`apps/proof-workbench` 是独立的浏览器 GUI，
浏览器只通过 HTTP/SSE 访问证明 API。GUI 不依赖 DeepSeek Harness，也不把 Harness
的 CLI、插件、工具、沙箱、MCP、Python SDK 或研究运行时带进来。

## 快速启动

需要 Node.js 22+ 和 pnpm 11+：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

浏览器访问 `http://127.0.0.1:3080`。Windows 使用：

```powershell
.\scripts\install.ps1
.\scripts\start.ps1
```

API-only 模式适合前端开发和自动化测试：

```bash
pnpm start:api
```

仓库当前配置的 API 地址是 `http://127.0.0.1:43100`（以启动输出和 TOML 为准）。启动链路只包含 Node、pnpm、
TypeScript proof API 和 `apps/proof-workbench`，不需要任何外部 Harness checkout。

GUI 源码在 [`apps/proof-workbench`](apps/proof-workbench)。其中 `web/` 可以独立交给
任意静态服务器托管；`server/main.mjs` 只负责本地静态文件服务和连接 proof API。

## 完整证明链路

```text
Session → theorem → Proof Run
              ↓
Planner → 并行 Worker → 独立 Verifier
              ↓              ↓
        repository/白板 ← 合并反馈
              ↓
       submission gate → answer/proof
```

工作流按 `doc1-why-not-openprover.md` 中的 OpenProver 行为重新编排，包含
Planner action protocol、步骤工件、并行 Worker/Verifier、失败路线、候选去重、
白板、恢复和提交门。浏览器不会直接导入 `ProofWorkflow`。

证明状态严格分为：

`CANDIDATE_READY`（候选已独立验证但未提交）、`PARTIAL`（预算耗尽）、
`PROVED`（提交门通过）、`BLOCKED_PROVIDER`（Provider 或形式化依赖不可用）、
`FAILED`（工作流失败）和 `CANCELLED`（调用方取消）。只有提交门通过后才会
返回 `PROVED`。

## HTTP/SSE API

典型客户端流程：

```text
POST /v1/sessions
POST /v1/sessions/:sessionId/theorem
POST /v1/sessions/:sessionId/proof-runs
GET  /v1/sessions/:sessionId/proof-runs/:runId/events   # SSE
GET  /v1/sessions/:sessionId/proof-runs/:runId
GET  /v1/sessions/:sessionId/proof-runs/:runId/result
```

此外还提供：

- `GET /v1/sessions`、Session 读取和运行列表；
- `POST .../cancel` 取消运行；
- `GET /v1/config`、`GET /v1/config/document`、`PUT /v1/config`；
- `GET /v1/config/models` 脱敏模型目录。

一个最小命题是：

```text
1 + 3 + ... + (2n - 1) = n²，n ≥ 1
```

API-only 的完整验收应创建 Session、提交这条命题、启动 Proof Run、读取 SSE、
轮询结果并确认 `PROVED` 与归纳证明文本；重启服务后再次读取同一个 Session、
Run 和结果，应得到相同的持久化产物。

## TOML 配置与模型角色

唯一权威配置是 [`configs/math-agent.toml`](configs/math-agent.toml)，包含：

- `[runtime]`：Host、Web/API 端口、数据目录；
- `[proof]`：模式、并行 Worker 数、步骤上限；
- `[research]`、`[budgets]` 与 `[corpus]`：长周期策略、检查点/停滞、执行预算和语义导入；
- `[tools]`：统一工件访问、允许的受控执行能力与可执行文件；
- `[formalization]`：默认启用的 Lean 4 进程门及其 Lake project；用户只需提交数学命题，Formalizer 会生成或修复完整 Lean 源码，编译失败会回到动态修复任务；若调用方提供精确 Lean 目标，runtime 仍会严格保持该声明；
- `[models.*]`：Provider、Model ID、Base URL、API key 环境变量名、推理强度、
  context window、最大输出长度和自定义请求参数；
- `[roles.*]`：Planner、Worker、Verifier、Synthesizer、Formalizer、Literature
  Researcher，以及 Research Director、Corpus Bootstrapper、Secondary Auditor 的模型映射和运行参数。

## 自主研究 API

`/v1/research/projects` 提供持久化的 MRR v1 研究链路：语义 corpus bootstrap、模型驱动策略、
类型化中间数学贡献、严格 target-proof 门、统一跨周期工件检索、自动证据回执、路线/覆盖图、
任务级恢复、文献、root readiness、综合、独立终审和可选 Lean 进程门。完整协议、硬不变量和外部
Proof-as-Test 启动说明见：

- [`docs/MATHEMATICAL_RESEARCH_RUNTIME.md`](docs/MATHEMATICAL_RESEARCH_RUNTIME.md)
- [`docs/MRR_V1_INVARIANTS.md`](docs/MRR_V1_INVARIANTS.md)
- [`docs/MRR_CRITICAL_LAYER_READINESS.md`](docs/MRR_CRITICAL_LAYER_READINESS.md)
- [`docs/CORPUS_ARCHIVE_PROTOCOL.md`](docs/CORPUS_ARCHIVE_PROTOCOL.md)
- [`docs/CORPUS_ARCHIVE_INTEGRATION_MAP.md`](docs/CORPUS_ARCHIVE_INTEGRATION_MAP.md)

长期研究语料发布是一个独立、默认关闭的投影层。只有已持久化的语义 effect，或带有当前
`FinalProofAuthority` 的严格结论，才可进入配置的 canonical Git corpus；Planner/Worker/
Verifier 原始输出、scratch、candidate proof 与 audit JSON 均不会直接发布。`[corpus]` 中的
`publishing_enabled`、`repository_url`、`local_checkout`、`branch`、`auto_push`、
`index_command` 和可选 `node_path` 控制该层。Git 或 push 失败不会回滚研究真值。

构建后可用以下命令检查/恢复 outbox，无需直接打开状态 JSON：

```bash
pnpm run build:proof
pnpm run corpus -- status --project <project-id>
pnpm run corpus -- pending --project <project-id>
pnpm run corpus -- inspect --project <project-id> --intent <intent-id>
pnpm run corpus -- retry --project <project-id> --intent <intent-id>
pnpm run corpus -- publish --project <project-id> --intent <intent-id>
pnpm run corpus -- reconcile --project <project-id>
```

Web 设置页“模型与证明角色”和高级 TOML 编辑器共用一个 `ConfigService`。
修改使用修订号、串行化并发更新、文件锁语义和临时文件原子替换；正在运行的
Proof Run 使用启动时配置快照，新配置只影响后续运行。密钥只保存环境变量名，
不写入配置文件、不显示、不返回；Provider 仅在真实运行时按环境变量读取。

当前默认配置的 `gemini37` 使用 `google-vertex` provider 和
`gemini-3.7-flash`。Provider adapter 已改为 Google 官方
`@google/genai` SDK：本地通过 Application Default Credentials（例如
`gcloud auth application-default login`）认证，部署到 Cloud Run 时直接使用
Cloud Run service identity 的 ADC；项目 ID 和模型区域分别由
`GOOGLE_CLOUD_PROJECT`、`GOOGLE_CLOUD_LOCATION` 提供。启动脚本和后端不会扫描、
读取或签名仓库里的 GCP JSON 文件。对应项目必须启用 Vertex AI API，并授予
运行身份调用模型所需的 IAM 权限。

Cloud Run 部署与演示见 [`docs/CLOUD_RUN_DEMO.md`](docs/CLOUD_RUN_DEMO.md)。

## 开发检查

研究运行时文档：[`docs/MATHEMATICAL_RESEARCH_RUNTIME.md`](docs/MATHEMATICAL_RESEARCH_RUNTIME.md)。
Critical Layer 启动与就绪边界：[`docs/MRR_CRITICAL_LAYER_READINESS.md`](docs/MRR_CRITICAL_LAYER_READINESS.md)。

```bash
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run test:proof
pnpm run build
```

核心测试覆盖 TOML 解析、角色/模型校验、密钥脱敏、请求参数、原子写入、并发
修订、ProofWorkflow、SSE/HTTP API、OpenProver parity 和 Session 恢复。

目录中的旧研究资料和历史实现仅作为迁移参考，不属于当前启动链路；活动入口
只有 `scripts/install.*`、`scripts/start.*`、`package.json`、`backend/`、
`apps/proof-workbench/` 和上述 HTTP API。

MIT License.
