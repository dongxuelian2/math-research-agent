# Rust 终端客户端

`apps/mathagent-tui` 是 MathAgent 的交互入口。它使用 Rust 的 `ratatui` 绘制 TUI、`crossterm` 接收键盘事件，并在后台线程调用 Python 研究后端。

## 分层

```text
Rust TUI
  ├── 键盘、命令输入、历史记录
  ├── 项目状态和定理列表展示
  ├── 后台任务状态与运行日志
  └── uv run → Python research CLI
                    ├── Provider / Responses API
                    ├── 研究编排
                    ├── 反例与依赖审计
                    └── Audit Gate / 持久化工件
```

Rust 层不复制数学研究状态机，也不直接实现模型协议。这样终端可以保持轻量，Python 层的 provider、审计和项目数据仍然可以被 CI、脚本和其他客户端复用。

完整的项目隔离、焦点、布局、滚动、补全和聊天边界见 [TUI 交互设计](TUI_INTERACTION_DESIGN.md)。

## 安装与启动

```bash
bash scripts/install.sh
bash scripts/start.sh
```

安装脚本会锁定同步 Python 依赖，并使用 `apps/mathagent-tui/Cargo.lock` 构建 release 二进制。开发时也可以直接运行：

```bash
cargo run --manifest-path apps/mathagent-tui/Cargo.toml -- --root "$PWD"
```

## 交互命令

输入命令后按 Enter 执行：

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示帮助 |
| `/status`、`/refresh` | 重新读取项目和定理状态 |
| `/switch [path]` | 不带路径时打开项目选择器；带路径时直接切换已有项目；`/project` 仍是兼容别名 |
| `/new [id] [purpose]` | 打开大号多行目标编辑器；填写项目 ID 和长目标后用 `F2` 创建并切换 |
| `/run` | 由项目级 orchestrator 分析 purpose、生成子命题 DAG，并按依赖前沿并行调度证明与审计 |
| `/import <file>` | 将论文、Markdown、文本、TeX 或 PDF 原件归档到当前项目 |
| `/config <path>` | 切换模型配置 |
| `/stop` | 停止当前项目的研究运行 |
| `/steps` | 读取当前项目最近的持久化步骤 |
| `/details` | 切换 Activity 摘要和完整 Transcript |
| `/demo [path]` | 运行确定性的演示项目生成器 |
| `/clear` | 清空当前会话日志 |
| `/quit` | 退出终端 |

一个完整流程：

```text
/new odd-sum 证明所有自然数 n 的前 n 个奇数之和等于 n²
/run
```

如果目标较长，可以只输入 `/new`，在大号编辑器中填写项目 ID 和多行核心目标。编辑器内 `Tab` / `Shift-Tab` 切换字段，目标框中 `Enter` 或 `Ctrl-J` 换行，`F2` 创建，`Esc` 取消。已有项目用 `/switch` 选择或切换。

项目会保留可恢复的工作轨迹：`timeline.jsonl` 是统一的追加式步骤流，TUI 打开项目时会恢复其中的 Activity；`/steps` 也读取它。`/run` 会复用未完成的项目计划和子命题 `state.json`，已通过的子命题不会重复运行，未完成的运行会从已有白板、Worker 报告、流水线状态和审计产物继续。

导入已有材料：

```text
/import /path/to/old-paper.pdf
/run
```

原件保存在项目的 `inbox/`，`/run` 时提取为 `work/imported/<file-id>.md`，并生成同目录的分析清单。导入材料只是未验证的工作上下文，不会因为文件名或论文措辞自动变成 `PROVED`。PDF 使用系统 `pdftotext` 提取文本；扫描型或加密 PDF 若无法提取，会保留原件并在步骤流中标记错误。

输入 `/` 会在输入框上方打开命令菜单，`↑/↓` 选择，`Tab` 或 `Enter` 补全。`Tab`/`Shift-Tab` 切换面板焦点；鼠标只有左键单击才会切换焦点，移动和 hover 不会改变焦点。滚轮只滚动指针所在面板，也不会抢夺焦点。Session 向上滚动后会暂停自动跟随，按 `End` 恢复。`Shift-Enter` 或 `Ctrl-J` 输入换行。

Session 默认采用类似 Codex/OpenCode 的 Activity 摘要，只显示 `Run · <target>`、`Explore · <project>`、成功/停止/失败和 stderr 错误。原始命令与 stdout 隐藏在完整 Transcript 中；按 `Ctrl-T` 或输入 `/details` 打开全屏 Transcript，再按 `Ctrl-T` 或 `Esc` 返回。每条记录是有类型的 entry，折行续行继承原始样式，不会出现 Error/System 第一行有颜色、第二行变白的问题。

项目是隔离工作区。切换项目会同时切换它自己的项目状态、输入草稿、历史、会话画布、选中定理、滚动位置和运行状态；后台输出按项目路由，即使当前正在查看其他项目也不会串台。

当前 Rust TUI 已支持自由文本编辑和项目内会话隔离，但 Python 后端还没有项目内普通聊天协议。自由文本会保留在当前项目的 TUI 会话中并明确提示能力缺口，不会绕过研究编排直接调用模型或伪造证明状态。真实模型运行前，请将 `configs/.env.example` 复制为仓库根目录 `.env` 并填写环境变量；TUI 启动后端时会自动读取该文件，已有 shell 环境变量优先。
