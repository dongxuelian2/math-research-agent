# MathAgent TUI 交互设计

## 1. 目标

MathAgent 的终端界面不是一个“带输入框的状态面板”，而是项目化的数学研究工作台。它借鉴 Claude Code、Codex CLI 和 OpenCode 已经验证过的终端交互：自由编辑输入、斜线命令上拉菜单、上下选择、Tab 补全、鼠标聚焦、面板独立滚动、离开底部后暂停自动跟随。

MathAgent 自己的差异在于：证明状态必须属于一个明确项目和定理，聊天内容不能直接冒充证明事实，模型回答也不能绕过 Audit Gate 把目标升级为 `PROVED`。

## 2. 核心模型：Project Workspace

一个 Project Workspace 是类似 Git 分支的独立工作上下文，但这个类比只描述“切换和隔离”，不表示项目之间可以自动 merge。

每个项目拥有两层状态：

| 层 | 内容 | 生命周期 |
| --- | --- | --- |
| 持久化研究状态 | `project.json`、`index.json`、定理、runs、events、审计、steering | 存在于 `projects/<id>/`，重启后仍在 |
| TUI 会话状态 | 输入草稿、输入历史、会话画布、选中定理、各面板滚动位置 | 当前 TUI 进程内按项目分别保存 |

项目的第一层身份是 `purpose`：它描述项目要解决的核心问题或研究目标，不等同于某一个 theorem。theorem 是 orchestrator 围绕 purpose 自动分解出的可验证子命题；新项目必须先写入 purpose，TUI 不提供人工登记子命题的命令。

切换项目时必须原子切换以下内容：

- 项目名称、路径和当前目标；
- 定理列表、选中定理和证明事实状态；
- 当前项目自己的对话/步骤画布；
- 未发送的输入草稿和历史；
- 工作区、画布和输入区的滚动位置；
- 当前项目的运行/停止状态。

项目 A 的后台输出必须按项目路径路由回 A 的会话。即使用户已经切到项目 B，也不能把 A 的输出画到 B。切回 A 后可以继续看到输出。当前实现允许每个项目各自保有一个运行任务。

### 2.1 项目级 Orchestrator

项目研究的唯一入口是 `/run`，流程固定为：

```text
purpose
  ↓
Project Supervisor / Planner
  ↓  ProjectPlanSchema（结构化、限量、无环）
子命题 materializer
  ↓
ResearchMap / OPEN obligations
  ↓
ResearchOrchestrator
  ├─ worker fan-out
  ├─ counterexample / dependency / boundary audits
  ├─ Audit Gate
  └─ failure route → repair / re-plan
```

Planner 只能返回结构化 `ProjectPlanSchema`；只有 schema 校验、ID/依赖/环检查全部通过后，子命题才会写入项目。TUI 不提供 `/theorem` 或其他人工子命题登记命令。每次计划保存在 `runs/orchestrator/<run-id>/project_plan.json`，项目元数据中的 `orchestrator` 保存当前计划、开放问题、子命题和子运行结果。

这个边界采用了 AutoGen GraphFlow 的有向图、并行/条件/循环控制思路，以及 LangGraph Supervisor 的中央 supervisor handoff 和 StateGraph 持久化共享状态思路；但实现仍使用本项目自有的 `ResearchMap`、Audit Gate 和 runtime control plane，不把第三方 agent 消息直接当作证明事实。

## 3. 项目切换

### 3.1 入口

- 输入 `/switch`：打开已有项目选择器；
- 输入 `/switch <path>`：直接切换到路径；`/project` 作为兼容别名保留；
- 输入 `/new` 或 `/new <id>`：打开大号项目目标编辑器；
- 输入 `/new <id> <purpose>`：按核心目标初始化一个新项目，并自动切换过去；
- Workspace 获得焦点时按 `p`：打开项目选择器。

### 3.2 选择器

选择器是居中的 modal，不是把项目列表塞进输入历史。它负责切换已有项目；新建项目使用 `/new` 进入大号编辑器：

