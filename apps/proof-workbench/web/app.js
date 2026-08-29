const runtime = window.__MATH_PROOF_RUNTIME__ || {};
const apiOrigin = typeof runtime.apiOrigin === "string" ? runtime.apiOrigin.replace(/\/$/u, "") : "";
const languageStorageKey = "math-proof-language";
const translations = {
  zh: {
    pageTitle: "数学证明工作台",
    languageToggleText: "EN",
    switchLanguage: "切换到英文",
    switchTheme: "切换主题",
    mainNavigation: "主导航",
    proofWorkbench: "证明工作台",
    modelsAndSettings: "模型与设置",
    proofSessions: "证明会话",
    newSession: "新建会话",
    newProof: "新建证明",
    proofApiConnected: "已连接",
    proofApiSameOrigin: "同源连接",
    brandSubtitle: "以证明为先的研究",
    emptySessions: "还没有证明会话",
    emptySessionsHint: "创建一个命题开始研究",
    untitledTheorem: "未输入命题",
    runCount: "{count} 次运行",
    cannotConnect: "无法连接证明 API：{message}",
    apiRequestFailed: "Proof API 请求失败（{status}）",
    status: {
      OPEN: "待提交",
      RUNNING: "运行中",
      CANDIDATE_READY: "候选待提交",
      PROVED: "已证明",
      PARTIAL: "部分完成",
      FAILED: "失败",
      BLOCKED_FORMAL: "形式化工具阻塞",
      BLOCKED_PROVIDER: "Provider 阻塞",
      CANCELLED: "已取消",
      fallback: "待提交",
    },
    sessionCreated: "已创建新的证明会话",
    theoremRequired: "请先输入数学命题",
    proofStarted: "证明运行已启动",
    cancelRequested: "已请求取消当前运行",
    settingsSaved: "模型与角色配置已保存",
    tomlSaved: "TOML 配置已保存",
    requestNotCompleted: "请求未完成",
    configurationNotSaved: "配置未保存",
    dynamicProofWorkspace: "动态证明工作区",
    theoremPageDescription: "Controller 动态拆解任务图；Worker、Verifier 与 Lean 修复回合按真实后端状态推进。",
    ready: "就绪",
    sectionTheorem: "01 / 命题",
    theorem: "数学命题",
    theoremPlaceholder: "例如：对所有 n ≥ 1，1 + 3 + ... + (2n - 1) = n²。",
    contextLabel: "假设与证明要求",
    optional: "可选",
    contextPlaceholder: "例如：请使用数学归纳法，并明确说明边界条件。",
    noLeanCodeRequired: "无需填写 Lean 代码",
    formalPolicy: "Formalizer 会根据上面的数学命题生成完整 Lean 4 源码；runtime 只在本地 Lean 进程通过后才会结束。编译失败会自动带着反馈回到修复任务。",
    leanSessionProject: "本 session 的 Lean 项目",
    leanProjectReady: "Lean 项目已就绪",
    leanProjectNotInitialized: "等待 Lean 项目",
    leanProjectUnavailable: "Lean 项目不可用",
    leanProjectUnavailableHint: "Lean 工具链或证明包初始化失败。",
    formalizationDisabled: "当前 Cloud Run 轻量版本未携带 Lean；本次只运行非形式化证明，完整形式化源码保留在仓库中。",
    leanToolchain: "工具链",
    leanPackages: "证明包",
    leanImports: "导入",
    proofMode: "证明模式",
    prove: "证明",
    proveAndFormalize: "证明并形式化",
    formalizeOnly: "仅形式化",
    clear: "清空",
    cancelRun: "取消运行",
    processing: "处理中…",
    startProof: "开始证明",
    dynamicWorkflow: "02 / 动态工作流",
    controllerFrontier: "Controller 与当前 Frontier",
    strategy: "策略",
    waitingStrategy: "等待 Controller 生成本轮策略",
    legacyWorkflow: "Legacy workflow",
    runnableTasks: "当前可运行任务",
    controllerRounds: "Controller 回合",
    dynamicTasks: "动态任务",
    currentFrontier: "当前 Frontier",
    leanAttempts: "Lean 尝试",
    recentEvents: "最近事件",
    taskGraph: "03 / 任务图",
    dynamicTaskGraph: "动态任务图",
    researchWhiteboard: "研究白板",
    whiteboardPlaceholder: "Controller 的研究白板会显示在这里。",
    formalGate: "04 / 形式化门",
    leanVerificationRepair: "Lean 验证与修复回合",
    process: "流程",
    finalSubmission: "05 / 提交",
    finalAnswer: "最终答案",
    proofPlaceholder: "提交一个命题后，完整证明会显示在这里。",
    noCompleteProof: "本次运行没有生成完整证明文本。",
    workflowInProgress: "动态 Workflow 正在推进，最终状态必须通过对应提交门。",
    leanFormalResult: "Lean 形式化结果",
    emptyTaskGraph: "Controller 生成动态任务后，任务图会显示在这里。",
    noRunnableFrontier: "当前没有可运行 frontier",
    noLeanProcess: "尚未调用 Lean 进程门。",
    noFeedback: "无反馈",
    noDependencies: "无前置依赖",
    dependencies: "依赖 {ids}",
    defaultWorker: "默认 Worker",
    agent: "Agent {id}",
    continuation: "续接 {id}",
    unnamedTask: "未命名任务",
    runLabel: "运行",
    revisionLabel: "修订",
    stepLabel: "步骤",
    leanPassed: "LEAN 通过",
    leanRejected: "LEAN 拒绝",
    passed: "通过",
    rejected: "拒绝",
    unknown: "未知",
    taskStatus: {
      PENDING: "等待依赖",
      RUNNING: "运行中",
      COMPLETED: "已完成",
      PARTIAL: "待续接",
      FAILED_RETRYABLE: "待修复",
      FAILED_TERMINAL: "终止失败",
      BLOCKED: "阻塞",
      fallback: "未知",
    },
    events: {
      "proof/planner_output": "Controller 已规划",
      "proof/task_dispatched": "任务已派发",
      "proof/task_status_changed": "任务状态变化",
      "proof/research_result": "Worker 返回结果",
      "proof/verification_result": "Verifier 返回裁决",
      "proof/formal_verification_result": "Lean 进程返回",
      "proof/route_failed": "证明路线被拒绝",
      "proof/submitted": "非形式化证明已提交",
      "proof/status_changed": "运行状态变化",
      "proof/step_started": "Controller 回合开始",
      "proof/step_finished": "Controller 回合结束",
      fallback: "Proof 事件",
    },
    dynamicTask: "动态任务",
    verifier: "Verifier",
    leanPassedSummary: "Lean 通过",
    leanRejectedSummary: "Lean 拒绝",
    configuration: "配置",
    settingsDescription: "证明角色和 Provider 配置由同一个 TOML 服务管理。",
    settingsSecurityDescription: "每个 Proof Run 在启动时固定一份配置快照。密钥只通过环境变量读取，不会显示在页面或 API 响应中。",
    loadingSettings: "正在读取 math-agent.toml…",
    settingsUnavailable: "配置不可用，请重新加载。",
    reloadSettings: "重新加载",
    sdkCloudIdentity: "SDK ADC / Cloud Run 身份",
    noKeyRequired: "无需密钥",
    envConfigured: "环境变量已配置",
    waitingEnv: "等待环境变量",
    provider: "Provider",
    modelId: "Model ID",
    baseUrl: "Base URL",
    apiKeyEnv: "API Key 环境变量",
    reasoning: "Reasoning",
    defaultValue: "默认",
    contextWindow: "Context window",
    maxTokens: "Max tokens",
    customRequestParameters: "自定义请求参数（JSON）",
    roleDisabled: "已关闭",
    roleEnabled: "新运行启用",
    modelCatalog: "模型目录",
    modelsSection: "01 / 模型",
    rolesSection: "02 / 角色",
    sourceSection: "03 / 源码",
    saveModelConfig: "保存模型配置",
    saving: "保存中…",
    roleMapping: "证明角色映射",
    advancedToml: "高级 TOML",
    saveToml: "保存 TOML",
    roles: {
      planner: "Planner：工作流规划",
      worker: "Worker：数学探索",
      verifier: "Verifier：独立审计",
      synthesizer: "Synthesizer：答案组织",
      formalizer: "Formalizer：Lean 形式化",
      literature_researcher: "Literature Researcher：文献上下文",
    },
  },
  en: {
    pageTitle: "Math Research Agent",
    languageToggleText: "中",
    switchLanguage: "切换到中文",
    switchTheme: "Toggle theme",
    mainNavigation: "Main navigation",
    proofWorkbench: "Proof Workbench",
    modelsAndSettings: "Models & Settings",
    proofSessions: "Proof sessions",
    newSession: "New session",
    newProof: "New proof",
    proofApiConnected: "connected",
    proofApiSameOrigin: "same-origin",
    brandSubtitle: "Proof-first research",
    emptySessions: "No proof sessions yet",
    emptySessionsHint: "Create a theorem to start researching",
    untitledTheorem: "No theorem entered",
    runCount: "{count} runs",
    cannotConnect: "Cannot connect to Proof API: {message}",
    apiRequestFailed: "Proof API request failed ({status})",
    status: {
      OPEN: "Ready",
      RUNNING: "Running",
      CANDIDATE_READY: "Candidate ready",
      PROVED: "Proved",
      PARTIAL: "Partial",
      FAILED: "Failed",
      BLOCKED_FORMAL: "Formal tool blocked",
      BLOCKED_PROVIDER: "Provider blocked",
      CANCELLED: "Cancelled",
      fallback: "Ready",
    },
    sessionCreated: "New proof session created",
    theoremRequired: "Enter a theorem first",
    proofStarted: "Proof run started",
    cancelRequested: "Cancellation requested for the current run",
    settingsSaved: "Model and role configuration saved",
    tomlSaved: "TOML configuration saved",
    requestNotCompleted: "Request incomplete",
    configurationNotSaved: "Configuration not saved",
    dynamicProofWorkspace: "DYNAMIC PROOF WORKSPACE",
    theoremPageDescription: "The Controller decomposes the task graph dynamically; Worker, Verifier, and Lean repair rounds follow live backend state.",
    ready: "READY",
    sectionTheorem: "01 / THEOREM",
    theorem: "Theorem",
    theoremPlaceholder: "Example: For every n ≥ 1, 1 + 3 + ... + (2n - 1) = n².",
    contextLabel: "Assumptions & proof requirements",
    optional: "optional",
    contextPlaceholder: "Example: Use mathematical induction and state the boundary conditions explicitly.",
    noLeanCodeRequired: "No Lean code required",
    formalPolicy: "The Formalizer generates complete Lean 4 source from the theorem above; the runtime ends only after the local Lean process passes. Compilation failures return to the repair task with feedback.",
    leanSessionProject: "Lean project for this session",
    leanProjectReady: "Lean project ready",
    leanProjectNotInitialized: "Waiting for Lean project",
    leanProjectUnavailable: "Lean project unavailable",
    leanProjectUnavailableHint: "Lean toolchain or proof-package setup failed.",
    formalizationDisabled: "This lightweight Cloud Run image does not carry Lean; this demo runs informal proofs only, while the complete formalization source remains in the repository.",
    leanToolchain: "Toolchain",
    leanPackages: "Packages",
    leanImports: "Imports",
    proofMode: "Proof mode",
    prove: "Prove",
    proveAndFormalize: "Prove & formalize",
    formalizeOnly: "Formalize only",
    clear: "Clear",
    cancelRun: "Cancel run",
    processing: "Processing…",
    startProof: "Start proof",
    dynamicWorkflow: "02 / DYNAMIC WORKFLOW",
    controllerFrontier: "Controller & current frontier",
    strategy: "Strategy",
    waitingStrategy: "Waiting for the Controller to generate this round's strategy",
    legacyWorkflow: "Legacy workflow",
    runnableTasks: "Runnable tasks",
    controllerRounds: "Controller rounds",
    dynamicTasks: "Dynamic tasks",
    currentFrontier: "Current frontier",
    leanAttempts: "Lean attempts",
    recentEvents: "Recent events",
    taskGraph: "03 / TASK GRAPH",
    dynamicTaskGraph: "Dynamic task graph",
    researchWhiteboard: "Research whiteboard",
    whiteboardPlaceholder: "The Controller's research whiteboard will appear here.",
    formalGate: "04 / FORMAL GATE",
    leanVerificationRepair: "Lean verification & repair rounds",
    process: "PROCESS",
    finalSubmission: "05 / SUBMISSION",
    finalAnswer: "Final answer",
    proofPlaceholder: "The complete proof will appear here after you submit a theorem.",
    noCompleteProof: "This run did not produce complete proof text.",
    workflowInProgress: "The dynamic workflow is progressing; the final state must pass the corresponding submission gate.",
    leanFormalResult: "Lean formalization result",
    emptyTaskGraph: "The task graph will appear after the Controller creates dynamic tasks.",
    noRunnableFrontier: "No runnable frontier",
    noLeanProcess: "The Lean process gate has not been called yet.",
    noFeedback: "No feedback",
    noDependencies: "No dependencies",
    dependencies: "Depends on {ids}",
    defaultWorker: "Default Worker",
    agent: "Agent {id}",
    continuation: "Continues {id}",
    unnamedTask: "Unnamed task",
    runLabel: "RUN",
    revisionLabel: "REV",
    stepLabel: "step",
    leanPassed: "LEAN PASSED",
    leanRejected: "LEAN REJECTED",
    passed: "PASSED",
    rejected: "REJECTED",
    unknown: "Unknown",
    taskStatus: {
      PENDING: "Waiting for dependencies",
      RUNNING: "Running",
      COMPLETED: "Completed",
      PARTIAL: "Awaiting continuation",
      FAILED_RETRYABLE: "Needs repair",
      FAILED_TERMINAL: "Terminal failure",
      BLOCKED: "Blocked",
      fallback: "Unknown",
    },
    events: {
      "proof/planner_output": "Controller planned",
      "proof/task_dispatched": "Task dispatched",
      "proof/task_status_changed": "Task status changed",
      "proof/research_result": "Worker returned a result",
      "proof/verification_result": "Verifier returned a verdict",
      "proof/formal_verification_result": "Lean process returned",
      "proof/route_failed": "Proof route rejected",
      "proof/submitted": "Informal proof submitted",
      "proof/status_changed": "Run status changed",
      "proof/step_started": "Controller round started",
      "proof/step_finished": "Controller round finished",
      fallback: "Proof event",
    },
    dynamicTask: "Dynamic task",
    verifier: "Verifier",
    leanPassedSummary: "Lean passed",
    leanRejectedSummary: "Lean rejected",
    configuration: "CONFIGURATION",
    settingsDescription: "Proof roles and Provider configuration are managed by the same TOML service.",
    settingsSecurityDescription: "Each Proof Run captures a configuration snapshot at launch. Secrets are read only from environment variables and never shown in the page or API response.",
    loadingSettings: "Reading math-agent.toml…",
    settingsUnavailable: "Configuration unavailable. Reload to try again.",
    reloadSettings: "Reload settings",
    sdkCloudIdentity: "SDK ADC / Cloud Run identity",
    noKeyRequired: "No key required",
    envConfigured: "Environment variable configured",
    waitingEnv: "Waiting for environment variable",
    provider: "Provider",
    modelId: "Model ID",
    baseUrl: "Base URL",
    apiKeyEnv: "API key environment variable",
    reasoning: "Reasoning",
    defaultValue: "Default",
    contextWindow: "Context window",
    maxTokens: "Max tokens",
    customRequestParameters: "Custom request parameters (JSON)",
    roleDisabled: "Disabled",
    roleEnabled: "Enabled for new runs",
    modelCatalog: "Model catalog",
    modelsSection: "01 / MODELS",
    rolesSection: "02 / ROLES",
    sourceSection: "03 / SOURCE",
    saveModelConfig: "Save model configuration",
    saving: "Saving…",
    roleMapping: "Proof role mapping",
    advancedToml: "Advanced TOML",
    saveToml: "Save TOML",
    roles: {
      planner: "Planner: Workflow planning",
      worker: "Worker: Mathematical exploration",
      verifier: "Verifier: Independent audit",
      synthesizer: "Synthesizer: Answer composition",
      formalizer: "Formalizer: Lean formalization",
      literature_researcher: "Literature Researcher: Literature context",
    },
  },
};

