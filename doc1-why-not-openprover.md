OpenProver 与 Math Research Agent 证明流程对照研究结果

一、研究范围

对照对象：

1. OpenProver
   路径：
   /home/xia/code/math-research-agent/openprover

2. 当前 Math Research Agent
   路径：
   /home/xia/code/math-research-agent

本次研究主要检查：

- 从命题进入系统到证明提交的完整链路；
- Planner、Worker、Verifier、Auditor、Repair 之间如何传递信息；
- 失败证明如何被记录；
- 失败路线是否会阻止重复尝试；
- 同一个错误结论换一种说法后是否可能再次提交；
- 形式化证明和非形式化证明的最终门禁；
- 当前系统中模型组件之间的断裂位置。

本次只读分析，没有修改代码。

当前已验证：

- 当前项目相关测试：20 个通过；
- 两个项目的 Python 代码均可通过 compileall；
- OpenProver 测试运行时因为依赖 mcp 需要访问 PyPI，但当前 DNS/网络不可用，未能完整执行。


二、OpenProver 的完整流程

OpenProver 的主要链路：

命题与配置
  ↓
Prover 初始化
  ↓
Planner 读取 theorem、whiteboard、repo index、最近历史
  ↓
Planner 生成动作
  ↓
Spawn Worker
  ↓
Worker 独立尝试证明
  ↓
Verifier 独立检查 Worker 结果
  ↓
Worker 结果与 Verifier 结果合并
  ↓
反馈给下一轮 Planner
  ↓
Planner 继续探索、写 repo、更新 whiteboard 或提交
  ↓
submit_proof / submit_lean_proof
  ↓
结束或继续

对应代码：

/home/xia/code/math-research-agent/openprover/openprover/prover.py


1. Prover 初始化

OpenProver 初始化以下状态：

- theorem 文本；
- 工作目录；
- whiteboard；
- repo；
- token/budget；
- step history；
- Worker 与 Verifier 配置；
- 是否使用 Lean；
- 是否从旧运行恢复。

如果是 resume，OpenProver 会读取：

- WHITEBOARD.md；
- step_history.json；
- 上一次的步骤信息；
- 中断的 Worker 信息。

step_history 会保留最近几轮 Planner 结果，通常最多保留最近 3 步。


2. Planner 输入

Planner 每轮看到：

- theorem statement；
- 当前 whiteboard；
- repo index；
- 最近 Worker 结果；
- 最近 Verifier 结果；
- 当前 budget；
- 是否已经有 PROOF.md；
- 是否已经有 PROOF.lean；
- 最近步骤历史；
- 当前是否陷入无进展。

Planner 的任务不是直接写证明，而是通过动作协议决定下一步：

- spawn；
- write_items；
- write_whiteboard；
- read_items；
- read_theorem；
- literature_search；
- submit_proof；
- submit_lean_proof。

Planner 输出会被解析成结构化动作。


3. Worker 阶段

Planner 通过 spawn 创建多个 Worker。

每个 Worker 接收：

- theorem；
- 分配给它的具体任务；
- 独立证明方向；
- 结果格式要求。

Worker 可能负责：

- 构造主证明；
- 验证某个 lemma；
- 寻找反例；
- 检查边界；
- 检查某个证明步骤；
- 尝试另一条证明路线。

Worker 之间通常并行执行。


4. Verifier 阶段

每个 Worker 的输出之后，会单独调用 Verifier。

Verifier 接收：

- 原始任务；
- Worker 输出。

Verifier 被要求：

- 独立检查；
- 不要替 Worker 悄悄修复；
- 指出具体漏洞；
- 输出明确 Verdict。

可能的结果包括：

- VERDICT: CORRECT；
- VERDICT: CRITICALLY FLAWED；
- VERDICT: NEEDS MINOR FIXES；
- VERDICT: UNFINISHED。

相关代码：

/home/xia/code/math-research-agent/openprover/openprover/prover.py:2154
/home/xia/code/math-research-agent/openprover/openprover/prompts.py:546


5. Verifier 结果返回 Planner

这是 OpenProver 当前流程中比较重要的一点。

OpenProver 会将：

- Worker 结果；
- Verifier 结果；
- 之前保存的 Worker 结果；
- 之前保存的 Verifier 结果；

合并到当前步骤输出中，然后通过 `_push_output()` 放入下一轮 Planner 的上下文。

