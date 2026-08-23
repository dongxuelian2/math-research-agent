# ADR-0001：研究层与自研证明候选内核分层

## 决策

`math_research_agent.research` 是项目层唯一公开编排入口；候选生成由同仓库的 `math_research_agent.core.ResearchEngine` 负责。研究层通过明确的数据对象和策略回调接收 worker 事件，不依赖另一个 proving framework 的类层次或私有方法。

## 原因

仓库的主要产品是数学研究 agent，不是对某个上游 proving framework 的二次包装。自研内核让候选生成的协议、工件、预算和失败语义都由本项目控制，也使审计门保持在候选生成之外。

## 不变式

1. 候选生成只能写 `CANDIDATE_PROOF.md`，不能直接写 `PROVED`。
2. Provider 输出必须先通过完整结构化校验。
3. 失败路线要留下可恢复的父子运行关系和修复上下文。
4. Lean 成功证书是形式化证据，不自动改变自然语言命题状态。