const terminalStatuses = new Set(["PROVED", "PARTIAL", "FAILED", "BLOCKED_FORMAL", "BLOCKED_PROVIDER", "CANCELLED"]);
const eventNames = [
  "proof/obligation_created",
  "proof/status_changed",
  "proof/step_started",
  "proof/step_finished",
  "proof/task_dispatched",
	"proof/task_status_changed",
  "proof/research_result",
  "proof/verification_result",
  "proof/candidate_ready",
  "proof/route_failed",
  "proof/repository_updated",
  "proof/whiteboard_updated",
	"proof/formal_verification_result",
	"proof/planner_output",
	"proof/submitted",
];

const state = {
  view: "proof",
  theme: localStorage.getItem("math-proof-theme") === "light" ? "light" : "dark",
  language: localStorage.getItem(languageStorageKey) === "en" ? "en" : "zh",
  sessions: [],
  activeSessionId: undefined,
  session: undefined,
  run: undefined,
  result: undefined,
  events: [],
  draftTheorem: "",
  draftContext: "",
	draftLeanTheorem: "",
  draftMode: "prove",
  formalizationEnabled: undefined,
  busy: false,
  settings: undefined,
  settingsToml: "",
  modelParameters: {},
  settingsLoading: false,
  settingsSaving: false,
  error: "",
  notice: "",
};

