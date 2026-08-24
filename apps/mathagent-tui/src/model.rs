use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Child;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use serde_json::Value;

use super::{read_snapshot, string_field};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Focus {
    Workspace,
    Transcript,
    Composer,
}

impl Focus {
    pub(super) fn next(self) -> Self {
        match self {
            Self::Workspace => Self::Transcript,
            Self::Transcript => Self::Composer,
            Self::Composer => Self::Workspace,
        }
    }

    pub(super) fn previous(self) -> Self {
        match self {
            Self::Workspace => Self::Composer,
            Self::Transcript => Self::Workspace,
            Self::Composer => Self::Transcript,
        }
    }

    pub(super) fn label(self) -> &'static str {
        match self {
            Self::Workspace => "项目",
            Self::Transcript => "活动",
            Self::Composer => "输入",
        }
    }
}

#[derive(Clone, Copy)]
pub(super) struct CommandSpec {
    pub(super) name: &'static str,
    pub(super) usage: &'static str,
    pub(super) description: &'static str,
}

pub(super) const COMMANDS: &[CommandSpec] = &[
    CommandSpec {
        name: "switch",
        usage: "/switch [path|select]",
        description: "选择或切换已有项目工作区",
    },
    CommandSpec {
        name: "new",
        usage: "/new [id] [purpose]",
        description: "打开大号编辑器，以长目标创建新项目",
    },
    CommandSpec {
        name: "run",
        usage: "/run",
        description: "启动项目级 orchestrator，分析并推进子问题",
    },
    CommandSpec {
        name: "import",
        usage: "/import <file>",
        description: "把论文、Markdown、文本或 PDF 加入当前项目",
    },
    CommandSpec {
        name: "stop",
        usage: "/stop",
        description: "停止当前项目正在运行的证明",
    },
    CommandSpec {
        name: "steps",
        usage: "/steps",
        description: "读取并定位到当前项目的步骤流",
    },
    CommandSpec {
        name: "details",
        usage: "/details",
        description: "切换摘要与完整诊断日志",
    },
    CommandSpec {
        name: "status",
        usage: "/status",
        description: "刷新当前项目状态",
    },
    CommandSpec {
        name: "config",
        usage: "/config <path>",
        description: "切换当前会话的模型配置",
    },
    CommandSpec {
        name: "demo",
        usage: "/demo [path]",
        description: "生成确定性演示项目",
    },
    CommandSpec {
        name: "clear",
        usage: "/clear",
        description: "清空当前项目的会话画布",
    },
    CommandSpec {
        name: "motion",
        usage: "/motion [full|reduced]",
        description: "切换动画强度",
    },
    CommandSpec {
        name: "help",
        usage: "/help",
        description: "显示按键与命令帮助",
    },
    CommandSpec {
        name: "quit",
        usage: "/quit",
        description: "退出 MathAgent",
    },
];

#[derive(Debug)]
pub(super) enum BackendEvent {
    Line {
        project: PathBuf,
        line: String,
        stderr: bool,
    },
    Finished {
        project: PathBuf,
        activity: String,
        success: bool,
        cancelled: bool,
    },
}

#[derive(Debug)]
pub(super) struct UiActivity {
    pub(super) event_id: String,
    pub(super) theorem_id: String,
    pub(super) run_id: String,
    pub(super) role: String,
    pub(super) action: String,
    pub(super) stage: String,
    pub(super) title: String,
    pub(super) summary: String,
    pub(super) status: String,
    pub(super) elapsed_ms: Option<u64>,
    pub(super) started_at: Instant,
    pub(super) updated_at: Instant,
    pub(super) flash_until: Option<Instant>,
    pub(super) error: Option<String>,
    pub(super) diagnostic: Option<String>,
    pub(super) error_action: Option<String>,
    pub(super) retryable: Option<bool>,
    pub(super) artifacts: Vec<String>,
    pub(super) history: Vec<String>,
}

#[derive(Debug)]
pub(super) struct AnimationState {
    pub(super) frame: usize,
    pub(super) last_tick: Instant,
    pub(super) reduced_motion: bool,
    pub(super) ascii_spinner: bool,
}

impl Default for AnimationState {
    fn default() -> Self {
        Self {
            frame: 0,
            last_tick: Instant::now(),
            reduced_motion: false,
            ascii_spinner: false,
        }
    }
}

