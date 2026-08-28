const runtime = window.__MATH_PROOF_RUNTIME__ || {};
const apiOrigin = typeof runtime.apiOrigin === "string" ? runtime.apiOrigin.replace(/\/$/u, "") : "";
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
  sessions: [],
  activeSessionId: undefined,
  session: undefined,
  run: undefined,
  result: undefined,
  events: [],
  draftTheorem: "",
  draftContext: "",
	draftLeanTheorem: "",
	draftMode: "prove_and_formalize",
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
    throw new Error(`无法连接证明 API：${error instanceof Error ? error.message : String(error)}`);
  }
  const text = await response.text();
  let payload;
  try {
    payload = text.length === 0 ? undefined : JSON.parse(text);
  } catch {
    payload = undefined;
  }
  if (!response.ok) {
    throw new Error(payload?.error?.message || `Proof API 请求失败（${response.status}）`);
  }
  return payload;
}

function isTerminal(status) {
  return terminalStatuses.has(status);
}

function statusLabel(status) {
  return {
    OPEN: "待提交",
    RUNNING: "运行中",
    CANDIDATE_READY: "候选待提交",
    PROVED: "已证明",
    PARTIAL: "部分完成",
    FAILED: "失败",
		BLOCKED_FORMAL: "形式化工具阻塞",
    BLOCKED_PROVIDER: "Provider 阻塞",
    CANCELLED: "已取消",
  }[status] || status || "待提交";
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
  const value = await request("/v1/sessions");
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
		state.draftMode = value?.mode || "prove_and_formalize";
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
    showNotice("已创建新的证明会话");
  } catch (error) {
    showError(error);
  } finally {
    state.busy = false;
    render();
  }
}