let eventSource;
let refreshTimer;
let connectedRunKey = "";
let noticeTimer;

const app = document.getElementById("app");
if (app === null) throw new Error("Proof Workbench: missing #app");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

function t(key, variables = {}) {
  const locale = translations[state.language] || translations.zh;
  const fallback = translations.zh;
  const resolve = (dictionary) => key.split(".").reduce((value, part) => value?.[part], dictionary);
  const template = resolve(locale) ?? resolve(fallback) ?? key;
  return Object.entries(variables).reduce((value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)), String(template));
}

function toggleLanguage() {
  state.language = state.language === "zh" ? "en" : "zh";
  localStorage.setItem(languageStorageKey, state.language);
  render();
}

function apiPath(path) {
  return `${apiOrigin}${path}`;
}

async function request(path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (init.body !== undefined && !headers.has("content-type")) headers.set("content-type", "application/json");
  let response;
  try {
    response = await fetch(apiPath(path), { ...init, headers });
  } catch (error) {
    throw new Error(t("cannotConnect", { message: error instanceof Error ? error.message : String(error) }));
  }
  const text = await response.text();
  let payload;
  try {
    payload = text.length === 0 ? undefined : JSON.parse(text);
  } catch {
    payload = undefined;
  }
  if (!response.ok) {
    throw new Error(payload?.error?.message || t("apiRequestFailed", { status: response.status }));
  }
  return payload;
}

function isTerminal(status) {
  return terminalStatuses.has(status);
}

function statusLabel(status) {
  return t(`status.${status}`) === `status.${status}` ? status || t("status.fallback") : t(`status.${status}`);
}

function statusTone(status) {
  if (status === "PROVED") return "success";
	if (status === "FAILED" || status === "BLOCKED_PROVIDER" || status === "BLOCKED_FORMAL") return "danger";
  if (status === "RUNNING") return "running";
  if (status === "PARTIAL" || status === "CANCELLED") return "warning";
  return "neutral";
}

function prettyJson(value) {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

function showError(error) {
  state.error = error instanceof Error ? error.message : String(error);
  render();
}

function showNotice(message) {
  state.notice = message;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => {
    state.notice = "";
    render();
  }, 2600);
  render();
}

function closeRunStream() {
  if (eventSource !== undefined) eventSource.close();
  eventSource = undefined;
  clearInterval(refreshTimer);
  refreshTimer = undefined;
  connectedRunKey = "";
}

