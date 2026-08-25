# Proof Workbench GUI

这是 Math Research Agent 的独立 GUI 模块。它只包含浏览器端的证明工作台和一个很薄的本地静态服务器，不依赖 DeepSeek Harness、Cordis、Python SDK、MCP、沙箱、CLI、插件系统或任何证明状态机。

## 保留的 GUI 能力

- 证明会话侧栏与会话切换；
- 数学命题、上下文和证明模式输入；
- Planner、Worker、Verifier、proof gate、Lean 的进度展示；
- 通过 SSE 显示运行事件、白板、候选和验证结果；
- `PROVED`、`PARTIAL`、`FAILED`、`BLOCKED_PROVIDER`、`CANCELLED` 等真实状态；
- 最终证明和 Lean 形式化结果展示；
- 模型目录、角色映射和 TOML 设置编辑。

## 边界

GUI 只调用 `/v1/...` HTTP API。证明逻辑、Provider 路由、持久化和提交门属于仓库根目录的 `backend/`，不复制到这里。

浏览器文件位于 `web/`，可以独立交给任意静态服务器托管。集成启动时，`server/main.mjs` 同时启动 proof API 和静态服务器；它只通过编译后的 `backend/dist/` 连接后端。

```bash
node server/main.mjs
```

完整项目入口仍然是仓库根目录的 `bash scripts/start.sh`。
