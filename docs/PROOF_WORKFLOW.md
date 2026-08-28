# TypeScript ProofWorkflow

本文档记录 TypeScript 对 OpenProver 证明工作流的 clean-room 重编排。参考依据是仓库中的行为考据文档 `doc1-why-not-openprover.md` 与此前已移出的 OpenProver 实现；运行时不依赖 OpenProver Python 包。

## 运行链路

```text
obligation + mode
      │
      ▼
Planner context
  theorem / whiteboard / repository index / failed routes / task graph
  recent merged Worker+Verifier output / budget / artifact status
      │
      ▼
Workflow-controller action protocol
  JSON 或 <OPENPROVER_ACTION> TOML
      │
      ├── read_theorem / read_items / write_items / write_whiteboard
      ├── spawn ──┬─ ready logical agent 1 ──┐
      │           ├─ ready logical agent N ──┼─ 独立 Verifier 批次 ──┐
      │           └─ blocked/dependent tasks ┘                        │
      │                                                               ▼
      │                                       merged feedback + task states
      │                                                               │
      └──────────────────────────────────────────── next controller round
      ├── literature_search / use_tool
      ├── submit_proof
      └── submit_lean_proof
```

`ProofWorkflow`/`ProofRuntime` 是唯一拥有证明状态和提交门禁的执行内核。默认的 `dynamic` 模式由模型在每轮决定是直接求解还是如何拆出逻辑 agent；它可以声明 `dependsOn`、`successCriteria` 和 `continuationOf`，运行时只执行依赖已完成的 ready frontier，并把任务状态反馈给下一轮控制器。运行时会从命题中的显式问题分段、编号要求和长度/要求密度计算 decomposition signal；复合命题若首轮仍只有一个 broad worker，会自动展开为问题驱动的 focused workers 加最终 synthesis，而不是默默接受单 agent 覆盖整题。简单命题仍可走单 worker。agent 元数据只描述目的和能力，不授予工具权限；实际模型/profile 由受信任的 factory 解析。`legacy` 模式保留旧的固定 Planner/Worker/Verifier 提示词以便对照回退。

因此，模型负责“这次工作应该如何组织”，运行时负责“哪些动作被允许、如何持久化、何时并行、如何恢复以及什么证据可以提交”。这不是一次性让模型生成不可检查的 DAG：每轮都会重新规划，部分输出会成为可续跑的状态，独立 Verifier 和 formal gate 仍然是提交前的硬门槛。`maxWorkers` 是并发上限，不是拆解策略；复合首轮计划的准入 guard 会保证至少形成 focused frontier 和 synthesis barrier。若模型忘记为 `PARTIAL` 或 `FAILED_RETRYABLE` 任务发出 `continuationOf`，动态运行时会依据原任务元数据生成一个稳定的通用续接任务；它不推断或硬编码某一道题的具体分块。

在 `formalize_only` / `prove_and_formalize` 模式中，调用方只需提交数学命题，不必手写 Lean 证明。若调用方提供精确的 `THEOREM.lean` 模板，Formalizer 必须保持该声明并填补证明洞；否则 Formalizer 负责把原命题翻译成 Lean 声明并返回完整源文件。运行时自动创建 `FORMALIZATION` 任务并路由到配置的 Formalizer。每个完整 Lean 源文件由非 shell 的 Lean 进程检查；编译拒绝会持久化为 `formalAttempts`，任务转为 `FAILED_RETRYABLE`，编译反馈和上一版源码进入下一 continuation。Planner 的 `stop` 不能跳过这条修复链。只有进程成功才写入 `PROOF.lean`；工具不可用才进入 `BLOCKED_FORMAL`。运行时同时拒绝 proof-local 的 `sorry`、`admit`、`axiom`、`constant` 和 `opaque` 逃逸。没有预先给定 Lean 声明时，Lean 能机械保证的是 Formalizer 生成的命题及其证明；自然语言到形式命题的对应关系仍需在任务输出中审计。

## 与 OpenProver 的对应关系

| OpenProver 行为 | TypeScript 实现 |
| --- | --- |
| `Prover.run()` / `_do_step()` | `ProofWorkflow.run()`，每轮建立 `steps/step_NNN` |
| `parse_planner_toml()` | `parsePlannerPlan()`，同时接受 JSON 与 tagged TOML |
| 动态 `spawn` 任务图与 ready frontier | `dispatchTasks()` + `dependsOn` + `maxWorkers` |
| 逻辑 agent 复用/创建 | `ProofAgentFactory` + 稳定 `agentId` |
| Worker 完成后并行 Verifier | `dispatchTasks()` 的两阶段批次 |
| `_push_output()` | `recentOutputs` 中的 `Merged Worker + Verifier feedback` |
| `Repo` / `[[slug]]` | `ProofRepository` 与 `resolveWikilinks()` |
| `WHITEBOARD.md` | run workspace 内的白板工件和 Session event |
| `step_history.json` / resume | `state.json`、Session custom entries、`step_status.json` |
| `submit_proof` | 读取 candidate 或 repository slug，并要求独立 `CORRECT` |
| `FORMALIZATION` task / `submit_lean_proof` | `ProofFormalVerifier`；默认 adapter 执行 `lake env lean`，失败进入动态修复 continuation |

Planner 的 retry 只处理协议层失败，最多三次；解析器兼容旧的单数 `task` 输出并归一化为任务数组。数学上被 Verifier 否定的路线不能通过 retry 绕过，而会写入 `failedRoutes` 并出现在下一轮上下文中。Worker 如果以模型输出上限结束，结果会记录为 `PARTIAL`，不会被伪装成候选；控制器可以用 `continuationOf` 生成续跑任务。

## 持久化工件

一次 run 默认位于：

```text
.math-agent/proof-runs/<run-id>/
├── THEOREM.md
├── THEOREM.lean                  # 调用方提供精确目标时
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
        ├── task status events / dependency frontier in state.json
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
- `BLOCKED_FORMAL`：Lean 命令不可用或形式化环境未配置。
- `BLOCKED_PROVIDER`：模型、literature 或远端工具 Provider 不可用。
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