function connectRunStream(run) {
  if (state.activeSessionId === undefined || run === undefined) {
    closeRunStream();
    return;
  }
  const key = `${state.activeSessionId}/${run.runId}`;
  if (isTerminal(run.status)) {
    closeRunStream();
    return;
  }
  if (connectedRunKey === key) return;
  closeRunStream();
  connectedRunKey = key;
  const path = `/v1/sessions/${encodeURIComponent(state.activeSessionId)}/proof-runs/${encodeURIComponent(run.runId)}/events`;
  eventSource = new EventSource(apiPath(path));
	const refresh = () => { void refreshRun(); };
  for (const name of eventNames) {
    eventSource.addEventListener(name, (event) => {
      try {
        const value = JSON.parse(event.data);
        state.events = [...state.events.slice(-99), value];
      } catch {
        state.events = [...state.events.slice(-99), { type: name }];
      }
      refresh();
    });
  }
  eventSource.onerror = refresh;
  refreshTimer = setInterval(refresh, 1200);
}

async function loadSessions(selectFirst = true) {
  const [value, configuration] = await Promise.all([
    request("/v1/sessions"),
    request("/v1/config").catch(() => undefined),
  ]);
  if (typeof configuration?.formalization?.enabled === "boolean") {
    state.formalizationEnabled = configuration.formalization.enabled;
    if (state.formalizationEnabled === false) state.draftMode = "prove";
    else if (state.activeSessionId === undefined && configuration.proof?.defaultMode) state.draftMode = configuration.proof.defaultMode;
  }
  state.sessions = Array.isArray(value?.sessions) ? value.sessions : [];
  if (state.activeSessionId !== undefined && state.sessions.some((item) => item.sessionId === state.activeSessionId)) {
    render();
    return;
  }
  if (selectFirst && state.sessions[0]?.sessionId !== undefined) {
    await selectSession(state.sessions[0].sessionId);
    return;
  }
  render();
}

async function selectSession(sessionId) {
  closeRunStream();
  state.activeSessionId = sessionId;
  state.session = undefined;
  state.run = undefined;
  state.result = undefined;
  state.events = [];
  state.error = "";
  render();
  try {
    const value = await request(`/v1/sessions/${encodeURIComponent(sessionId)}`);
    state.session = value;
    state.draftTheorem = value?.obligation?.theorem || "";
    state.draftContext = value?.obligation?.context || "";
		state.draftLeanTheorem = value?.leanTheorem || "";
		state.draftMode = value?.mode || (state.formalizationEnabled === false ? "prove" : "prove_and_formalize");
    state.run = Array.isArray(value?.runs) ? value.runs.at(-1) : undefined;
    if (state.run?.ready === true) {
      state.result = await request(`/v1/sessions/${encodeURIComponent(sessionId)}/proof-runs/${encodeURIComponent(state.run.runId)}/result`);
    }
    connectRunStream(state.run);
    render();
  } catch (error) {
    showError(error);
  }
}

async function refreshRun(shouldRender = true) {
  if (state.activeSessionId === undefined || state.run?.runId === undefined) return;
  try {
    const path = `/v1/sessions/${encodeURIComponent(state.activeSessionId)}/proof-runs/${encodeURIComponent(state.run.runId)}`;
    state.run = await request(path);
    if (isTerminal(state.run.status)) {
      closeRunStream();
      state.result = await request(`${path}/result`).catch(() => state.result);
      await loadSessions(false).catch(() => undefined);
    } else {
      connectRunStream(state.run);
    }
    if (shouldRender) render();
  } catch (error) {
    if (shouldRender) showError(error);
  }
}

async function createSession() {
  try {
    state.busy = true;
    render();
    const value = await request("/v1/sessions", { method: "POST", body: "{}" });
    await loadSessions(false);
    await selectSession(value.sessionId);
    showNotice(t("sessionCreated"));
  } catch (error) {
    showError(error);
  } finally {
    state.busy = false;
    render();
  }
}

async function submitProof() {
  if (state.draftTheorem.trim().length === 0) {
    state.error = t("theoremRequired");
    render();
    return;
  }
  state.busy = true;
  state.error = "";
  render();
  try {
    let sessionId = state.activeSessionId;
    if (sessionId === undefined) {
      const created = await request("/v1/sessions", { method: "POST", body: "{}" });
      sessionId = created.sessionId;
      state.activeSessionId = sessionId;
    }
    await request(`/v1/sessions/${encodeURIComponent(sessionId)}/theorem`, {
      method: "POST",
		body: JSON.stringify({ theorem: state.draftTheorem, context: state.draftContext || undefined, leanTheorem: state.draftLeanTheorem.trim() || undefined, mode: state.draftMode }),
    });
    await request(`/v1/sessions/${encodeURIComponent(sessionId)}/proof-runs`, {
      method: "POST",
      body: JSON.stringify({ mode: state.draftMode }),
    });
    state.events = [];
    await loadSessions(false);
    await selectSession(sessionId);
    showNotice(t("proofStarted"));
  } catch (error) {
    showError(error);
  } finally {
    state.busy = false;
    render();
  }
}

async function cancelRun() {
  if (state.activeSessionId === undefined || state.run?.runId === undefined) return;
  try {
    await request(`/v1/sessions/${encodeURIComponent(state.activeSessionId)}/proof-runs/${encodeURIComponent(state.run.runId)}/cancel`, { method: "POST", body: "{}" });
    showNotice(t("cancelRequested"));
    await refreshRun();
  } catch (error) {
    showError(error);
  }
}

async function loadSettings() {
  if (state.settings !== undefined || state.settingsLoading) return;
  state.settingsLoading = true;
  state.error = "";
  render();
  try {
    const [settings, document] = await Promise.all([request("/v1/config"), request("/v1/config/document")]);
    state.settings = settings;
    state.settingsToml = document?.toml || "";
    state.modelParameters = Object.fromEntries(Object.entries(settings.models || {}).map(([name, model]) => [name, prettyJson(model.requestParameters)]));
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
  } finally {
    state.settingsLoading = false;
    render();
  }
}

function updateModel(name, field, value) {
  if (state.settings?.models?.[name] === undefined) return;
  if (field === "contextWindow" || field === "maxTokens") {
    state.settings.models[name][field] = value === "" ? undefined : Number(value);
    return;
  }
  state.settings.models[name][field] = value === "" ? undefined : value;
}