async function submitProof() {
  if (state.draftTheorem.trim().length === 0) {
    state.error = "请先输入数学命题";
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
    showNotice("证明运行已启动");
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
    showNotice("已请求取消当前运行");
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
        ...(model.serviceAccountFileEnv ? { serviceAccountFileEnv: model.serviceAccountFileEnv } : {}),
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
    showNotice("模型与角色配置已保存");
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
    showNotice("TOML 配置已保存");
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
    ? `<div class="empty-sidebar">还没有证明会话<br><span>创建一个命题开始研究</span></div>`
    : state.sessions.map((session) => {
      const active = session.sessionId === state.activeSessionId ? " active" : "";
      const status = session.latestStatus || "OPEN";
      return `<button class="session-row${active}" data-action="select-session" data-session-id="${escapeAttribute(session.sessionId)}">
        <span class="status-dot ${statusTone(status)}"></span>
        <span class="session-copy"><strong>${escapeHtml(session.theorem || "未输入命题")}</strong><small>${escapeHtml(statusLabel(status))} · ${session.runCount || 0} 次运行</small></span>
      </button>`;
    }).join("");
  return `<aside class="sidebar">
    <div class="brand"><div class="brand-mark">∑</div><div><strong>MATH LAB</strong><small>Proof-first research</small></div></div>
    <nav class="main-nav" aria-label="主导航">
      <button class="nav-item${state.view === "proof" ? " selected" : ""}" data-action="view-proof"><span>⌘</span>证明工作台</button>
      <button class="nav-item${state.view === "settings" ? " selected" : ""}" data-action="view-settings"><span>⚙</span>模型与设置</button>
    </nav>
    <div class="sidebar-label"><span>证明会话</span><button class="icon-button" data-action="new-session" aria-label="新建会话">＋</button></div>
    <div class="session-list">${rows}</div>
    <button class="new-session" data-action="new-session" ${state.busy ? "disabled" : ""}>＋ 新建证明</button>
    <div class="sidebar-footer"><span class="online-dot"></span> Proof API ${apiOrigin ? "已连接" : "同源连接"}</div>
  </aside>`;
}

function renderTopbar() {
  const status = state.view === "proof" ? (state.run?.status || "OPEN") : "";
  return `<header class="topbar">
    <div class="breadcrumb"><span>Math Research Agent</span><i>/</i><strong>${state.view === "settings" ? "模型与设置" : "证明工作台"}</strong></div>
    <div class="top-actions">
      ${state.view === "proof" && status ? `<span class="status-pill ${statusTone(status)}"><span></span>${escapeHtml(statusLabel(status))}</span>` : ""}
      <button class="theme-toggle" data-action="toggle-theme" title="切换主题">${state.theme === "dark" ? "☼" : "☾"}</button>
    </div>
  </header>`;
}

function renderProof() {
  const run = state.run;
  const runState = run?.state || {};
  const status = run?.status || "OPEN";
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
				? formalAttempt === undefined ? taskStatusLabel(task.status) : formalAttempt.result?.ok ? "LEAN PASSED" : `LEAN ${formalAttempt.result?.failureKind || "REJECTED"}`
				: verification?.verdict || taskStatusLabel(task.status);
			const dependencies = Array.isArray(task.dependsOn) && task.dependsOn.length > 0 ? `依赖 ${task.dependsOn.join(", ")}` : "无前置依赖";
			const agent = task.agent?.agentId ? `Agent ${task.agent.agentId}` : "默认 Worker";
			return `<article class="task-row ${taskTone(task.status)}"><div class="task-main"><div class="task-title"><span class="task-kind ${task.kind === "FORMALIZATION" ? "formal" : ""}">${task.kind === "FORMALIZATION" ? "LEAN" : "MATH"}</span><strong>${escapeHtml(task.summary || task.description || "未命名任务")}</strong></div><small>${escapeHtml(task.taskId || "task")} · ${escapeHtml(agent)} · ${escapeHtml(dependencies)}${task.continuationOf ? ` · 续接 ${escapeHtml(task.continuationOf)}` : ""}</small>${task.lastError ? `<p>${escapeHtml(task.lastError)}</p>` : ""}</div><b class="verdict ${formalAttempt?.result?.ok || verification?.verdict === "CORRECT" ? "good" : ""}">${escapeHtml(outcome)}</b></article>`;
		}).join("")
		: `<div class="empty-panel">Controller 生成动态任务后，任务图会显示在这里。</div>`;
	const frontier = activeFrontier.length > 0
		? activeFrontier.map((task) => `<span class="frontier-chip ${task.kind === "FORMALIZATION" ? "formal" : ""}">${escapeHtml(task.summary || task.taskId)}</span>`).join("")
		: `<span class="frontier-empty">当前没有可运行 frontier</span>`;
	const formalRows = formalAttempts.length === 0
		? `<div class="empty-panel">尚未调用 Lean 进程门。</div>`
		: formalAttempts.slice().reverse().map((attempt) => `<div class="formal-attempt"><span>#${attempt.attempt} · step ${attempt.step}</span><b class="${attempt.result?.ok ? "good" : ""}">${attempt.result?.ok ? "PASSED" : escapeHtml(attempt.result?.failureKind || "REJECTED")}</b><p>${escapeHtml(attempt.result?.feedback || "无反馈")}</p></div>`).join("");
  return `<main class="workspace">
		<section class="page-heading"><div><div class="eyebrow">DYNAMIC PROOF WORKSPACE</div><h1>数学证明工作台</h1><p>Controller 动态拆解任务图；Worker、Verifier 与 Lean 修复回合按真实后端状态推进。</p></div><div class="run-id">${run ? `RUN <code>${escapeHtml(run.runId)}</code>` : "READY"}</div></section>
    ${state.error ? `<div class="alert danger-alert" role="alert"><strong>请求未完成</strong><span>${escapeHtml(state.error)}</span><button data-action="dismiss-error">×</button></div>` : ""}
    ${state.notice ? `<div class="alert notice-alert"><span>${escapeHtml(state.notice)}</span></div>` : ""}
    <section class="composer card">
      <div class="section-kicker">01 / THEOREM</div>
      <label for="theorem-input">数学命题</label>
      <textarea id="theorem-input" class="theorem-input" placeholder="例如：对所有 n ≥ 1，1 + 3 + ... + (2n - 1) = n²。">${escapeHtml(state.draftTheorem)}</textarea>
			<label for="context-input">假设与证明要求 <span>可选</span></label>
			<textarea id="context-input" class="context-input" placeholder="例如：请使用数学归纳法，并明确说明边界条件。">${escapeHtml(state.draftContext)}</textarea>
			${state.draftMode === "prove" ? "" : `<div class="formal-policy-note"><strong>无需填写 Lean 代码</strong><span>Formalizer 会根据上面的数学命题生成完整 Lean 4 源码；runtime 只在本地 Lean 进程通过后才会结束。编译失败会自动带着反馈回到修复任务。</span></div>`}
			<div class="composer-footer"><select id="mode-input" aria-label="证明模式"><option value="prove" ${state.draftMode === "prove" ? "selected" : ""}>证明</option><option value="prove_and_formalize" ${state.draftMode === "prove_and_formalize" ? "selected" : ""}>证明并形式化</option><option value="formalize_only" ${state.draftMode === "formalize_only" ? "selected" : ""}>仅形式化</option></select><div class="composer-actions"><button class="button secondary" data-action="clear-draft">清空</button>${run && !isTerminal(status) ? `<button class="button secondary danger-button" data-action="cancel-run">取消运行</button>` : ""}<button class="button primary" data-action="submit-proof" ${state.busy ? "disabled" : ""}><span>${state.busy ? "处理中…" : "开始证明"}</span><b>↗</b></button></div></div>
		</section>
		<div class="dashboard-grid">
			<section class="card progress-card"><div class="card-header"><div><div class="section-kicker">02 / DYNAMIC WORKFLOW</div><h2>Controller 与当前 Frontier</h2></div><span class="status-text ${statusTone(status)}">${escapeHtml(statusLabel(status))}</span></div><div class="workflow-strategy"><span>策略</span><strong>${escapeHtml(latestWorkflow?.strategy || (runState.workflowMode === "dynamic" ? "等待 Controller 生成本轮策略" : "Legacy workflow"))}</strong>${latestWorkflow?.rationale ? `<p>${escapeHtml(latestWorkflow.rationale)}</p>` : ""}</div><div class="subhead">当前可运行任务</div><div class="frontier-list">${frontier}</div><div class="metrics"><div><strong>${run?.step || 0}</strong><span>Controller 回合</span></div><div><strong>${taskValues.length}</strong><span>动态任务</span></div><div><strong>${activeFrontier.length}</strong><span>当前 Frontier</span></div><div><strong>${formalAttempts.length}</strong><span>Lean 尝试</span></div></div>${eventRows ? `<div class="event-log"><div class="subhead">最近事件</div>${eventRows}</div>` : ""}</section>
			<section class="card board-card"><div class="card-header"><div><div class="section-kicker">03 / TASK GRAPH</div><h2>动态任务图</h2></div><span class="small-tag">${escapeHtml((runState.workflowMode || "dynamic").toUpperCase())}</span></div><div class="task-list">${tasks}</div><div class="subhead board-heading">研究白板</div><pre class="whiteboard">${escapeHtml(typeof runState.whiteboard === "string" && runState.whiteboard ? runState.whiteboard : "Controller 的研究白板会显示在这里。")}</pre></section>
		</div>
		<section class="card formal-card"><div class="card-header"><div><div class="section-kicker">04 / FORMAL GATE</div><h2>Lean 验证与修复回合</h2></div><span class="small-tag">PROCESS</span></div><div class="formal-attempts">${formalRows}</div></section>
		<section class="card answer-card"><div class="card-header"><div><div class="section-kicker">05 / SUBMISSION</div><h2>最终答案</h2></div>${state.result?.status ? `<span class="status-pill ${statusTone(state.result.status)}"><span></span>${escapeHtml(statusLabel(state.result.status))}</span>` : ""}</div>${proof ? `<pre class="proof-output">${escapeHtml(proof)}</pre>` : `<div class="answer-placeholder"><div class="placeholder-mark">∎</div><p>${run === undefined ? "提交一个命题后，完整证明会显示在这里。" : isTerminal(status) ? "本次运行没有生成完整证明文本。" : "动态 Workflow 正在推进，最终状态必须通过对应提交门。"}</p></div>`}${formalProof ? `<div class="formal-section"><div class="subhead">Lean 形式化结果</div><pre class="proof-output">${escapeHtml(formalProof)}</pre></div>` : ""}</section>
	</main>`;
}

