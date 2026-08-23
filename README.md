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

脚本会生成零成本的 Observatory 演示并在 `http://127.0.0.1:8765` 启动本地界面。真实 Gemini 运行需要配置 `GEMINI_API_KEY`；也支持 Vertex Gemini、OpenAI 和 Codex CLI 路由。

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

## 仓库结构

```text
src/math_research_agent/
├── core/       自研候选生成内核：预算、知识条目、动作协议、worker loop
├── research/   项目编排、审计门、状态机、路由、持久化和 Observatory
├── providers/  Provider 公共传输工具
└── formal/     可选 Lean 编译器桥接与证明完整性检查
tests/          核心、研究层和端到端测试
docs/           架构、信任边界和运行说明
scripts/        Bash/PowerShell 启动与 benchmark 脚本
examples/       数学题与证明样例
```

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