async function saveSettings() {
  if (state.settings === undefined) return;
  state.settingsSaving = true;
  state.error = "";
  render();
  try {
    const models = Object.fromEntries(Object.entries(state.settings.models || {}).map(([name, model]) => {
      const raw = state.modelParameters[name] ?? "{}";
      const requestParameters = raw.trim().length === 0 ? undefined : JSON.parse(raw);
      return [name, {
        provider: model.provider,
        model: model.model,
        ...(model.baseUrl ? { baseUrl: model.baseUrl } : {}),
        ...(model.apiKeyEnv ? { apiKeyEnv: model.apiKeyEnv } : {}),
        ...(model.reasoningEffort ? { reasoningEffort: model.reasoningEffort } : {}),
        ...(model.contextWindow ? { contextWindow: Number(model.contextWindow) } : {}),
        ...(model.maxTokens ? { maxTokens: Number(model.maxTokens) } : {}),
        ...(model.requestHeaders ? { requestHeaders: model.requestHeaders } : {}),
        ...(requestParameters === undefined ? {} : { requestParameters }),
        ...(model.enabled === undefined ? {} : { enabled: model.enabled }),
      }];
    }));
    state.settings = await request("/v1/config", {
      method: "PUT",
      body: JSON.stringify({ expectedRevision: state.settings.revision, update: { models, roles: state.settings.roles } }),
    });
    const document = await request("/v1/config/document");
    state.settingsToml = document?.toml || state.settingsToml;
    state.modelParameters = Object.fromEntries(Object.entries(state.settings.models || {}).map(([name, model]) => [name, prettyJson(model.requestParameters)]));
    showNotice(t("settingsSaved"));
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
  } finally {
    state.settingsSaving = false;
    render();
  }
}

async function saveToml() {
  if (state.settings === undefined) return;
  state.settingsSaving = true;
  state.error = "";
  render();
  try {
    state.settings = await request("/v1/config", {
      method: "PUT",
      body: JSON.stringify({ expectedRevision: state.settings.revision, toml: state.settingsToml }),
    });
    showNotice(t("tomlSaved"));
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
  } finally {
    state.settingsSaving = false;
    render();
  }
}

function render() {
	const currentScroller = app.querySelector(".content-scroll");
	const scrollTop = currentScroller?.scrollTop;
	const active = document.activeElement;
	const activeId = active instanceof HTMLElement && app.contains(active) ? active.id : "";
	const selection = active instanceof HTMLTextAreaElement || active instanceof HTMLInputElement
		? { start: active.selectionStart, end: active.selectionEnd }
		: undefined;
  document.documentElement.dataset.theme = state.theme;
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  document.title = t("pageTitle");
  app.innerHTML = `
    <div class="shell">
      ${renderSidebar()}
      <div class="content-shell">
        ${renderTopbar()}
        <div class="content-scroll">
          ${state.view === "settings" ? renderSettings() : renderProof()}
        </div>
      </div>
    </div>`;
	const nextScroller = app.querySelector(".content-scroll");
	if (nextScroller !== null && typeof scrollTop === "number") nextScroller.scrollTop = scrollTop;
	if (activeId) {
		const nextActive = document.getElementById(activeId);
		if (nextActive instanceof HTMLElement) {
			nextActive.focus({ preventScroll: true });
			if (selection !== undefined && (nextActive instanceof HTMLTextAreaElement || nextActive instanceof HTMLInputElement)) {
				nextActive.setSelectionRange(selection.start, selection.end);
			}
		}
	}
}

function renderSidebar() {
  const rows = state.sessions.length === 0
    ? `<div class="empty-sidebar">${escapeHtml(t("emptySessions"))}<br><span>${escapeHtml(t("emptySessionsHint"))}</span></div>`
    : state.sessions.map((session) => {
      const active = session.sessionId === state.activeSessionId ? " active" : "";
      const status = session.latestStatus || "OPEN";
      return `<button class="session-row${active}" data-action="select-session" data-session-id="${escapeAttribute(session.sessionId)}">
        <span class="status-dot ${statusTone(status)}"></span>
        <span class="session-copy"><strong>${escapeHtml(session.theorem || t("untitledTheorem"))}</strong><small>${escapeHtml(statusLabel(status))} · ${escapeHtml(t("runCount", { count: session.runCount || 0 }))}</small></span>
      </button>`;
    }).join("");
  return `<aside class="sidebar">
    <div class="brand"><div class="brand-mark">∑</div><div><strong>MATH LAB</strong><small>${escapeHtml(t("brandSubtitle"))}</small></div></div>
    <nav class="main-nav" aria-label="${escapeAttribute(t("mainNavigation"))}">
      <button class="nav-item${state.view === "proof" ? " selected" : ""}" data-action="view-proof"><span>⌘</span>${escapeHtml(t("proofWorkbench"))}</button>
      <button class="nav-item${state.view === "settings" ? " selected" : ""}" data-action="view-settings"><span>⚙</span>${escapeHtml(t("modelsAndSettings"))}</button>
    </nav>
    <div class="sidebar-label"><span>${escapeHtml(t("proofSessions"))}</span><button class="icon-button" data-action="new-session" aria-label="${escapeAttribute(t("newSession"))}">＋</button></div>
    <div class="session-list">${rows}</div>
    <button class="new-session" data-action="new-session" ${state.busy ? "disabled" : ""}>＋ ${escapeHtml(t("newProof"))}</button>
    <div class="sidebar-footer"><span class="online-dot"></span> Proof API ${escapeHtml(apiOrigin ? t("proofApiConnected") : t("proofApiSameOrigin"))}</div>
  </aside>`;
}

function renderTopbar() {
  const status = state.view === "proof" ? (state.run?.status || "OPEN") : "";
  return `<header class="topbar">
    <div class="breadcrumb"><span>Math Research Agent</span><i>/</i><strong>${escapeHtml(state.view === "settings" ? t("modelsAndSettings") : t("proofWorkbench"))}</strong></div>
    <div class="top-actions">
      ${state.view === "proof" && status ? `<span class="status-pill ${statusTone(status)}"><span></span>${escapeHtml(statusLabel(status))}</span>` : ""}
      <button class="language-toggle" data-action="toggle-language" title="${escapeAttribute(t("switchLanguage"))}" aria-label="${escapeAttribute(t("switchLanguage"))}">${escapeHtml(t("languageToggleText"))}</button>
      <button class="theme-toggle" data-action="toggle-theme" title="${escapeAttribute(t("switchTheme"))}" aria-label="${escapeAttribute(t("switchTheme"))}">${state.theme === "dark" ? "☼" : "☾"}</button>
    </div>
  </header>`;
}