function taskStatusLabel(status) {
	return { PENDING: "等待依赖", RUNNING: "运行中", COMPLETED: "已完成", PARTIAL: "待续接", FAILED_RETRYABLE: "待修复", FAILED_TERMINAL: "终止失败", BLOCKED: "阻塞" }[status] || status || "未知";
}

function taskTone(status) {
	if (status === "RUNNING") return "running";
	if (status === "COMPLETED") return "completed";
	if (status === "FAILED_RETRYABLE" || status === "PARTIAL") return "retryable";
	if (status === "FAILED_TERMINAL" || status === "BLOCKED") return "blocked";
	return "pending";
}

function eventLabel(type) {
	return {
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
	}[type] || type || "Proof event";
}

function eventSummary(event) {
	if (event.type === "proof/task_status_changed") return `${event.taskId || "task"}: ${taskStatusLabel(event.status)}`;
	if (event.type === "proof/task_dispatched") return event.task?.summary || event.task?.taskId || "动态任务";
	if (event.type === "proof/verification_result") return event.result?.verdict || event.candidateId || "Verifier";
	if (event.type === "proof/formal_verification_result") return event.result?.ok ? "Lean passed" : event.result?.failureKind || "Lean rejected";
	if (event.type === "proof/status_changed") return statusLabel(event.status);
	if (typeof event.summary === "string") return event.summary;
	return event.taskId || event.candidateId || "";
}

