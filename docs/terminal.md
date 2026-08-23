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
| `/project <path>` | 切换项目目录 |
| `/config <path>` | 切换模型配置 |
| `/run [theorem-id]` | 后台启动研究运行 |
| `/demo [path]` | 运行确定性的演示项目生成器 |
| `/clear` | 清空当前会话日志 |
| `/quit` | 退出终端 |

`Ctrl-C` 退出，`F5` 刷新，方向键浏览命令历史。真实模型运行前，请复制并填写 `configs/.env.example` 对应的环境变量。