因此 Planner 下一轮通常能看到：

```text
Worker 0:
这个证明路线……

Verification of Worker 0:
VERDICT: CRITICALLY FLAWED
问题在于……
```

相关代码：

/home/xia/code/math-research-agent/openprover/openprover/prover.py:1546-1634

这是 OpenProver 比我们当前 core 流程更完整的地方。


6. Repo 与 Whiteboard

OpenProver 让 Planner 把长期信息写入：

- repo；
- WHITEBOARD.md。

repo 用于保存：

- 已证明 lemma；
- 失败尝试；
- 关键观察；
- 文献结果；
- 最终证明；
- Lean 文件。

whiteboard 用于保存：

- 当前证明计划；
- 已经失败的方向；
- 下一步任务；
- 当前未解决的困难；
- 哪些 Worker 已经做过什么。

Prompt 明确要求：

- 失败尝试必须记录；
- 失败路线不能重复；
- 不要给另一个 Worker 分配相同任务；
- 需要说明失败原因；
- 每次重要事件后更新 whiteboard。

相关代码：

/home/xia/code/math-research-agent/openprover/openprover/prompts.py:70-109
/home/xia/code/math-research-agent/openprover/openprover/prompts.py:255-281


7. 非形式化证明提交

OpenProver 的 submit_proof 流程：

1. Planner 指定一个 repo item slug；
2. 系统读取 repo item；
3. 将内容写入 PROOF.md；
4. 检查运行模式；
5. 认为提交完成；
6. 结束当前 session。

相关代码：

/home/xia/code/math-research-agent/openprover/openprover/prover.py:1024-1050

这里没有看到：

- rejected candidate hash；
- 语义重复检测；
- 历史错误结论匹配；
- 失败路线硬阻断；
- 审计后的再次提交门禁。

因此 OpenProver 的非形式化模式并不能保证绝对避免重复错误证明。


8. Lean 形式化证明提交

OpenProver 的 submit_lean_proof 会：

1. 读取 Lean repo item；
2. 检查 theorem/lemma/def 的原始声明是否保持；
3. 检查是否修改了 theorem statement；
4. 检查是否还存在 sorry；
5. 生成临时 proof 文件；
6. 执行 lake env lean；
7. 只有 Lean 编译成功才写入 PROOF.lean；
8. 编译失败则把错误反馈给 Planner；
9. Planner 修复后重新提交。

相关代码：

/home/xia/code/math-research-agent/openprover/openprover/prover.py:1162-1238

这是 OpenProver 真正的硬门禁。

因此：

- 非形式化证明依赖 LLM 自律和 Verifier；
- Lean 证明由 Lean 内核进行最终验证；
- Lean 编译失败后不会被接受；
- 但 OpenProver 也没有完整的语义去重系统。


三、当前 Math Research Agent 的完整流程

当前系统主要流程：

项目与命题
  ↓
ProjectOrchestrator
  ↓
ResearchOrchestrator
  ↓
Context / ClaimSnapshot / Truth Kernel
  ↓
CandidateEngine
  ↓
Core Engine Planner
  ↓
Worker
  ↓
Verifier sidecar
  ↓
Candidate Proof
  ↓
Specialist Auditors
  ↓
Final Auditor
  ↓
AuditGate
  ↓
PROVED / REJECTED / PARTIAL / BLOCKED
  ↓
FailureMap / FAILED_ROUTE / Repair Successor

相关目录：

/home/xia/code/math-research-agent/src/math_research_agent/research
/home/xia/code/math-research-agent/src/math_research_agent/core


1. ProjectOrchestrator

ProjectOrchestrator 负责更高层的项目管理：

- 管理多个 theorem；
- 管理 theorem 之间的依赖；
- 管理 branch；
- 管理 theorem 状态；
- 汇总子任务状态；
- 判断项目是否完成。

它的问题是：

如果所有子任务状态都是 PROVED，它可以判断项目完成，但目前没有一个足够强的全局根命题覆盖检查来证明整个项目的逻辑闭包。


2. ResearchOrchestrator

ResearchOrchestrator 管理单个 theorem 的完整生命周期：

- 建立 context；
- 生成候选证明；
- 执行 verifier；
- 执行 specialist auditors；
- 执行 final auditor；
- 产生 AuditGate；
- 推进 theorem 状态；
- 生成失败报告；
- 记录失败路线；
- 生成 repair successor。

