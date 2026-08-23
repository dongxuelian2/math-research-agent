# Math Research Agent

一个以数学证明为主的 agent：负责生成证明候选、并行探索、反例与依赖审计、失败路线修复，以及可选的 Lean 形式化。证明候选永远不能自行升级为 `PROVED`，最终状态由独立审计门决定。

```text
数学命题
   ↓
Context Builder → Planner → Workers → Candidate Proof
                                      ↓
                         counterexample / dependency / boundary audits
                                      ↓
                              Audit Gate
                         FAIL ↙       ↘ PASS
                    FAILED_ROUTE     PROVED
                         ↓
                    repair successor
```

## 快速启动

```bash
bash scripts/install.sh
bash scripts/start.sh
```

安装脚本会配置 Python 研究后端、构建 Rust 终端客户端，然后由 `start.sh` 启动 MathAgent。也可以用 `bash scripts/install.sh --launch` 一步安装并启动。Windows 使用 `.\scripts\install.ps1 -Launch`。

安装前需要系统已有 `uv` 和 Rust/Cargo；脚本会自动创建或更新项目的 `.venv`，并构建本地终端二进制。安装和启动入口统一在 `scripts/`，不会再维护另一套 Observatory bootstrap 流程。

终端内默认显示项目状态、当前目标、定理列表和研究日志。输入 `/help` 查看命令；例如 `/run demo-odd-sum` 会在后台启动一次研究运行。真实 Gemini 运行需要配置 `configs/.env.example` 中的环境变量；也支持 Vertex Gemini、OpenAI、任意 OpenAI Responses 兼容端点和 Codex CLI 路由。

单独生成演示：

```bash
uv run python -m math_research_agent.research demo \
  --project projects/observatory-demo
```

运行项目：

```bash
uv run math-research run \
  --project projects/demo \
  --target demo-odd-sum \
  --config configs/models.mock.json
```

多模型路由：`configs/models.responses.example.json` 展示了 planner/auditor 使用官方 OpenAI、worker/counterexample 使用另一个 Responses 兼容服务的配置。兼容服务只需提供 `POST /v1/responses`，并在配置中填写任意模型名、`base_url_env` 和 `api_key_env`；上层研究编排不需要改动。

## 仓库结构

```text
configs/        模型、环境模板和 benchmark manifest
apps/           Rust 终端客户端（ratatui/crossterm）
docs/           架构、信任边界、运行说明与历史归档
examples/       独立数学题与证明样例
projects/       可运行项目的命题、来源、指令和运行数据
scripts/        所有 Bash/PowerShell 安装、启动、运行和 benchmark 脚本
src/            Python 包源码
tests/          核心、研究层、Provider 和端到端测试
```

根目录只保留项目元数据和标准入口：`README.md`、`LICENSE`、`pyproject.toml`、`uv.lock`、`.gitignore`。`pytest` 配置已合并进 `pyproject.toml`，不会再额外维护根目录 `pytest.ini`。

Rust 终端与 Python 研究后端分层：Rust 只负责交互、键盘、状态展示和后台任务；数学证明生成、Provider 路由、审计门和信任内核仍由 `src/math_research_agent/` 提供。TUI 通过 `uv run --project ... python -m math_research_agent.research` 调用后端，因此不会复制第二套研究状态机。

Responses API 约定

所有 OpenAI 风格模型共享 `math_research_agent.providers.responses.ResponsesRequest` 请求契约。`openai` 使用官方端点，`openai_compatible` 使用自定义 `base_url`；两者都归一化为相同的文本、tool calls、usage、finish reason 和归档结果。Gemini、Codex CLI、Mock 仍保留各自原生适配器。

## 设计边界

- `core` 是仓库自有实现，不依赖第三方 proving framework。
- Provider 输出先经过完整 JSON / Pydantic 校验，再进入状态机。
- `FAILED_ROUTE`、审计发现、来源哈希和运行状态都写入持久化工件。
- Lean 是可选的形式化通道；模型声称证明成功不会代替 Lean 编译器证书。
- benchmark 只记录实际运行结果，不发布虚构准确率。

## 检查

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
```

MIT License.