function renderProof() {
	const run = state.run;
	const runState = run?.state || {};
	const status = run?.status || "OPEN";
	const formalizationEnabled = state.formalizationEnabled !== false;
	const leanProject = state.session?.leanProject;
	const leanProjectReady = leanProject?.status === "READY";
	const leanProjectUnavailable = leanProject?.status === "UNAVAILABLE";
	const leanProjectTone = leanProjectReady ? "success" : leanProjectUnavailable ? "danger" : "warning";
	const leanProjectLabel = leanProjectReady ? t("leanProjectReady") : leanProjectUnavailable ? t("leanProjectUnavailable") : t("leanProjectNotInitialized");
	const leanProjectDetails = leanProjectReady
		? `${t("leanToolchain")}: ${leanProject.toolchain || "-"} · ${t("leanPackages")}: ${(leanProject.packages || []).join(", ") || "-"} · ${t("leanImports")}: ${(leanProject.imports || []).join(", ") || "-"}`
		: leanProject?.error || t("leanProjectUnavailableHint");
	const taskValues = Array.isArray(runState.tasks) ? runState.tasks : [];
	const candidates = Array.isArray(runState.candidates) ? runState.candidates : [];
	const formalAttempts = Array.isArray(runState.formalAttempts) ? runState.formalAttempts : [];
	const candidateCount = candidates.length;
  const verifiedCount = runState.verifications && typeof runState.verifications === "object"
    ? Object.values(runState.verifications).filter((item) => item?.verdict === "CORRECT").length
    : 0;
  const proof = state.result?.answer?.proof || "";
  const formalProof = state.result?.answer?.formalProof || "";
	const executionPlans = Array.isArray(runState.executionPlans) ? runState.executionPlans : [];
	const latestWorkflow = [...executionPlans].reverse().find((item) => item?.plan?.workflow)?.plan?.workflow;
	const runningTasks = taskValues.filter((task) => task.status === "RUNNING");
	const pendingTasks = taskValues.filter((task) => task.status === "PENDING");
	const activeFrontier = runningTasks.length > 0 ? runningTasks : pendingTasks.filter((task) => (task.dependsOn || []).every((id) => taskValues.find((item) => item.taskId === id)?.status === "COMPLETED"));
	const eventRows = state.events.slice(-10).reverse().map((event) => `<div class="event-row"><span class="event-icon">›</span><span><strong>${escapeHtml(eventLabel(event.type))}</strong><small>${escapeHtml(eventSummary(event))}</small></span><time>${formatTime(event.timestamp)}</time></div>`).join("");
	const tasks = taskValues.length > 0
		? taskValues.map((task) => {
			const candidate = candidates.find((item) => item.taskId === task.taskId);
			const verification = candidate === undefined ? undefined : runState.verifications?.[candidate.candidateId];
			const formalAttempt = [...formalAttempts].reverse().find((attempt) => attempt.taskId === task.taskId || attempt.candidateId === candidate?.candidateId);
			const outcome = task.kind === "FORMALIZATION"
				? formalAttempt === undefined ? taskStatusLabel(task.status) : formalAttempt.result?.ok ? t("leanPassed") : formalAttempt.result?.failureKind ? `LEAN ${formalAttempt.result.failureKind}` : t("leanRejected")
				: verification?.verdict || taskStatusLabel(task.status);
			const dependencies = Array.isArray(task.dependsOn) && task.dependsOn.length > 0 ? t("dependencies", { ids: task.dependsOn.join(", ") }) : t("noDependencies");
			const agent = task.agent?.agentId ? t("agent", { id: task.agent.agentId }) : t("defaultWorker");
			return `<article class="task-row ${taskTone(task.status)}"><div class="task-main"><div class="task-title"><span class="task-kind ${task.kind === "FORMALIZATION" ? "formal" : ""}">${task.kind === "FORMALIZATION" ? "LEAN" : "MATH"}</span><strong>${escapeHtml(task.summary || task.description || t("unnamedTask"))}</strong></div><small>${escapeHtml(task.taskId || "task")} · ${escapeHtml(agent)} · ${escapeHtml(dependencies)}${task.continuationOf ? ` · ${escapeHtml(t("continuation", { id: task.continuationOf }))}` : ""}</small>${task.lastError ? `<p>${escapeHtml(task.lastError)}</p>` : ""}</div><b class="verdict ${formalAttempt?.result?.ok || verification?.verdict === "CORRECT" ? "good" : ""}">${escapeHtml(outcome)}</b></article>`;
		}).join("")
		: `<div class="empty-panel">${escapeHtml(t("emptyTaskGraph"))}</div>`;
	const frontier = activeFrontier.length > 0
		? activeFrontier.map((task) => `<span class="frontier-chip ${task.kind === "FORMALIZATION" ? "formal" : ""}">${escapeHtml(task.summary || task.taskId)}</span>`).join("")
		: `<span class="frontier-empty">${escapeHtml(t("noRunnableFrontier"))}</span>`;
	const formalRows = formalAttempts.length === 0
		? `<div class="empty-panel">${escapeHtml(t("noLeanProcess"))}</div>`
		: formalAttempts.slice().reverse().map((attempt) => `<div class="formal-attempt"><span>#${attempt.attempt} · ${escapeHtml(t("stepLabel"))} ${attempt.step}</span><b class="${attempt.result?.ok ? "good" : ""}">${attempt.result?.ok ? escapeHtml(t("passed")) : escapeHtml(attempt.result?.failureKind || t("rejected"))}</b><p>${escapeHtml(attempt.result?.feedback || t("noFeedback"))}</p></div>`).join("");
  return `<main class="workspace">
		<section class="page-heading"><div><div class="eyebrow">${escapeHtml(t("dynamicProofWorkspace"))}</div><h1>${escapeHtml(t("proofWorkbench"))}</h1><p>${escapeHtml(t("theoremPageDescription"))}</p></div><div class="run-id">${run ? `${escapeHtml(t("runLabel"))} <code>${escapeHtml(run.runId)}</code>` : escapeHtml(t("ready"))}</div></section>
    ${state.error ? `<div class="alert danger-alert" role="alert"><strong>${escapeHtml(t("requestNotCompleted"))}</strong><span>${escapeHtml(state.error)}</span><button data-action="dismiss-error">×</button></div>` : ""}
    ${state.notice ? `<div class="alert notice-alert"><span>${escapeHtml(state.notice)}</span></div>` : ""}
    <section class="composer card">
		<div class="section-kicker">${escapeHtml(t("sectionTheorem"))}</div>
		<label for="theorem-input">${escapeHtml(t("theorem"))}</label>
		<textarea id="theorem-input" class="theorem-input" placeholder="${escapeAttribute(t("theoremPlaceholder"))}">${escapeHtml(state.draftTheorem)}</textarea>
			<label for="context-input">${escapeHtml(t("contextLabel"))} <span>${escapeHtml(t("optional"))}</span></label>
			<textarea id="context-input" class="context-input" placeholder="${escapeAttribute(t("contextPlaceholder"))}">${escapeHtml(state.draftContext)}</textarea>
			${formalizationEnabled ? (state.draftMode === "prove" ? "" : `<div class="formal-policy-note"><strong>${escapeHtml(t("noLeanCodeRequired"))}</strong><span>${escapeHtml(t("formalPolicy"))}</span></div>`) : `<div class="formal-policy-note"><strong>${escapeHtml(t("formalizationDisabled"))}</strong></div>`}
			${formalizationEnabled ? `<div class="lean-project-status ${leanProjectTone}"><div class="lean-project-heading"><span class="subhead">${escapeHtml(t("leanSessionProject"))}</span><span class="status-pill ${leanProjectTone}"><span></span>${escapeHtml(leanProjectLabel)}</span></div><p>${escapeHtml(leanProjectDetails)}</p></div>` : ""}
			<div class="composer-footer"><select id="mode-input" aria-label="${escapeAttribute(t("proofMode"))}"><option value="prove" ${state.draftMode === "prove" ? "selected" : ""}>${escapeHtml(t("prove"))}</option>${formalizationEnabled ? `<option value="prove_and_formalize" ${state.draftMode === "prove_and_formalize" ? "selected" : ""}>${escapeHtml(t("proveAndFormalize"))}</option><option value="formalize_only" ${state.draftMode === "formalize_only" ? "selected" : ""}>${escapeHtml(t("formalizeOnly"))}</option>` : ""}</select><div class="composer-actions"><button class="button secondary" data-action="clear-draft">${escapeHtml(t("clear"))}</button>${run && !isTerminal(status) ? `<button class="button secondary danger-button" data-action="cancel-run">${escapeHtml(t("cancelRun"))}</button>` : ""}<button class="button primary" data-action="submit-proof" ${state.busy ? "disabled" : ""}><span>${escapeHtml(state.busy ? t("processing") : t("startProof"))}</span><b>↗</b></button></div></div>
		</section>
		<div class="dashboard-grid">
			<section class="card progress-card"><div class="card-header"><div><div class="section-kicker">${escapeHtml(t("dynamicWorkflow"))}</div><h2>${escapeHtml(t("controllerFrontier"))}</h2></div><span class="status-text ${statusTone(status)}">${escapeHtml(statusLabel(status))}</span></div><div class="workflow-strategy"><span>${escapeHtml(t("strategy"))}</span><strong>${escapeHtml(latestWorkflow?.strategy || (runState.workflowMode === "dynamic" ? t("waitingStrategy") : t("legacyWorkflow")))}</strong>${latestWorkflow?.rationale ? `<p>${escapeHtml(latestWorkflow.rationale)}</p>` : ""}</div><div class="subhead">${escapeHtml(t("runnableTasks"))}</div><div class="frontier-list">${frontier}</div><div class="metrics"><div><strong>${run?.step || 0}</strong><span>${escapeHtml(t("controllerRounds"))}</span></div><div><strong>${taskValues.length}</strong><span>${escapeHtml(t("dynamicTasks"))}</span></div><div><strong>${activeFrontier.length}</strong><span>${escapeHtml(t("currentFrontier"))}</span></div><div><strong>${formalAttempts.length}</strong><span>${escapeHtml(t("leanAttempts"))}</span></div></div>${eventRows ? `<div class="event-log"><div class="subhead">${escapeHtml(t("recentEvents"))}</div>${eventRows}</div>` : ""}</section>
			<section class="card board-card"><div class="card-header"><div><div class="section-kicker">${escapeHtml(t("taskGraph"))}</div><h2>${escapeHtml(t("dynamicTaskGraph"))}</h2></div><span class="small-tag">${escapeHtml((runState.workflowMode || "dynamic").toUpperCase())}</span></div><div class="task-list">${tasks}</div><div class="subhead board-heading">${escapeHtml(t("researchWhiteboard"))}</div><pre class="whiteboard">${escapeHtml(typeof runState.whiteboard === "string" && runState.whiteboard ? runState.whiteboard : t("whiteboardPlaceholder"))}</pre></section>
		</div>
		${formalizationEnabled ? `<section class="card formal-card"><div class="card-header"><div><div class="section-kicker">${escapeHtml(t("formalGate"))}</div><h2>${escapeHtml(t("leanVerificationRepair"))}</h2></div><span class="small-tag">${escapeHtml(t("process"))}</span></div><div class="formal-attempts">${formalRows}</div></section>` : ""}
		<section class="card answer-card"><div class="card-header"><div><div class="section-kicker">${escapeHtml(t("finalSubmission"))}</div><h2>${escapeHtml(t("finalAnswer"))}</h2></div>${state.result?.status ? `<span class="status-pill ${statusTone(state.result.status)}"><span></span>${escapeHtml(statusLabel(state.result.status))}</span>` : ""}</div>${proof ? `<pre class="proof-output">${escapeHtml(proof)}</pre>` : `<div class="answer-placeholder"><div class="placeholder-mark">∎</div><p>${escapeHtml(run === undefined ? t("proofPlaceholder") : isTerminal(status) ? t("noCompleteProof") : t("workflowInProgress"))}</p></div>`}${formalProof ? `<div class="formal-section"><div class="subhead">${escapeHtml(t("leanFormalResult"))}</div><pre class="proof-output">${escapeHtml(formalProof)}</pre></div>` : ""}</section>
	</main>`;
}

