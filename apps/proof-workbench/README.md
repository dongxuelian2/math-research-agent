# Proof Workbench GUI

这是 Math Research Agent 的独立 GUI 模块。它只包含浏览器端的证明工作台和一个很薄的静态服务器，不依赖 DeepSeek Harness、Cordis、Python SDK、MCP、沙箱、CLI、插件系统或任何证明状态机。

## 保留的 GUI 能力

- 证明会话侧栏与会话切换；
- 数学命题、上下文和证明模式输入；形式化模式下由 Formalizer 自动生成 Lean 源码，用户不需要手写证明代码；
- 动态 Controller 策略、ready frontier、任务依赖、逻辑 Agent、continuation 和 Lean 修复回合展示；
- 通过 SSE 显示运行事件、白板、候选、Verifier 裁决和 Lean 进程结果；
- `PROVED`、`PARTIAL`、`FAILED`、`BLOCKED_FORMAL`、`BLOCKED_PROVIDER`、`CANCELLED` 等真实状态；
- SSE/轮询刷新时保留内容区滚动位置、输入焦点和光标；
- 最终证明和 Lean 形式化结果展示；
- 模型目录、角色映射和 TOML 设置编辑。
- 右上角中英语言切换；语言选择保存在浏览器本地。
- 在完整源码配置下，新建 session 时显示后端初始化的独立 Lean 项目、工具链、Mathlib 包和导入；
  初始化失败会在界面中显示为不可用，而不会伪装成已形式化。Cloud Run 演示配置不携带 Lean，默认只运行非形式化证明。

## 边界

GUI 只调用 `/v1/...` HTTP API。证明逻辑、Provider 路由、持久化和提交门属于仓库根目录的 `backend/`，不复制到这里。

浏览器文件位于 `web/`，可以独立交给任意静态服务器托管。集成启动时，`server/main.mjs` 在本地同时启动 proof API 和静态服务器；在 Cloud Run `--cloud-run` 模式下，静态服务器与 proof API 共享一个公开端口和同源地址，并通过同进程路由转发 API 请求。它只通过编译后的 `backend/dist/` 连接后端。

```bash
node server/main.mjs
```

完整项目入口仍然是仓库根目录的 `bash scripts/start.sh`。