主要阶段：

CONTEXT
  ↓
CANDIDATE
  ↓
AUDITING
  ↓
PROVED / REJECTED / PARTIAL / BLOCKED


3. Context 阶段

Context 中包含：

- theorem statement；
- theorem id；
- claim snapshot；
- assertion identity；
- 允许使用的 dependency；
- frozen branches；
- notation scope；
- truth kernel 信息；
- replay policy；
- research map；
- open obligations；
- 当前 pipeline 状态；
- 允许使用的 authority。

这部分比 OpenProver 的普通 theorem + whiteboard 更强，因为我们有：

- TruthStore；
- ClaimSnapshot；
- ResearchMap；
- Authority registry；
- Dependency authority resolver；
- Replay policy；
- Cross-plane execution binding。


4. CandidateEngine

CandidateEngine 是研究层和 Core Engine 之间的适配器。

它会：

1. 读取 CONTEXT.md；
2. 如果存在 REPAIR_CONTEXT.md，则附加到上下文；
3. 创建 engine 工作目录；
4. 创建 Planner、Worker 的 RoutedLLMClient；
5. 创建 Budget；
6. 构造 ResearchPolicy；
7. 启动 Core Engine；
8. 读取 engine/PROOF.md；
9. 将其移动为 CANDIDATE_PROOF.md。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/research/candidate_engine.py:53-145


5. Core Engine Planner

Core Engine 的 Planner 每轮看到：

- theorem；
- whiteboard；
- repo index；
- 最近 Worker reports；
- budget；
- 当前步骤历史。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/core/engine.py:156-167

当前 Core Engine 的动作主要包括：

- spawn；
- write_items；
- write_whiteboard；
- submit_proof。

与 OpenProver 相比，当前 Core Engine 的 repo 读取能力更弱：

- Planner 看到 repo index；
- 但没有像 OpenProver 那样在流程中充分读取相关 repo item 内容；
- 失败证明是否被重新加载，依赖上下文和 whiteboard；
- 没有统一的 ProofFragment/FailureFragment 接口。


6. Worker 阶段

当前 Worker 接收：

- theorem；
- 任务描述；
- MRA worker event footer 要求。

Worker 输出除了数学文本，还应该带有结构化事件，例如：

- PROGRESS；
- NO_PROGRESS；
- FAILED_ROUTE；
- ERROR；
- VERIFIED_LEMMA；
- BRANCH_CLOSURE；
- PARAMETER_REDUCTION；
- STRONGER_INVARIANT。

之后 ResearchPolicy 会将 Worker 输出解析为 typed event sidecar：

- event_0.json；
- verifier_event_0.json。

这比 OpenProver 的纯文本 Worker 结果更结构化。


7. Verifier 阶段

当前系统有 Verifier，但存在关键断裂。

Core Engine 会执行 Verifier：

/home/xia/code/math-research-agent/src/math_research_agent/core/engine.py:205-233

Verifier 结果会被写入 verifier_event 文件，并被 ResearchPolicy 读取。

但是 `_run_workers()` 返回给 Planner 的主要是 Worker reports，而不是完整的 Verifier reports。

也就是说：

```text
Worker 结果
  ↓
Verifier 结果
  ↓
写入 sidecar / policy
  ↓
没有完整返回到 Planner history
```

这会造成：

```text
Worker：证明看起来成立
Verifier：发现关键漏洞
Planner 下一轮：主要只看到 Worker 的结果
Planner：继续沿着原路线改写
```

这是我们当前最严重的反馈断裂。


8. ResearchPolicy

ResearchPolicy 负责：

- pre-submit gate；
- role scheduling；
- stop controller；
- worker event materialization；
- verifier event materialization；
- 将 typed event 写入路由系统；
- 记录失败；
- 记录 verifier disagreement；
- 记录 frontier cycle。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/research/research_policy.py

但是当前 CandidateEngine 创建 ResearchPolicy 时：

- 配置了 root_obligation_id；
- 配置了 pipeline scheduler；
- 配置了 role scheduler；
- 配置了 stop controller；
- 没有完整把 model_router 注入到所有研究路径。

因此一部分 Worker failure 可能只停留在 sidecar 中，无法完全进入策略路由和 Planner 决策。


9. Candidate 提交前门禁