function taskStatusLabel(status) {
	return t(`taskStatus.${status}`) === `taskStatus.${status}` ? status || t("taskStatus.fallback") : t(`taskStatus.${status}`);
}

function taskTone(status) {
	if (status === "RUNNING") return "running";
	if (status === "COMPLETED") return "completed";
	if (status === "FAILED_RETRYABLE" || status === "PARTIAL") return "retryable";
	if (status === "FAILED_TERMINAL" || status === "BLOCKED") return "blocked";
	return "pending";
}

function eventLabel(type) {
	return t(`events.${type}`) === `events.${type}` ? type || t("events.fallback") : t(`events.${type}`);
}

function eventSummary(event) {
	if (event.type === "proof/task_status_changed") return `${event.taskId || "task"}: ${taskStatusLabel(event.status)}`;
	if (event.type === "proof/task_dispatched") return event.task?.summary || event.task?.taskId || t("dynamicTask");
	if (event.type === "proof/verification_result") return event.result?.verdict || event.candidateId || t("verifier");
	if (event.type === "proof/formal_verification_result") return event.result?.ok ? t("leanPassedSummary") : event.result?.failureKind || t("leanRejectedSummary");
	if (event.type === "proof/status_changed") return statusLabel(event.status);
	if (typeof event.summary === "string") return event.summary;
	return event.taskId || event.candidateId || "";
}