impl AnimationState {
    const SPINNER: [&'static str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
    const ASCII_SPINNER: [&'static str; 4] = ["|", "/", "-", "\\"];

    pub(super) fn detected() -> Self {
        Self {
            ascii_spinner: matches!(env::var("TERM").as_deref(), Ok("dumb") | Err(_)),
            ..Self::default()
        }
    }

    pub(super) fn tick(&mut self) {
        let now = Instant::now();
        if now.duration_since(self.last_tick) >= Duration::from_millis(90) {
            self.frame = self.frame.wrapping_add(1);
            self.last_tick = now;
        }
    }

    pub(super) fn spinner(&self) -> &'static str {
        if self.reduced_motion {
            "•"
        } else if self.ascii_spinner {
            Self::ASCII_SPINNER[self.frame % Self::ASCII_SPINNER.len()]
        } else {
            Self::SPINNER[self.frame % Self::SPINNER.len()]
        }
    }

    pub(super) fn pulse_style(&self, active: bool) -> Style {
        if !active || self.reduced_motion {
            return Style::default();
        }
        if self.frame % 4 < 2 {
            Style::default().add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::Gray)
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum TranscriptKind {
    User,
    System,
    Activity,
    Success,
    Warning,
    Error,
    Failure,
    Step,
    Output,
}

#[derive(Debug)]
pub(super) struct TranscriptEntry {
    pub(super) kind: TranscriptKind,
    pub(super) text: String,
    pub(super) compact: bool,
}

impl TranscriptEntry {
    pub(super) fn new(kind: TranscriptKind, text: impl Into<String>, compact: bool) -> Self {
        Self {
            kind,
            text: text.into(),
            compact,
        }
    }

    pub(super) fn from_legacy(line: impl Into<String>) -> Self {
        let line = line.into();
        for (prefix, kind, compact) in [
            ("You · ", TranscriptKind::User, true),
            ("System · ", TranscriptKind::System, true),
            ("Error · ", TranscriptKind::Error, true),
            ("Step · ", TranscriptKind::Step, true),
            ("Agent · ", TranscriptKind::Output, false),
        ] {
            if let Some(text) = line.strip_prefix(prefix) {
                return Self::new(kind, text, compact);
            }
        }
        Self::new(TranscriptKind::Output, line, false)
    }
}

#[derive(Debug, Default)]
pub(super) struct ProjectSnapshot {
    pub(super) name: String,
    pub(super) display_title: String,
    pub(super) id: String,
    pub(super) purpose: String,
    pub(super) current_target: String,
    pub(super) theorems: Vec<TheoremRow>,
    pub(super) orchestrator_status: String,
    pub(super) error: Option<String>,
}

#[derive(Debug, Clone)]
pub(super) struct TheoremRow {
    pub(super) id: String,
    pub(super) title: String,
    pub(super) statement: String,
    pub(super) status: String,
    pub(super) dependencies: Vec<String>,
    pub(super) tags: Vec<String>,
    pub(super) source_file: String,
    pub(super) audit_status: String,
    pub(super) last_updated: String,
}

#[derive(Default)]
pub(super) struct ProjectSession {
    pub(super) input: String,
    pub(super) cursor: usize,
    pub(super) history: Vec<String>,
    pub(super) history_index: Option<usize>,
    pub(super) transcript: Vec<TranscriptEntry>,
    pub(super) activities: Vec<UiActivity>,
    pub(super) activity_selected: Option<usize>,
    pub(super) activity_offset: usize,
    pub(super) activity_viewport: usize,
    pub(super) activity_follow: bool,
    pub(super) snapshot: ProjectSnapshot,
    pub(super) theorem_selected: usize,
    pub(super) theorem_scroll: usize,
    pub(super) transcript_offset: usize,
    pub(super) transcript_visual_lines: usize,
    pub(super) transcript_viewport: usize,
    pub(super) follow_transcript: bool,
    pub(super) detail_open: bool,
    pub(super) detail_scroll: usize,
    pub(super) timeline_bytes: u64,
}

impl ProjectSession {
    pub(super) fn new(project: &Path) -> Self {
        let mut session = Self {
            snapshot: read_snapshot(project),
            follow_transcript: true,
            activity_follow: true,
            ..Default::default()
        };
        let project_name = if session.snapshot.display_title.is_empty() {
            if session.snapshot.name.is_empty() {
                "数学研究项目".to_string()
            } else {
                session.snapshot.name.clone()
            }
        } else {
            session.snapshot.display_title.clone()
        };
        session.entry(
            TranscriptKind::System,
            format!("项目已打开 · {project_name}"),
            true,
        );
        session.entry(
            TranscriptKind::System,
            "就绪 · 输入 / 查看命令 · 点击左侧子命题查看详情",
            true,
        );
        session.load_project_history(project);
        session
    }

    pub(super) fn load_project_history(&mut self, project: &Path) {
        let timeline = project.join("timeline.jsonl");
        if timeline.is_file() {
            self.load_timeline(&timeline);
        } else {
            self.load_ui_events(project);
        }
    }

    pub(super) fn load_timeline(&mut self, path: &Path) {
        let Ok(metadata) = fs::metadata(path) else {
            return;
        };
        let length = metadata.len();
        if length == self.timeline_bytes {
            return;
        }
        let Ok(body) = fs::read_to_string(path) else {
            return;
        };
        for line in body
            .lines()
            .rev()
            .take(500)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            if let Ok(value) = serde_json::from_str::<Value>(line) {
                self.apply_timeline_event(&value);
            }
        }
        self.timeline_bytes = length;
        self.activity_follow = true;
    }

    pub(super) fn load_ui_events(&mut self, project: &Path) {
        let path = project.join("logs").join("ui-events.jsonl");
        let Ok(body) = fs::read_to_string(path) else {
            return;
        };
        for line in body
            .lines()
            .rev()
            .take(240)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            if let Ok(value) = serde_json::from_str::<Value>(line) {
                self.apply_ui_event(&value);
            }
        }
        self.activity_follow = true;
    }

    pub(super) fn apply_timeline_event(&mut self, value: &Value) {
        let kind = string_field(value, "kind");
        let payload = value.get("payload").cloned().unwrap_or(Value::Null);
        let event_type = payload
            .get("event_type")
            .and_then(Value::as_str)
            .unwrap_or("");
        let action = {
            let action = string_field(value, "action");
            if action.is_empty() {
                payload
                    .get("type")
                    .and_then(Value::as_str)
                    .unwrap_or("timeline_event")
                    .to_string()
            } else {
                action
            }
        };
        let theorem_id = {
            let theorem_id = string_field(value, "theorem_id");
            if theorem_id.is_empty() {
                payload
                    .get("obligation_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string()
            } else {
                theorem_id
            }
        };
        let title = payload
            .get("title")
            .and_then(Value::as_str)
            .filter(|text| !text.is_empty())
            .map(ToOwned::to_owned)
            .unwrap_or_else(|| {
                if theorem_id.is_empty() {
                    action.clone()
                } else {
                    format!("{} · {}", action, theorem_id)
                }
            });
        let summary = {
            let summary = string_field(value, "summary");
            if summary.is_empty() {
                if kind == "PIPELINE_EVENT" {
                    format!("{} · {}", action, theorem_id)
                } else {
                    payload
                        .get("summary")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string()
                }
            } else {
                summary
            }
        };
        let normalized = serde_json::json!({
            "event_type": if event_type.is_empty() { "research_ui_event" } else { event_type },
            "event_id": string_field(value, "event_id"),
            "project_id": string_field(value, "project_id"),
            "theorem_id": theorem_id,
            "run_id": string_field(value, "run_id"),
            "role": string_field(value, "role"),
            "action": action,
            "stage": string_field(value, "stage"),
            "title": title,
            "summary": summary,
            "status": string_field(value, "status"),
            "elapsed_ms": payload.get("elapsed_ms").cloned().unwrap_or(Value::Null),
            "artifacts": value.get("artifacts").cloned().unwrap_or_else(|| serde_json::json!([])),
            "error": payload.get("error").cloned().unwrap_or(Value::Null),
        });
        self.apply_ui_event(&normalized);
    }

    pub(super) fn log(&mut self, message: impl Into<String>) {
        self.transcript.extend(
            message
                .into()
                .lines()
                .map(|line| TranscriptEntry::from_legacy(line.to_owned())),
        );
    }

    pub(super) fn entry(&mut self, kind: TranscriptKind, text: impl Into<String>, compact: bool) {
        self.transcript
            .push(TranscriptEntry::new(kind, text, compact));
    }

    pub(super) fn apply_ui_event(&mut self, value: &Value) {
        if string_field(value, "event_type") != "research_ui_event" {
            return;
        }
        let event_id = string_field(value, "event_id");
        if event_id.is_empty() {
            return;
        }
        let now = Instant::now();
        let was_following = self.activity_follow;
        let status = string_field(value, "status");
        let summary = string_field(value, "summary");
        let index = self
            .activities
            .iter()
            .position(|item| item.event_id == event_id);
        let item = UiActivity {
            event_id: event_id.clone(),
            theorem_id: string_field(value, "theorem_id"),
            run_id: string_field(value, "run_id"),
            role: string_field(value, "role"),
            action: string_field(value, "action"),
            stage: string_field(value, "stage"),
            title: string_field(value, "title"),
            summary: summary.clone(),
            status: status.clone(),
            elapsed_ms: value.get("elapsed_ms").and_then(Value::as_u64),
            started_at: now,
            updated_at: now,
            flash_until: Some(now + Duration::from_millis(1200)),
            error: value
                .get("error")
                .and_then(|error| error.get("message"))
                .and_then(Value::as_str)
                .map(ToOwned::to_owned),
            diagnostic: value
                .get("error")
                .and_then(|error| error.get("diagnostic"))
                .and_then(Value::as_str)
                .map(ToOwned::to_owned),
            error_action: value
                .get("error")
                .and_then(|error| error.get("action"))
                .and_then(Value::as_str)
                .map(ToOwned::to_owned),
            retryable: value
                .get("error")
                .and_then(|error| error.get("retryable"))
                .and_then(Value::as_bool),
            artifacts: value
                .get("artifacts")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(ToOwned::to_owned)
                        .collect()
                })
                .unwrap_or_default(),
            history: vec![format!(
                "{}{}",
                status,
                if summary.is_empty() {
                    String::new()
                } else {
                    format!("：{}", summary)
                }
            )],
        };
        if let Some(index) = index {
            let existing = &mut self.activities[index];
            let changed = existing.status != item.status || existing.summary != item.summary;
            if changed {
                existing.history.push(item.history[0].clone());
                if existing.history.len() > 8 {
                    existing.history.remove(0);
                }
            }
            existing.theorem_id = item.theorem_id;
            existing.run_id = item.run_id;
            existing.role = item.role;
            existing.action = item.action;
            existing.stage = item.stage;
            existing.title = item.title;
            existing.summary = item.summary;
            existing.status = item.status;
            existing.elapsed_ms = item.elapsed_ms;
            existing.updated_at = now;
            if changed {
                existing.flash_until = item.flash_until;
            }
            existing.error = item.error;
            existing.diagnostic = item.diagnostic;
            existing.error_action = item.error_action;
            existing.retryable = item.retryable;
            existing.artifacts = item.artifacts;
            self.activity_selected = Some(index);
        } else {
            self.activities.push(item);
            self.activity_selected = Some(self.activities.len() - 1);
        }
        if was_following {
            self.activity_follow = true;
        }
    }
}

