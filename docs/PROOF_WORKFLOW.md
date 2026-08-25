# TypeScript ProofWorkflow

本文档记录 TypeScript 对 OpenProver 证明工作流的 clean-room 重编排。参考依据是仓库中的行为考据文档 `doc1-why-not-openprover.md` 与此前已移出的 OpenProver 实现；运行时不依赖 OpenProver Python 包。

## 运行链路

```text
obligation + mode
      │
      ▼
Planner context
  theorem / whiteboard / repository index / failed routes
  recent merged Worker+Verifier output / budget / artifact status
      │
      ▼
Planner action protocol
  JSON 或 <OPENPROVER_ACTION> TOML
      │
      ├── read_theorem / read_items / write_items / write_whiteboard
      ├── spawn ──┬─ Worker 1 ──┐
      │           ├─ Worker 2 ──┼─ 独立 Verifier 批次 ──┐
      │           └─ Worker N ──┘                       │
      │                                                  ▼
      │                                     merged feedback → next Planner
      ├── literature_search / use_tool
      ├── submit_proof
      └── submit_lean_proof
```

`ProofWorkflow`/`ProofRuntime` 是唯一拥有证明状态和提交门禁的编排器。三个 Agent 角色只负责模型调用：Planner 决定下一步，Worker 解决一个聚焦任务，Verifier 独立检查 Worker 的输出。

## 与 OpenProver 的对应关系

| OpenProver 行为 | TypeScript 实现 |
| --- | --- |
| `Prover.run()` / `_do_step()` | `ProofWorkflow.run()`，每轮建立 `steps/step_NNN` |
| `parse_planner_toml()` | `parsePlannerPlan()`，同时接受 JSON 与 tagged TOML |
| `spawn` 并行 Worker | `dispatchTasks()` + `maxWorkers` |
| Worker 完成后并行 Verifier | `dispatchTasks()` 的两阶段批次 |
| `_push_output()` | `recentOutputs` 中的 `Merged Worker + Verifier feedback` |
| `Repo` / `[[slug]]` | `ProofRepository` 与 `resolveWikilinks()` |
| `WHITEBOARD.md` | run workspace 内的白板工件和 Session event |
| `step_history.json` / resume | `state.json`、Session custom entries、`step_status.json` |
| `submit_proof` | 读取 candidate 或 repository slug，并要求独立 `CORRECT` |
| `submit_lean_proof` | `ProofFormalVerifier`；默认 adapter 执行 `lake env lean` |

Planner 的 retry 只处理协议层失败，最多三次；数学上被 Verifier 否定的路线不能通过 retry 绕过，而会写入 `failedRoutes` 并出现在下一轮上下文中。

## 持久化工件

一次 run 默认位于：

```text
.math-agent/proof-runs/<run-id>/
├── THEOREM.md
├── THEOREM.lean                  # 配置 formal mode 时
├── WHITEBOARD.md
├── run_config.json
├── state.json
├── PROOF.md                       # informal gate 通过后
├── PROOF.lean                     # formal gate 通过后
├── repo/
└── steps/
    └── step_001/
        ├── planner_context.json
        ├── planner_prompt.md
        ├── planner_response.txt
        ├── planner_plan.json
        ├── actions.json
        ├── worker_<id>_task.md
        ├── worker_<id>_result.json
        ├── worker_<id>_output.md
        ├── verifier_<candidate>.json
        └── step_status.json
```

Session JSONL 保存同一 run 的 typed `proof/*` custom entries；文件工件保存完整上下文和模型输出，二者共同用于恢复和审计。

## 完成状态

- `CANDIDATE_READY`：至少一个候选通过独立 Verifier，但尚未完成提交。
- `PROVED`：`prove` 已写入 `PROOF.md`；`formalize_only` 已写入 `PROOF.lean`；`prove_and_formalize` 两者都已写入。
- `FAILED`：所有允许的步骤都结束但没有路线通过独立验证；具体失败路线保存在 `failedRoutes` 和步骤工件中。
- `PARTIAL`：预算或最大 step 到达，仍无可接受完成物。
- `BLOCKED_PROVIDER`：模型、literature、tool 或 formal verifier 依赖不可用。
- `CANCELLED`：调用方通过取消 API 或 `AbortSignal` 中止运行。

`PROVED` 只表示本 workflow 的局部提交门禁通过，不自动表示 Python 研究层的全局分支闭合、authority、dependency 或审计治理已经完成。

## HTTP Proof API

`ProofApiServer` 是前端与证明编排器之间的 HTTP 边界，位于
`backend/src/api/server.ts`。它不让前端直接持有 `ProofWorkflow`
实例，而是把一次运行拆成可轮询的 session/run 资源：

```text
POST /v1/sessions
  ↓ 201 sessionId
POST /v1/sessions/:sessionId/theorem
  ↓ 200 THEOREM_ACCEPTED
POST /v1/sessions/:sessionId/proof-runs
  ↓ 202 runId / status link / result link
GET  /v1/sessions/:sessionId/proof-runs/:runId/events
  ↓ text/event-stream typed proof events
GET  /v1/sessions/:sessionId/proof-runs/:runId
GET  /v1/sessions/:sessionId/proof-runs/:runId/result
  ↓ 200 PROVED + answer.proof
```

`POST /theorem` 会把用户命题追加到 Session JSONL，并保存 typed
`proof-api/theorem_submitted` entry；`POST /proof-runs` 启动异步
`ProofWorkflow`，状态接口在运行期间返回 `202`/`RUNNING`，结束后返回
`ready: true`、最终状态、`ProofRunResult`、完整 `answer.proof` 和持久化
`state`。`CANDIDATE_READY` 不是运行终态，只有下一轮 Planner 的
`submit_proof` 通过提交门后才会变成 `PROVED`。角色通过 `ProofApiRoleFactory` 注入，因此 HTTP 层可以接真实
`AgentCore`/Provider，也可以在离线测试中接 `MockProvider`。

完整的客户端边界测试是
`backend/test/proof-api.test.ts`：测试只使用原生 HTTP
`fetch` 完成创建 session、提交奇数和命题、启动 run、轮询 result、读取
最终证明文件；它不直接调用 `ProofWorkflow.run()`。

## 最小 API 验证

`proof-agent-smoke.test.ts` 用三个真实 `AgentCore` 和离线 `MockProvider` 运行“前 n 个奇数之和等于 n²”：Planner 先写白板/资料并 spawn，Worker 生成归纳证明，独立 Verifier 返回 `CORRECT`，下一轮 Planner 通过 `submit_proof` 写入 `PROOF.md`。该测试是引入更复杂编排前的真实 API 闭环门槛。