function renderSettings() {
  if (state.settingsLoading || state.settings === undefined) {
    return `<main class="workspace settings-view"><section class="page-heading"><div><div class="eyebrow">${escapeHtml(t("configuration"))}</div><h1>${escapeHtml(t("modelsAndSettings"))}</h1><p>${escapeHtml(t("settingsDescription"))}</p></div></section><section class="card loading-card">${escapeHtml(state.settingsLoading ? t("loadingSettings") : t("settingsUnavailable"))}<button class="button secondary" data-action="reload-settings">${escapeHtml(t("reloadSettings"))}</button></section></main>`;
  }
  const settings = state.settings;
  const modelCards = Object.entries(settings.models || {}).map(([name, model]) => {
    const usesCloudIdentity = model.provider === "google-vertex";
    const hasCredential = model.apiKeyEnv !== undefined || usesCloudIdentity;
    const credentialState = usesCloudIdentity ? "configured" : !hasCredential ? "" : model.credentialConfigured ? "configured" : "missing";
    const credentialLabel = usesCloudIdentity ? t("sdkCloudIdentity") : !hasCredential ? t("noKeyRequired") : model.credentialConfigured ? t("envConfigured") : t("waitingEnv");
    return `<article class="model-card"><div class="model-card-header"><div><span class="model-name">${escapeHtml(name)}</span><span class="provider-label">${escapeHtml(model.provider || t("unknown"))}</span></div><span class="credential-state ${credentialState}">${escapeHtml(credentialLabel)}</span></div><div class="model-fields"><label>${escapeHtml(t("provider"))}<input data-model-field="provider" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.provider || "")}" /></label><label>${escapeHtml(t("modelId"))}<input data-model-field="model" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.model || "")}" /></label><label>${escapeHtml(t("baseUrl"))}<input data-model-field="baseUrl" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.baseUrl || "")}" /></label><label>${escapeHtml(t("apiKeyEnv"))}<input data-model-field="apiKeyEnv" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.apiKeyEnv || "")}" /></label><label>${escapeHtml(t("reasoning"))}<select data-model-field="reasoningEffort" data-model-name="${escapeAttribute(name)}"><option value="">${escapeHtml(t("defaultValue"))}</option><option value="low" ${model.reasoningEffort === "low" ? "selected" : ""}>low</option><option value="medium" ${model.reasoningEffort === "medium" ? "selected" : ""}>medium</option><option value="high" ${model.reasoningEffort === "high" ? "selected" : ""}>high</option></select></label><label>${escapeHtml(t("contextWindow"))}<input type="number" data-model-field="contextWindow" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.contextWindow || "")}" /></label><label>${escapeHtml(t("maxTokens"))}<input type="number" data-model-field="maxTokens" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.maxTokens || "")}" /></label><label class="wide-field">${escapeHtml(t("customRequestParameters"))}<textarea class="json-input" data-model-params="${escapeAttribute(name)}" spellcheck="false">${escapeHtml(state.modelParameters[name] || "{}")}</textarea></label></div></article>`;
  }).join("");
  const modelNames = Object.keys(settings.models || {});
  const roles = Object.entries(settings.roles || {}).map(([role, profile]) => `<label class="role-row"><span><strong>${escapeHtml(roleLabel(role))}</strong><small>${escapeHtml(profile.enabled === false ? t("roleDisabled") : t("roleEnabled"))}</small></span><select data-role-model="${escapeAttribute(role)}">${modelNames.map((name) => `<option value="${escapeAttribute(name)}" ${profile.model === name ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}</select></label>`).join("");
  return `<main class="workspace settings-view"><section class="page-heading"><div><div class="eyebrow">${escapeHtml(t("configuration"))}</div><h1>${escapeHtml(t("modelsAndSettings"))}</h1><p>${escapeHtml(t("settingsSecurityDescription"))}</p></div><span class="revision">${escapeHtml(t("revisionLabel"))} ${escapeHtml(String(settings.revision || "").slice(0, 10))}</span></section>${state.error ? `<div class="alert danger-alert" role="alert"><strong>${escapeHtml(t("configurationNotSaved"))}</strong><span>${escapeHtml(state.error)}</span><button data-action="dismiss-error">×</button></div>` : ""}<section class="card settings-card"><div class="card-header"><div><div class="section-kicker">${escapeHtml(t("modelsSection"))}</div><h2>${escapeHtml(t("modelCatalog"))}</h2></div><button class="button primary" data-action="save-settings" ${state.settingsSaving ? "disabled" : ""}>${escapeHtml(state.settingsSaving ? t("saving") : t("saveModelConfig"))}</button></div><div class="model-list">${modelCards}</div></section><section class="card settings-card"><div class="section-kicker">${escapeHtml(t("rolesSection"))}</div><h2>${escapeHtml(t("roleMapping"))}</h2><div class="role-list">${roles}</div></section><section class="card settings-card"><div class="card-header"><div><div class="section-kicker">${escapeHtml(t("sourceSection"))}</div><h2>${escapeHtml(t("advancedToml"))}</h2></div><button class="button secondary" data-action="save-toml" ${state.settingsSaving ? "disabled" : ""}>${escapeHtml(t("saveToml"))}</button></div><textarea id="toml-input" class="toml-input" spellcheck="false">${escapeHtml(state.settingsToml)}</textarea></section></main>`;
}

function roleLabel(role) {
  return t(`roles.${role}`) === `roles.${role}` ? role : t(`roles.${role}`);
}

function formatTime(value) {
  if (typeof value !== "number") return "";
  try { return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch { return ""; }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (target === null) return;
  const action = target.dataset.action;
  if (action === "select-session") void selectSession(target.dataset.sessionId);
  else if (action === "new-session") void createSession();
  else if (action === "view-proof") { state.view = "proof"; render(); }
  else if (action === "view-settings") { state.view = "settings"; render(); void loadSettings(); }
  else if (action === "reload-settings") { state.settings = undefined; void loadSettings(); }
  else if (action === "toggle-language") toggleLanguage();
  else if (action === "toggle-theme") { state.theme = state.theme === "dark" ? "light" : "dark"; localStorage.setItem("math-proof-theme", state.theme); render(); }
  else if (action === "submit-proof") void submitProof();
  else if (action === "cancel-run") void cancelRun();
	else if (action === "clear-draft") { state.draftTheorem = ""; state.draftContext = ""; state.draftLeanTheorem = ""; state.error = ""; render(); }
  else if (action === "dismiss-error") { state.error = ""; render(); }
  else if (action === "save-settings") void saveSettings();
  else if (action === "save-toml") void saveToml();
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (target.id === "theorem-input") state.draftTheorem = target.value;
  else if (target.id === "context-input") state.draftContext = target.value;
	else if (target.id === "lean-theorem-input") state.draftLeanTheorem = target.value;
  else if (target.id === "toml-input") state.settingsToml = target.value;
  else if (target.dataset.modelField !== undefined) updateModel(target.dataset.modelName, target.dataset.modelField, target.value);
  else if (target.dataset.modelParams !== undefined) state.modelParameters[target.dataset.modelParams] = target.value;
});

document.addEventListener("change", (event) => {
  const target = event.target;
	if (target.id === "mode-input") { state.draftMode = target.value; render(); }
  else if (target.dataset.roleModel !== undefined && state.settings?.roles?.[target.dataset.roleModel] !== undefined) state.settings.roles[target.dataset.roleModel].model = target.value;
  else if (target.dataset.modelField !== undefined) updateModel(target.dataset.modelName, target.dataset.modelField, target.value);
});

render();
void loadSessions().catch(showError);
