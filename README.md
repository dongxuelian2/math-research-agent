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
uv sync --extra dev
bash scripts/bootstrap.sh
```

脚本会生成零成本的 Observatory 演示并在 `http://127.0.0.1:8765` 启动本地界面。真实 Gemini 运行需要配置 `GEMINI_API_KEY`；也支持 Vertex Gemini、OpenAI、任意 OpenAI Responses 兼容端点和 Codex CLI 路由。

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
docs/           架构、信任边界、运行说明与历史归档
examples/       独立数学题与证明样例
projects/       可运行项目的命题、来源、指令和运行数据
scripts/        所有 Bash/PowerShell 启动、运行和 benchmark 脚本
src/            Python 包源码
tests/          核心、研究层、Provider 和端到端测试
```

根目录只保留项目元数据和标准入口：`README.md`、`LICENSE`、`pyproject.toml`、`uv.lock`、`.gitignore`。`pytest` 配置已合并进 `pyproject.toml`，不会再额外维护根目录 `pytest.ini`。

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