```text
┌ Switch project · isolated workspace ─────────────────────────┐
│ Filter  obser▌                                               │
│                                                              │
│ ▶ Research Observatory   observatory-demo  target: bounded…  │
│   Math Research Demo     demo              target: odd-sum   │
└ Enter open · Esc cancel · ↑↓/PgUp/PgDn/mouse wheel ──────────┘
```

- 直接输入即过滤项目名、项目 id 和路径；
- `↑/↓` 改变高亮项，`PgUp/PgDn` 跨页；
- 鼠标滚轮滚动，单击改变选中项；
- `Enter` 进入选中项目，`Esc` 原样返回；
- 新项目默认创建在 `projects/<safe-name>/`；如需指定目录可传第二个 `path` 参数；
- 选中项必须同时有 `▶`、粗体和背景色，不能只靠颜色。

## 4. 主布局

宽终端采用左右布局：

```text
┌ MATHAGENT  project › target                         RUNNING ┐
├──────────────── Workspace ─────────┬──────── Session ──────┤
│ project / path / target            │ 用户消息               │
│ selected theorem title             │ Agent 实时输出          │
├──────────────── Theorems ──────────┤ Step / Error / System   │
│ ▶ ✓ theorem-a  PROVED              │                         │
│   → theorem-b  IN_RESEARCH         ├──────── Input ─────────┤
│   × theorem-c  FAILED_ROUTE        │ /pro▌                   │
│                                    │  ↑ command popup         │
└────────────────────────────────────┴─────────────────────────┘
  Focus: INPUT  Tab next pane  / commands  mouse click/scroll
```

- 左上：当前项目身份、路径、目标和当前选中定理；
- 左下：可选择、可滚动的定理列表；
- 右上：当前项目的聊天、证明步骤、系统消息和错误；
- 右下：自由编辑的多行输入区；
- 终端宽度不足 92 列时改为上下布局，避免把右侧输入压成不可用窄栏。

## 5. 焦点模型

主界面只有三个可聚焦区域：`PROJECTS`、`SESSION`、`INPUT`。

| 操作 | 结果 |
| --- | --- |
| 鼠标单击面板 | 焦点直接进入该面板 |
| 鼠标移动 / hover | 不改变焦点，也不改变选中状态 |
| 鼠标滚轮 | 滚动指针所在面板，但不抢夺当前焦点 |
| `Tab` | 下一个面板 |
| `Shift-Tab` | 上一个面板 |
| 面板获得焦点 | 边框变青色、加粗，底栏显示焦点名 |
| `Esc` | 关闭当前浮层；输入为空时从 Input 回到 Session |

按键先由最具体的上下文处理，顺序为：modal → 补全菜单 → 当前焦点面板 → 全局键。这样补全菜单打开时 `↓` 绝不能同时滚动会话，输入区的 `Tab` 也不会误触发其他全局操作。

## 6. 输入和斜线补全

输入使用字符游标而不是简单地在字符串尾部追加，因此中文、粘贴、左右移动、Home/End、删除和中间插入都可用。

- 输入 `/` 立即在输入框上方打开命令菜单；
- 继续输入按命令名过滤；
- `↑/↓` 或滚轮移动高亮；
- `Tab` 或 `Enter` 把高亮命令补全到输入框；
- `Esc` 只关闭补全菜单，不清空已经输入的内容；
- `Shift-Enter` 或 `Ctrl-J` 插入换行；
- `Ctrl-A/E/U/W` 分别为行首、行尾、删至行首、删除前一个词；
- bracketed paste 作为一次编辑插入，避免逐字符触发补全和快捷键。

大号编辑器包含 Project ID 单行框和 Core goal 多行框。目标框支持中文、多行粘贴、上下移动和滚动；`Tab` / `Shift-Tab` 切换字段，`Ctrl-Enter` 提交创建，`Esc` 取消。这样长命题不会被压缩进一行命令参数。