当前 `before_submit()` 主要调用 PreSubmitGate。

PreSubmitGate 检查：

- dependency 是否阻塞；
- dependency cycle；
- authority manifest；
- external claims 是否分类；
- branches 是否全部解决；
- authority 是否存在；
- replay source 是否允许；
- 是否包含结构化 blocker token。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/research/research_policy.py:41-58
/home/xia/code/math-research-agent/src/math_research_agent/research/campaign.py:546-618

这个门禁可以阻断：

- 缺少 authority；
- 未解决 dependency；
- 未解决 branch；
- replay policy 不允许；
- 明确声明的 blocker。

但它不能阻断：

- 同一错误证明的改写；
- 同一个错误主结论；
- 同一条失败路线；
- 仅替换措辞但没有新数学内容的候选；
- 已经被 Auditor 否定过的相同候选。


10. Specialist Auditors

当前审计器包括：

- counterexample_hunter；
- dependency_auditor；
- exhaustiveness_auditor；
- boundary_auditor；
- final_proof_auditor。

主要检查：

- 正向蕴含；
- 逆向蕴含；
- 完备性；
- 参数范围；
- 边界情况；
- dependency validity；
- 是否存在 counterexample；
- 各 specialist auditor 是否通过；
- final auditor 是否通过；
- 计算证据是否与数学证明分离。

这部分比 OpenProver 的单个 Worker Verifier 更强。

但是它是候选生成之后的外部阶段，当前还没有和候选生成循环形成完整实时闭环。


11. AuditGate

AuditGate 只有在全部关键条件通过时才 passed。

如果失败：

- 写入 FAILURE_REPORT.md；
- 写入 FAILURE_MAP.json；
- 写入 FAILURE_MAP.md；
- theorem 状态变为 REJECTED、PARTIAL 或 BLOCKED；
- 生成 route failure；
- 记录 reopen conditions；
- 更新 ResearchMap；
- 更新 governance state。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/research/orchestrator.py:2425-2605


12. FailureMap

FailureMap 会记录：

- category；
- exact_rejected_claim；
- auditor；
- candidate_location；
- authority_expected；
- blocking；
- repair_suggestion；
- affected_branch。

失败分类包括：

- FOUNDATION_GAP；
- SEMANTIC_GAP；
- DEPENDENCY_GAP；
- SCOPE_GAP；
- EXHAUSTIVENESS_GAP；
- BOUNDARY_GAP；
- CONVERSE_GAP；
- COUNTEREXAMPLE；
- INFRASTRUCTURE_ERROR；
- PROVIDER_ERROR。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/research/campaign.py:163-244


13. FAILED_ROUTE

Audit 失败后会将失败路线注册为持久状态。

记录内容包括：

- route description；
- exact failure condition；
- failure domain；
- evidence refs；
- method family；
- reopen conditions；
- route failure id；
- research map binding；
- governance binding。

允许重新尝试的条件包括：

- dependency snapshot changed；
- assumption snapshot changed；
- authority context changed；
- new verified lemma；
- failure condition removed。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/research/route_failure.py
/home/xia/code/math-research-agent/src/math_research_agent/research/orchestrator.py:2460-2510

这部分是我们比 OpenProver 更强的地方。

但当前问题是：

FAILED_ROUTE 已经被记录，却没有在每一次 `submit_proof` 时强制检查。


14. Repair Successor

当前系统不是在同一个 ResearchOrchestrator 内无限重试，而是通过 repair successor 产生新的运行。

Repair successor 会继承：

- previous candidate；
- previous audits；
- failure map；
- failed routes；
- verified local lemmas；
- usage summary；
- trust kernel context；
- replay policy hash；
- pending literature；
- blocked dependencies；
- verified authority；
- DAG edges；
- pending verification。

同时生成 REPAIR_CONTEXT.md，里面包含：

- theorem statement；
- previous candidate；
- failure map；
- changed dependencies；
- verified local lemmas；
- frozen strategy fingerprints；
- 不要重试 frozen strategy 的提示。

相关代码：

/home/xia/code/math-research-agent/src/math_research_agent/research/orchestrator.py:1738-1811
/home/xia/code/math-research-agent/src/math_research_agent/research/campaign.py:759-819

问题是：

- repair context 主要还是给模型看的；
- frozen strategy 没有完全变成硬门禁；
- candidate 没有语义指纹；
- 新候选没有强制证明自己与旧候选发生了实质变化。