pub(super) struct RunningTask {
    pub(super) started_at: Instant,
    pub(super) child: Arc<Mutex<Option<Child>>>,
    pub(super) cancelled: Arc<AtomicBool>,
}

#[derive(Clone)]
pub(super) struct ProjectChoice {
    pub(super) path: PathBuf,
    pub(super) id: String,
    pub(super) name: String,
    pub(super) target: String,
}

pub(super) struct ProjectPicker {
    pub(super) choices: Vec<ProjectChoice>,
    pub(super) query: String,
    pub(super) selected: usize,
    pub(super) scroll: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum ProjectEditorField {
    Id,
    Goal,
}

pub(super) struct ProjectGoalEditor {
    pub(super) id: String,
    pub(super) id_cursor: usize,
    pub(super) goal: String,
    pub(super) goal_cursor: usize,
    pub(super) field: ProjectEditorField,
    pub(super) goal_scroll: usize,
}

impl ProjectGoalEditor {
    pub(super) fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            id_cursor: 0,
            goal: String::new(),
            goal_cursor: 0,
            field: ProjectEditorField::Goal,
            goal_scroll: 0,
        }
    }
}

impl ProjectPicker {
    pub(super) fn filtered(&self) -> Vec<&ProjectChoice> {
        let query = self.query.to_lowercase();
        self.choices
            .iter()
            .filter(|choice| {
                query.is_empty()
                    || choice.name.to_lowercase().contains(&query)
                    || choice.id.to_lowercase().contains(&query)
                    || choice
                        .path
                        .to_string_lossy()
                        .to_lowercase()
                        .contains(&query)
            })
            .collect()
    }
}

#[derive(Default)]
pub(super) struct UiRegions {
    pub(super) workspace: Rect,
    pub(super) theorem_list: Rect,
    pub(super) transcript: Rect,
    pub(super) composer: Rect,
    pub(super) completion: Rect,
    pub(super) picker: Rect,
    pub(super) project_editor: Rect,
    pub(super) editor_id: Rect,
    pub(super) editor_goal: Rect,
}