当前命令菜单：

| 命令 | 行为 |
| --- | --- |
| `/switch [path]` | 选择或直接切换已有项目；`/project` 为兼容别名 |
| `/new [id] [purpose]` | 打开大号编辑器，声明核心目标、创建标准项目目录并自动切换 |
| `/run` | 启动项目级 orchestrator：分析 purpose、生成子命题 DAG，并按依赖前沿并行调度证明和审计 |
| `/import <file>` | 归档论文、Markdown、文本、TeX 或 PDF，下一次 `/run` 自动转为项目工作文件 |
| `/stop` | 终止当前项目的运行进程 |
| `/steps` | 把当前项目最近的 `timeline.jsonl` 步骤载入画布 |
| `/details` | 打开或关闭完整 Transcript，与 `Ctrl-T` 相同 |
| `/status` | 重读项目和定理状态 |
| `/config <path>` | 切换模型配置 |
| `/demo [path]` | 生成确定性演示项目 |
| `/clear` | 只清空当前项目的 TUI 会话画布 |
| `/help` | 显示完整帮助 |
| `/quit` | 退出 |

当前 `/new` 把 purpose 持久化为项目身份；`/run` 进入项目级 orchestrator，由 planner 产生结构化子命题 DAG，再由既有 ResearchOrchestrator 按依赖前沿并行调度 worker、审计和修复；失败子命题只阻塞其依赖者，独立分支继续运行。再次打开项目时，TUI 从 `timeline.jsonl` 恢复 Activity；未完成的计划和子命题检查点会被复用，已完成的 `PROVED` 子命题不会重复运行。TUI 不接受手动 theorem 登记，也不从模型 prose 猜测状态。

## 7. Activity 摘要与完整 Transcript

主界面默认显示 Activity 摘要，不直接倾倒命令和 stdout/stderr：

```text
› /run
● Run · bounded-euler-polynomial
✕ Error · Target is already PROVED; request re-audit explicitly
✕ Failed · Run · bounded-euler-polynomial
```

- `Run · <target>` 表示证明运行；
- `Explore · <project>` 表示演示或探索任务；
- 成功、停止、失败分别使用 `✓`、`▲`、`✕`；
- 原始 `uv run ...` 命令与 stdout 默认隐藏；stderr 作为醒目的 Error 保留在摘要中；
- 错误摘要仍在主界面显示，不能因折叠详情而掩盖失败。

按 `Ctrl-T` 打开全屏 Transcript overlay，或输入 OpenCode 风格的 `/details`。完整页显示当前项目的全部 typed entries，包括原始命令、stdout、stderr、步骤和系统事件；再次按 `Ctrl-T` 或 `Esc` 返回 Activity。这里采用 Codex 当前源码中的 `open_transcript` 全局动作和 overlay 分层，而不是给每条字符串临时加一个展开布尔值。子命题详情页另有模型现场区域，只接收 provider 可见的 `thinking/reply` 增量；结构化响应仍留在研究控制面，不在详情页重复渲染。

Transcript 每条记录都带 `kind`，渲染器在折行前确定样式。续行继承原记录的 `kind` 和颜色，只隐藏重复前缀，因此红色 Error、灰色 System 不会在第二行变成白色。

## 8. 滚动规则

三个区域各自拥有滚动所有权，滚轮只作用于指针所在区域：

- Theorems：改变选中项并保证高亮项始终可见；
- Session：按视觉行滚动，中文宽度和换行后的行数参与计算；
- command popup / project picker：滚动候选列表；
- Help：`↑/↓` 和 `PgUp/PgDn` 滚动帮助正文。

Session 默认跟随最新输出。用户向上滚动后进入 `scroll paused`，新输出继续追加但不抢走视口；滚到底部或按 `End` 才恢复 `following`。会话画布不再使用原先的 500 行截断。

## 9. 选中状态和状态颜色

交互选择不能只靠边框或颜色：