四、OpenProver 与当前系统的逐项对照

1. Planner 输入

OpenProver：

- theorem；
- whiteboard；
- repo index；
- 最近 Worker 输出；
- 最近 Verifier 输出；
- step history。

当前系统：

- theorem；
- whiteboard；
- repo index；
- 最近 Worker reports；
- Context；
- ResearchMap；
- authority；
- dependency；
- 但 Verifier 结果没有完整回到 Planner。

结论：

OpenProver 的短期反馈闭环更完整；
我们的长期研究状态更强，但实时反馈闭环更弱。


2. Worker 与 Verifier 关系

OpenProver：

Worker → Verifier → 合并结果 → Planner。

当前系统：

Worker → Verifier → typed sidecar / policy / router；
但没有稳定地将完整 Verifier 结果返回 Planner。

结论：

我们存在明显的数据通道断裂。


3. 失败尝试记录

OpenProver：

- 主要通过 repo 和 whiteboard；
- Prompt 要求记录失败；
- 属于模型自律。

当前系统：

- FailureMap；
- FAILURE_REPORT；
- FAILED_ROUTE；
- ResearchMap；
- Governance state；
- Repair context；
- reopen conditions。

结论：

我们的持久化失败记录比 OpenProver 强。


4. 失败路线阻断

OpenProver：

- 主要是 prompt：
  “不要重试失败路线”。

当前系统：

- 有 StrategyFingerprintStore；
- 有 frozen strategy；
- 有 FAILED_ROUTE；
- 有 reopen conditions；
- 但没有全面接入候选提交硬门禁。

结论：

我们的数据结构更完整，但执行力度还不够。


5. 非形式化证明提交

OpenProver：

- 读取 repo item；
- 写入 PROOF.md；
- 结束 session；
- 没有 post-submit semantic audit；
- 没有 candidate dedupe。

当前系统：

- 先通过 PreSubmitGate；
- 再交给外部 Auditor；
- Auditor 失败后生成 FailureMap；
- 但下一次提交时没有自动检查是否是同一错误候选。

结论：

我们的审计更强，但候选重复防护不足。


6. 形式化证明提交

OpenProver：

- theorem statement integrity；
- sorry 检查；
- Lean 编译；
- 只有内核通过才接受。

当前系统：

- 有形式化研究和 formalization 组件；
- 有 authority、dependency、audit；
- 但候选生成主链路目前仍主要是 Markdown proof；
- 形式化编译没有成为所有候选证明的统一最终门禁。

结论：

OpenProver 在“局部 Lean proof acceptance”上更直接；
我们在研究治理和依赖审计上更强。


7. 完成条件

OpenProver：

- PROOF.md 存在即可结束非形式化模式；
- PROOF.lean 编译通过即可结束形式化模式；
- prove_and_formalize 模式要求两者都有。

当前系统：

- 需要 AuditGate；
- 需要 dependency/authority；
- 需要 branch/exhaustiveness/boundary；
- 需要研究状态推进；
- 需要最终 consolidation/promotion。

结论：

当前系统的完成标准明显更严格。


五、重复错误证明问题的准确结论

问题分为三种：

1. 同一个证明文本，仅修改格式；

2. 同一个数学结论，替换措辞、改写表达；

3. 同一条证明路线，换一个 lemma 名称或文字描述。

OpenProver 当前：

- 对第 1 类没有完整 hash 去重；
- 对第 2 类没有语义指纹；
- 对第 3 类只有 prompt 级禁止；
- Lean 编译可以阻止“不成立的形式化证明”；
- 非形式化 proof 仍可能重复提交。

当前系统：

- 对第 1 类没有 candidate hash gate；
- 对第 2 类没有 claim fingerprint；
- 对第 3 类有 StrategyFingerprint 和 FAILED_ROUTE，但没有强制接入 submit；
- Audit 失败会被记录，但不一定阻断后续语义等价候选；
- Verifier 结果没有完整回 Planner，导致重复概率更高。

因此准确结论是：

OpenProver 不是不会重复，而是它的单次运行反馈链比我们当前 core 更连续。

我们的系统不是没有失败记忆，而是失败记忆没有完全接入候选生成和提交决策。


六、当前模型组件之间的主要断裂

断裂一：Verifier → Planner