function renderSettings() {
  if (state.settingsLoading || state.settings === undefined) {
    return `<main class="workspace settings-view"><section class="page-heading"><div><div class="eyebrow">CONFIGURATION</div><h1>模型与设置</h1><p>证明角色和 Provider 配置由同一个 TOML 服务管理。</p></div></section><section class="card loading-card">${state.settingsLoading ? "正在读取 math-agent.toml…" : "配置不可用，请重新加载。"}<button class="button secondary" data-action="reload-settings">重新加载</button></section></main>`;
  }
  const settings = state.settings;
  const modelCards = Object.entries(settings.models || {}).map(([name, model]) => { const hasCredential = model.apiKeyEnv !== undefined || model.serviceAccountFileEnv !== undefined; const credentialLabel = !hasCredential ? "无需密钥" : model.credentialConfigured ? "环境变量已配置" : "等待环境变量"; return `<article class="model-card"><div class="model-card-header"><div><span class="model-name">${escapeHtml(name)}</span><span class="provider-label">${escapeHtml(model.provider || "unknown")}</span></div><span class="credential-state ${!hasCredential ? "" : model.credentialConfigured ? "configured" : "missing"}">${credentialLabel}</span></div><div class="model-fields"><label>Provider<input data-model-field="provider" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.provider || "")}" /></label><label>Model ID<input data-model-field="model" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.model || "")}" /></label><label>Base URL<input data-model-field="baseUrl" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.baseUrl || "")}" /></label><label>API Key 环境变量<input data-model-field="apiKeyEnv" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.apiKeyEnv || "")}" /></label><label>Service Account 文件环境变量<input data-model-field="serviceAccountFileEnv" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.serviceAccountFileEnv || "")}" /></label><label>Reasoning<select data-model-field="reasoningEffort" data-model-name="${escapeAttribute(name)}"><option value="">默认</option><option value="low" ${model.reasoningEffort === "low" ? "selected" : ""}>low</option><option value="medium" ${model.reasoningEffort === "medium" ? "selected" : ""}>medium</option><option value="high" ${model.reasoningEffort === "high" ? "selected" : ""}>high</option></select></label><label>Context window<input type="number" data-model-field="contextWindow" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.contextWindow || "")}" /></label><label>Max tokens<input type="number" data-model-field="maxTokens" data-model-name="${escapeAttribute(name)}" value="${escapeAttribute(model.maxTokens || "")}" /></label><label class="wide-field">自定义请求参数（JSON）<textarea class="json-input" data-model-params="${escapeAttribute(name)}" spellcheck="false">${escapeHtml(state.modelParameters[name] || "{}")}</textarea></label></div></article>`; }).join("");
  const modelNames = Object.keys(settings.models || {});
  const roles = Object.entries(settings.roles || {}).map(([role, profile]) => `<label class="role-row"><span><strong>${escapeHtml(roleLabel(role))}</strong><small>${profile.enabled === false ? "已关闭" : "新运行启用"}</small></span><select data-role-model="${escapeAttribute(role)}">${modelNames.map((name) => `<option value="${escapeAttribute(name)}" ${profile.model === name ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}</select></label>`).join("");
  return `<main class="workspace settings-view"><section class="page-heading"><div><div class="eyebrow">CONFIGURATION</div><h1>模型与设置</h1><p>每个 Proof Run 在启动时固定一份配置快照。密钥只通过环境变量读取，不会显示在页面或 API 响应中。</p></div><span class="revision">REV ${escapeHtml(String(settings.revision || "").slice(0, 10))}</span></section>${state.error ? `<div class="alert danger-alert" role="alert"><strong>配置未保存</strong><span>${escapeHtml(state.error)}</span><button data-action="dismiss-error">×</button></div>` : ""}<section class="card settings-card"><div class="card-header"><div><div class="section-kicker">01 / MODELS</div><h2>模型目录</h2></div><button class="button primary" data-action="save-settings" ${state.settingsSaving ? "disabled" : ""}>${state.settingsSaving ? "保存中…" : "保存模型配置"}</button></div><div class="model-list">${modelCards}</div></section><section class="card settings-card"><div class="section-kicker">02 / ROLES</div><h2>证明角色映射</h2><div class="role-list">${roles}</div></section><section class="card settings-card"><div class="card-header"><div><div class="section-kicker">03 / SOURCE</div><h2>高级 TOML</h2></div><button class="button secondary" data-action="save-toml" ${state.settingsSaving ? "disabled" : ""}>保存 TOML</button></div><textarea id="toml-input" class="toml-input" spellcheck="false">${escapeHtml(state.settingsToml)}</textarea></section></main>`;
}

function roleLabel(role) {
  return { planner: "Planner：工作流规划", worker: "Worker：数学探索", verifier: "Verifier：独立审计", synthesizer: "Synthesizer：答案组织", formalizer: "Formalizer：Lean 形式化", literature_researcher: "Literature Researcher：文献上下文" }[role] || role;
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