- 当前焦点：青色粗边框；
- 当前列表项：`▶` + 粗体 + 深色背景；
- 当前项目/目标：始终在顶栏和 Workspace 摘要重复显示；
- 运行状态：顶栏显示 `READY` 或 `RUNNING · <task>`；
- 证明事实：图标和文字同时出现。

| 状态 | 图标 | 颜色 |
| --- | --- | --- |
| `PROVED` | `✓` | 绿色 |
| `IN_RESEARCH` / `RUNNING` | `→` | 黄色 |
| `CANDIDATE_READY` / `PARTIAL` | `◐` | 品红 |
| `FAILED_ROUTE` | `×` | 红色 |
| 其他 | `·` | 默认色 |

`RUNNING` 是进程状态，`PROVED` 是证明事实状态，两者不能互相替代。

## 10. 开始、停止和步骤流

`/run` 为当前项目启动一个子进程。stdout 和 stderr 必须实时逐行进入该项目的 Session，不能等进程结束后一次性显示。`/stop` 只杀死当前项目的任务；其他项目的任务不受影响。

步骤有两个来源：运行中的实时 stdout/stderr，以及项目持久化的 `events.jsonl`（由 `/steps` 重新加载）。后续应把字符串输出升级为稳定的 typed event：

```json
{
  "project_id": "demo",
  "run_id": "run-123",
  "kind": "AUDIT_STARTED",
  "status": "RUNNING",
  "summary": "dependency audit",
  "at": "..."
}
```

TUI 只能展示事实事件；它不得从模型 prose 中猜测 `PROVED`、`FAILED_ROUTE` 或运行阶段。

## 11. 普通聊天 / 向 Agent 询问

目标交互与 Codex/Claude Code 一样：输入非斜线文本后直接发给当前项目 Agent，回答流式进入 Session；问题自动携带项目 id、当前目标、选中定理和只读状态摘要。

但聊天回答属于 `ADVISORY` 通道：

- 可以解释项目、运行状态、证明步骤和审计发现；
- 可以提出下一步或请求用户确认；
- 不可以直接写入证明事实状态；
- 需要改变 steering、启动运行或执行副作用时，必须转换成明确 command/action；
- 任何 `PROVED` 变更仍只能来自正式 Audit Gate。

建议的后端契约：

```text
ChatRequest {
  project_id, session_id, target_id,
  message, selected_theorem_id,
  context_snapshot_hash
}

ChatEvent = TextDelta | ToolStarted | ToolFinished |
            ProposedAction | Error | Done
```

聊天记录应保存到项目内 `sessions/<session-id>.jsonl`，项目切换时只恢复该项目的 session。`ProposedAction`（例如“开始证明”“停止证明”“修改 steering”）必须由 TUI 显式呈现并确认，不能把自然语言当 shell 执行。

当前仓库还没有这个项目内 ChatRequest/ChatEvent 后端入口。因此本轮 TUI 接受并按项目保留自由文本，但明确显示“对话协议尚未接通”，不会伪造一个 Agent 回答。补齐这个协议是下一阶段，而不是在 Rust 层绕过研究编排直接调用模型。

## 12. 验收标准

- 中文和多行输入的插入、删除、移动、粘贴不破坏游标；
- `/` 菜单向上展开，选择、高亮、滚动和 Tab 补全一致；
- 鼠标点击三个主面板后，焦点边框和键盘行为立即改变；
- 每个可滚动区域互不抢事件；
- 向上滚动 Session 后，新输出不把用户拉回底部；
- `/switch` 可以过滤并切换项目；切回旧项目时恢复其草稿、历史和画布；
- `/new` 提供可滚动的多行目标编辑器，并用 `Ctrl-Enter` 创建项目；
- A 项目的后台输出不会出现在 B 项目；
- `/stop` 只停止当前项目；
- `RUNNING`、`PARTIAL`、`CANDIDATE_READY`、`PROVED` 和 provider block 不混报；
- 无聊天后端时明确暴露能力缺口，不伪装为已完成。