Verifier 已经发现问题，但完整反驳没有回到 Planner 的下一轮上下文。

影响：

- Planner 继续相信 Worker；
- 模型重新表述同一结论；
- 重复调用 Worker、Verifier；
- 浪费 token。


断裂二：Auditor → CandidateEngine

外部 Auditor 发现候选错误，并生成 FailureMap。

但候选生成系统不是立即读取一个结构化的“禁止条件”，而是通过后续 repair context 让模型自己理解。

影响：

- 失败信息依赖模型是否正确阅读；
- 模型可能只修改措辞；
- 失败路线不一定真正冻结。


断裂三：FailureMap → submit_proof

FailureMap 记录了：

- exact rejected claim；
- auditor；
- repair suggestion；
- affected branch。

但 `submit_proof` 没有查询：

- 这个 claim 是否已经失败；
- 这个 route 是否已经失败；
- 是否有新 dependency；
- 是否有新 lemma；
- 是否满足 reopen condition。

影响：

- 旧错误结论仍然能够重新进入审计；
- Auditor 再次消耗 token。


断裂四：Worker event → Research routing

Worker event sidecar 已经有：

- NO_PROGRESS；
- FAILED_ROUTE；
- ERROR；
- progress signal；
- verifier disagreement。

但部分路径没有稳定接入 model router 和 strategy scheduler。

影响：

- 失败事件存在于文件；
- 但调度器不一定因此改变下一轮路线。


断裂五：Proof text → Proof structure

当前证明大多以整篇 Markdown 传递。

系统没有把证明拆成标准化结构：

- Claim；
- Lemma；
- Dependency；
- Assumption；
- ProofStep；
- Counterexample；
- FailurePoint；
- VerificationResult。

影响：

- Auditor 只能重新读整篇证明；
- 难以判断是哪个组件已经被否定；
- 难以只修复一个局部 obligation；
- 难以对新旧证明进行结构对比。


断裂六：Obligation identity

ResearchPolicy 中 obligation_id 的 fallback 可能使用：

- task.obligation_id；
- task.obligation；
- prover.work_dir.name。

如果任务没有明确 obligation_id，可能落到类似 engine 这样的工作目录名称。

影响：

- 失败被记录到错误 obligation；
- 路由历史无法准确聚合；
- 同一个数学问题可能被拆成多个“看似不同”的失败对象；
- 重复检测失效。


断裂七：项目完成与数学闭合

ProjectOrchestrator 可以汇总子任务状态。

但局部 PROVED 不一定代表：

- 根命题所有 branch 已覆盖；
- 所有 unbounded branch 已关闭；
- 所有依赖都是合法 authority；
- 没有遗漏 converse；
- 没有未处理的边界；
- 没有把 finite certificate 当成 global proof。

因此：

PROVED 必须继续区分：

- CANDIDATE_READY；
- PARTIAL；
- REJECTED；
- BLOCKED_PROVIDER_QUOTA；
- HUMAN_REQUIRED；
- PROVED。


七、我们当前最缺的核心机制

第一层：Candidate fingerprint

用于识别相同文本或格式改写。

内容包括：

- theorem_id；
- normalized proof text；
- normalized claim list；
- dependency list；
- lemma list；
- assumption list。

结果生成 candidate_hash。


第二层：Claim fingerprint

用于识别“同一个错误结论换一种说法”。

需要抽取：

- 主结论；
- 子结论；
- 关键蕴含；
- 使用的假设；
- 证明目标；
- 被 Auditor 拒绝的 claim。

结果生成 claim_fingerprint。


第三层：Route fingerprint

用于识别“同一条证明路线换包装”。

可以包含：

- theorem_id；
- branch_id；
- target_obligation；
- method_family；
- dependency set；
- lemma set；
- key assumption；
- failure point。

结果生成 route_fingerprint。


第四层：RejectedCandidate Registry

每次 Auditor 拒绝后持久化：

- candidate_hash；
- claim_fingerprint；
- route_fingerprint；
- exact rejected claim；
- failure condition；
- counterexample；
- audit evidence；
- repair suggestion；
- reopen conditions；
- parent run id；
- target obligation id。


第五层：CandidateNoveltyGate

提交前强制检查：

```text
candidate
  ↓
生成 candidate_hash
  ↓
生成 claim_fingerprint
  ↓
生成 route_fingerprint
  ↓
查询历史 rejected candidates
  ↓
若完全相同：
    直接拒绝
若 claim 相同、route 相同：
    检查是否有新证据
若无新 dependency、新 lemma、新 assumption 或 failure condition 改变：
    直接拒绝
否则：
    允许进入 Auditor
```


八、建议的修复优先级

P0：打通 Verifier → Planner

Verifier 结果必须和 Worker 结果一起进入 Planner history。

Planner 必须看到：

- VERDICT；
- exact gap；
- failure kind；
- failed route；
- required change；
- 是否允许重试。


P0：增加候选重复硬门禁

在 `submit_proof` 和外部 Auditor 之前增加：

- candidate hash；
- claim fingerprint；
- route fingerprint；
- rejected candidate 查询；
- no-progress 检查。


P0：把 FAILED_ROUTE 接入提交入口

当 route 被标记为失败后：

- 同一路线禁止再次提交；
- 除非满足 reopen condition；
- 不允许仅仅通过换措辞重新打开。


P1：统一 obligation identity

每个 Worker task 必须强制携带：

- theorem_id；
- obligation_id；
- branch_id；
- parent_obligation_id；
- route_id。

禁止使用工作目录名作为长期 fallback。


P1：结构化证明组件

将证明拆为：

- ProofClaim；
- ProofLemma；
- ProofDependency；
- ProofStep；
- ProofFailure；
- ProofAuditResult。

Auditor 不再只返回大段自然语言，而是返回具体被否定的组件。


P1：修复上下文结构化

REPAIR_CONTEXT 不应只有 Markdown。

应该附带：

- previous_candidate_hash；
- rejected_claims；
- prohibited_routes；
- allowed_reopen_conditions；
- required_new_evidence；
- unchanged_parts；
- exact repair obligation。


P2：增加 no-progress budget

设置：

- 同一 obligation 最大重复次数；
- 同一 route 最大尝试次数；
- 同一 claim 最大 rejected 次数；
- 连续 no-progress 次数；
- 自动停止阈值；
- 转人工审查条件。


P2：把 Lean 纳入最终闭环

对可以形式化的证明：

- 候选先生成 Markdown；
- 自动提取 Lean obligation；
- Lean 编译；
- 只有 Lean proof 或明确审计豁免才允许进入 PROVED。


九、最终判断

OpenProver 的主要长处：

- Worker 和 Verifier 结果即时返回 Planner；
- prompt 明确要求不要重试失败路线；
- repo 和 whiteboard 形成轻量失败记忆；
- Lean 路径有实际编译门禁；
- 单次运行内部反馈连续。

OpenProver 的主要不足：

- 非形式化 submit_proof 缺少语义审计；
- 没有 candidate hash；
- 没有 claim fingerprint；
- 没有全局 rejected candidate registry；
- prompt 级“不要重复”不是硬门禁；
- 外部审计重启后可能重复。

当前 Math Research Agent 的主要长处：

- 有完整的 ResearchOrchestrator；
- 有 specialist auditors；
- 有 AuditGate；
- 有 FailureMap；
- 有 FAILED_ROUTE；
- 有 reopen conditions；
- 有 ResearchMap；
- 有 Governance state；
- 有 repair successor；
- 有 authority、dependency、replay policy；
- 有更严格的 PROVED 条件。

当前 Math Research Agent 的主要不足：

- Verifier 结果没有完整回到 Planner；
- FailureMap 没有接入 submit_proof 的硬门禁；
- 没有候选语义指纹；
- route freeze 主要是 prompt，不是强制执行；
- 失败事件和路由调度没有完全打通；
- obligation identity 可能错绑；
- Proof 文本没有结构化拆分；
- 外部 Auditor 与 CandidateEngine 之间存在阶段性断裂；
- 当前系统更容易出现“错误结论改写后再次提交”。

一句话总结：

OpenProver 依靠“即时反馈 + prompt 纪律 + Lean 编译”减少重复；

我们拥有“持久失败路线 + 审计治理 + 修复 successor”，但还缺少把这些失败记忆强制接入 Planner 和 submit_proof 的 CandidateNoveltyGate。

目前最关键的两个修复点是：

1. 把 Verifier 的完整失败结果返回 Planner；
2. 在候选提交前增加基于 candidate、claim、route 三种指纹的重复证明硬门禁。