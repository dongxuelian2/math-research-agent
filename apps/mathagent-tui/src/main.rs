use std::collections::HashMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Stdout, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use crossterm::event::{
    self, DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture,
    Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Clear, Paragraph, Wrap};
use ratatui::{Frame, Terminal};
use serde_json::Value;
use unicode_width::UnicodeWidthChar;

const SCROLL_STEP: usize = 3;
const MAX_VISIBLE_COMPLETIONS: usize = 7;

// Keep the entry point focused on wiring and application lifecycle. Feature
// specific state, input, backend process handling, and rendering live in the
// sibling modules below so new agent capabilities do not grow this file again.
mod backend;
mod input;
mod model;
mod ui;

use backend::stop_child;
use model::*;
use ui::*;

struct App {
    root: PathBuf,
    project: PathBuf,
    config: PathBuf,
    sessions: HashMap<PathBuf, ProjectSession>,
    running: HashMap<PathBuf, RunningTask>,
    tx: Sender<BackendEvent>,
    rx: Receiver<BackendEvent>,
    focus: Focus,
    completion_index: usize,
    completion_hidden: bool,
    project_picker: Option<ProjectPicker>,
    project_editor: Option<ProjectGoalEditor>,
    show_help: bool,
    help_scroll: u16,
    transcript_fullscreen: bool,
    regions: UiRegions,
    should_quit: bool,
    animation: AnimationState,
    render_dirty: bool,
    transcript_cache: Option<TranscriptCache>,
    next_disk_refresh: Instant,
}

struct TranscriptCache {
    project: PathBuf,
    width: usize,
    full: bool,
    lines: Vec<TranscriptLine>,
}

impl Drop for App {
    fn drop(&mut self) {
        for task in self.running.values() {
            task.cancelled.store(true, Ordering::SeqCst);
            if let Some(child) = task.child.lock().expect("child lock").as_mut() {
                let _ = stop_child(child);
            }
        }
    }
}

impl App {
    fn new(root: PathBuf, project: PathBuf, config: PathBuf) -> Self {
        let (tx, rx) = mpsc::channel();
        let mut sessions = HashMap::new();
        sessions.insert(project.clone(), ProjectSession::new(&project));
        Self {
            root,
            project,
            config,
            sessions,
            running: HashMap::new(),
            tx,
            rx,
            focus: Focus::Composer,
            completion_index: 0,
            completion_hidden: false,
            project_picker: None,
            project_editor: None,
            show_help: false,
            help_scroll: 0,
            transcript_fullscreen: false,
            regions: UiRegions::default(),
            should_quit: false,
            animation: AnimationState::detected(),
            render_dirty: true,
            transcript_cache: None,
            next_disk_refresh: Instant::now(),
        }
    }

    fn invalidate(&mut self) {
        self.render_dirty = true;
        self.transcript_cache = None;
    }

    fn request_render(&mut self) {
        self.render_dirty = true;
    }

    fn animation_needed(&self) -> bool {
        self.running.contains_key(&self.project)
            || self.session().activities.iter().any(|activity| {
                matches!(activity.status.as_str(), "STARTED" | "PROGRESS")
                    || activity
                        .flash_until
                        .is_some_and(|until| until > Instant::now())
            })
    }

    fn session(&self) -> &ProjectSession {
        self.sessions
            .get(&self.project)
            .expect("current project session")
    }

    fn session_mut(&mut self) -> &mut ProjectSession {
        self.sessions
            .get_mut(&self.project)
            .expect("current project session")
    }

    fn log(&mut self, message: impl Into<String>) {
        self.session_mut().log(message);
    }

    fn refresh_snapshot(&mut self) {
        let snapshot = read_snapshot(&self.project);
        let project = self.project.clone();
        let session = self.session_mut();
        session.snapshot = snapshot;
        session.load_project_history(&project);
        session.remember_snapshot_signatures(&project);
        self.invalidate();
    }

    fn refresh_snapshot_if_changed(&mut self) {
        let now = Instant::now();
        if now < self.next_disk_refresh {
            return;
        }
        self.next_disk_refresh = now
            + if self.running.contains_key(&self.project) {
                Duration::from_millis(120)
            } else {
                Duration::from_millis(600)
            };
        let project = self.project.clone();
        if self.session_mut().refresh_from_disk(&project) {
            self.invalidate();
        }
    }

    fn switch_project(&mut self, path: PathBuf) {
        let path = normalize_path(&path);
        self.sessions
            .entry(path.clone())
            .or_insert_with(|| ProjectSession::new(&path));
        self.project = path;
        self.refresh_snapshot();
        self.focus = Focus::Composer;
        self.completion_hidden = false;
        self.project_picker = None;
        self.project_editor = None;
    }

    fn open_project_picker(&mut self) {
        let choices = discover_projects(&self.root);
        self.project_picker = Some(ProjectPicker {
            choices,
            query: String::new(),
            selected: 0,
            scroll: 0,
        });
    }

    fn open_project_editor(&mut self, id: impl Into<String>) {
        self.project_picker = None;
        self.project_editor = Some(ProjectGoalEditor::new(id));
        self.completion_hidden = true;
    }

    fn completion_candidates(&self) -> Vec<&'static CommandSpec> {
        if self.completion_hidden {
            return Vec::new();
        }
        command_candidates(&self.session().input)
    }

    fn normalize_completion(&mut self) {
        let len = self.completion_candidates().len();
        self.completion_index = if len == 0 {
            0
        } else {
            self.completion_index.min(len - 1)
        };
    }

    fn apply_completion(&mut self) {
        let candidates = self.completion_candidates();
        let Some(command) = candidates.get(self.completion_index) else {
            return;
        };
        let value = format!("/{} ", command.name);
        let cursor = value.chars().count();
        let session = self.session_mut();
        session.input = value;
        session.cursor = cursor;
        session.invalidate_input_layout();
        self.completion_hidden = true;
    }

    fn edit_input(&mut self, edit: impl FnOnce(&mut String, &mut usize)) {
        let session = self.session_mut();
        edit(&mut session.input, &mut session.cursor);
        session.invalidate_input_layout();
        session.history_index = None;
        self.completion_hidden = false;
        self.completion_index = 0;
        self.normalize_completion();
    }

    fn toggle_transcript(&mut self) {
        self.transcript_fullscreen = !self.transcript_fullscreen;
        self.focus = if self.transcript_fullscreen {
            Focus::Transcript
        } else {
            Focus::Composer
        };
        self.session_mut().follow_transcript = true;
    }
}

fn command_candidates(input: &str) -> Vec<&'static CommandSpec> {
    if !input.starts_with('/') || input.contains(char::is_whitespace) {
        return Vec::new();
    }
    let query = input.trim_start_matches('/').to_lowercase();
    COMMANDS
        .iter()
        .filter(|command| command.name.contains(&query))
        .collect()
}

fn char_to_byte(value: &str, char_index: usize) -> usize {
    value
        .char_indices()
        .nth(char_index)
        .map(|(index, _)| index)
        .unwrap_or(value.len())
}

fn insert_char(input: &mut String, cursor: &mut usize, ch: char) {
    let index = char_to_byte(input, *cursor);
    input.insert(index, ch);
    *cursor += 1;
}
fn insert_text(input: &mut String, cursor: &mut usize, value: &str) {
    let index = char_to_byte(input, *cursor);
    input.insert_str(index, value);
    *cursor += value.chars().count();
}
fn backspace(input: &mut String, cursor: &mut usize) {
    if *cursor == 0 {
        return;
    }
    let start = char_to_byte(input, *cursor - 1);
    let end = char_to_byte(input, *cursor);
    input.replace_range(start..end, "");
    *cursor -= 1;
}
fn delete_at_cursor(input: &mut String, cursor: &mut usize) {
    let start = char_to_byte(input, *cursor);
    let end = char_to_byte(input, *cursor + 1);
    if start < end {
        input.replace_range(start..end, "");
    }
}
fn delete_previous_word(input: &mut String, cursor: &mut usize) {
    while *cursor > 0
        && input
            .chars()
            .nth(*cursor - 1)
            .is_some_and(char::is_whitespace)
    {
        backspace(input, cursor);
    }
    while *cursor > 0
        && input
            .chars()
            .nth(*cursor - 1)
            .is_some_and(|ch| !ch.is_whitespace())
    {
        backspace(input, cursor);
    }
}
fn line_start(input: &str, cursor: usize) -> usize {
    input
        .chars()
        .take(cursor)
        .enumerate()
        .filter_map(|(index, ch)| (ch == '\n').then_some(index + 1))
        .last()
        .unwrap_or(0)
}
fn line_end(input: &str, cursor: usize) -> usize {
    cursor
        + input
            .chars()
            .skip(cursor)
            .position(|ch| ch == '\n')
            .unwrap_or_else(|| input.chars().count() - cursor)
}

fn move_vertical(input: &str, cursor: usize, delta: isize) -> usize {
    let chars: Vec<char> = input.chars().collect();
    let mut starts = vec![0];
    starts.extend(
        chars
            .iter()
            .enumerate()
            .filter_map(|(index, ch)| (*ch == '\n').then_some(index + 1)),
    );
    let line = starts
        .partition_point(|start| *start <= cursor)
        .saturating_sub(1);
    let target = line.saturating_add_signed(delta).min(starts.len() - 1);
    let column = cursor.saturating_sub(starts[line]);
    let end = chars[starts[target]..]
        .iter()
        .position(|ch| *ch == '\n')
        .map(|offset| starts[target] + offset)
        .unwrap_or(chars.len());
    starts[target] + column.min(end - starts[target])
}

fn char_index_at_width(value: &str, target_width: usize) -> usize {
    let mut width = 0;
    for (index, ch) in value.chars().enumerate() {
        let next = width + UnicodeWidthChar::width(ch).unwrap_or(0);
        if target_width < next {
            return index;
        }
        width = next;
    }
    value.chars().count()
}

fn resolve_path(root: &Path, value: &str) -> PathBuf {
    let path = PathBuf::from(value);
    if path.is_absolute() {
        path
    } else {
        root.join(path)
    }
}

fn project_slug(name: &str) -> String {
    let mut slug = String::new();
    let mut dash = false;
    for ch in name.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-') {
            slug.push(ch.to_ascii_lowercase());
            dash = false;
        } else if !slug.is_empty() && !dash {
            slug.push('-');
            dash = true;
        }
    }
    let slug = slug.trim_matches('-').to_string();
    if slug.is_empty() {
        "new-project".to_string()
    } else {
        slug
    }
}

fn normalize_path(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}

fn discover_projects(root: &Path) -> Vec<ProjectChoice> {
    let mut choices = Vec::new();
    fn visit(path: &Path, depth: usize, choices: &mut Vec<ProjectChoice>) {
        if depth > 4 {
            return;
        }
        if path.join("project.json").is_file() {
            let snapshot = read_snapshot(path);
            choices.push(ProjectChoice {
                path: normalize_path(path),
                id: snapshot.id,
                name: snapshot.name,
                target: snapshot.current_target,
            });
            return;
        }
        if let Ok(entries) = fs::read_dir(path) {
            for entry in entries.flatten() {
                if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                    visit(&entry.path(), depth + 1, choices);
                }
            }
        }
    }
    visit(&root.join("projects"), 0, &mut choices);
    choices.sort_by_key(|choice| choice.name.to_lowercase());
    choices
}

fn read_snapshot(project: &Path) -> ProjectSnapshot {
    let project_json = match read_json(&project.join("project.json")) {
        Ok(value) => value,
        Err(_error) => {
            return ProjectSnapshot {
                error: Some("项目状态读取失败；请打开诊断日志查看文件定位。".to_string()),
                ..Default::default()
            }
        }
    };
    let index_json = match read_json(&project.join("index.json")) {
        Ok(value) => value,
        Err(_error) => {
            return ProjectSnapshot {
                error: Some("项目索引读取失败；请打开诊断日志查看文件定位。".to_string()),
                ..Default::default()
            }
        }
    };
    let theorems = index_json
        .get("theorems")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| TheoremRow {
                    id: string_field(item, "id"),
                    title: string_field(item, "title"),
                    statement: string_field(item, "statement"),
                    status: string_field(item, "status"),
                    dependencies: item
                        .get("dependencies")
                        .and_then(Value::as_array)
                        .map(|values| {
                            values
                                .iter()
                                .filter_map(Value::as_str)
                                .map(ToOwned::to_owned)
                                .collect()
                        })
                        .unwrap_or_default(),
                    tags: item
                        .get("tags")
                        .and_then(Value::as_array)
                        .map(|values| {
                            values
                                .iter()
                                .filter_map(Value::as_str)
                                .map(ToOwned::to_owned)
                                .collect()
                        })
                        .unwrap_or_default(),
                    source_file: string_field(item, "source_file"),
                    audit_status: string_field(item, "audit_status"),
                    last_updated: string_field(item, "last_updated"),
                })
                .collect()
        })
        .unwrap_or_default();
    ProjectSnapshot {
        name: string_field(&project_json, "name"),
        display_title: {
            let title = string_field(&project_json, "display_title");
            if title.is_empty() {
                string_field(&project_json, "name")
            } else {
                title
            }
        },
        id: string_field(&project_json, "id"),
        purpose: {
            let purpose = string_field(&project_json, "purpose");
            if purpose.is_empty() {
                let description = string_field(&project_json, "description");
                if description.is_empty() {
                    string_field(&project_json, "name")
                } else {
                    description
                }
            } else {
                purpose
            }
        },
        current_target: string_field(&project_json, "current_target"),
        theorems,
        orchestrator_status: project_json
            .get("orchestrator")
            .and_then(|value| value.get("status"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        error: None,
    }
}

fn read_json(path: &Path) -> Result<Value, String> {
    let body = fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))?;
    serde_json::from_str(&body).map_err(|error| format!("{}: {error}", path.display()))
}

fn append_diagnostic(project: &Path, stderr: bool, line: &str) {
    let path = project.join("logs").join("tui-diagnostics.log");
    let Ok(mut handle) = (|| {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        OpenOptions::new().create(true).append(true).open(path)
    })() else {
        return;
    };
    let stream = if stderr { "stderr" } else { "stdout" };
    let _ = writeln!(handle, "[{stream}] {line}");
}

fn string_field(value: &Value, field: &str) -> String {
    value
        .get(field)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn read_theorem_detail(project: &Path, row: &TheoremRow) -> TheoremRow {
    let path = project.join("theorems").join(format!("{}.json", row.id));
    let Ok(value) = read_json(&path) else {
        return row.clone();
    };
    let mut detail = row.clone();
    let statement = string_field(&value, "statement");
    if !statement.is_empty() {
        detail.statement = statement;
    }
    let dependencies = value
        .get("dependencies")
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_str)
                .map(ToOwned::to_owned)
                .collect::<Vec<_>>()
        });
    if let Some(dependencies) = dependencies {
        detail.dependencies = dependencies;
    }
    let tags = value.get("tags").and_then(Value::as_array).map(|values| {
        values
            .iter()
            .filter_map(Value::as_str)
            .map(ToOwned::to_owned)
            .collect::<Vec<_>>()
    });
    if let Some(tags) = tags {
        detail.tags = tags;
    }
    for (field, target) in [
        ("status", &mut detail.status),
        ("source_file", &mut detail.source_file),
        ("audit_status", &mut detail.audit_status),
        ("last_updated", &mut detail.last_updated),
    ] {
        let value = string_field(&value, field);
        if !value.is_empty() {
            *target = value;
        }
    }
    detail
}

fn short_activity(value: &str) -> String {
    let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.is_empty() {
        "当前研究动作".to_string()
    } else {
        truncate_for_width(&compact, 36)
    }
}

fn display_location(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .ok()
        .filter(|relative| !relative.as_os_str().is_empty())
        .map(|relative| relative.display().to_string())
        .or_else(|| {
            path.file_name()
                .and_then(|name| name.to_str())
                .map(ToOwned::to_owned)
        })
        .map(|value| short_activity(&value))
        .unwrap_or_else(|| "项目内文件".to_string())
}

fn terminal() -> Result<Terminal<CrosstermBackend<Stdout>>> {
    enable_raw_mode().context("enable terminal raw mode")?;
    let mut stdout = io::stdout();
    execute!(
        stdout,
        EnterAlternateScreen,
        EnableMouseCapture,
        EnableBracketedPaste
    )
    .context("enter alternate screen")?;
    Terminal::new(CrosstermBackend::new(stdout)).context("create terminal")
}

fn restore_terminal(mut terminal: Terminal<CrosstermBackend<Stdout>>) -> Result<()> {
    disable_raw_mode().context("disable terminal raw mode")?;
    execute!(
        terminal.backend_mut(),
        DisableBracketedPaste,
        DisableMouseCapture,
        LeaveAlternateScreen
    )
    .context("leave alternate screen")?;
    terminal.show_cursor().context("show cursor")?;
    Ok(())
}

fn run(mut terminal: Terminal<CrosstermBackend<Stdout>>, mut app: App) -> Result<()> {
    loop {
        if app.animation_needed() && app.animation.tick() {
            app.request_render();
        }
        app.drain_backend_events();
        // Pi-style invalidation: disk projections and the terminal are not
        // rebuilt when nothing changed. This is especially important while a
        // long prompt is sitting in the editor.
        app.refresh_snapshot_if_changed();
        if app.render_dirty {
            terminal.draw(|frame| draw_ui(frame, &mut app))?;
            app.render_dirty = false;
        }
        if app.should_quit {
            break;
        }
        let timeout = if app.animation_needed() {
            Duration::from_millis(50)
        } else {
            Duration::from_millis(500)
        };
        if event::poll(timeout)? {
            match event::read()? {
                Event::Key(key) => app.handle_key(key),
                Event::Mouse(mouse) => app.handle_mouse(mouse),
                Event::Paste(value) => app.handle_paste(value),
                _ => {}
            }
            app.invalidate();
        }
    }
    restore_terminal(terminal)
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().skip(1).collect();
    let root = args
        .windows(2)
        .find(|pair| pair[0] == "--root")
        .map(|pair| PathBuf::from(&pair[1]))
        .unwrap_or(env::current_dir().context("read current directory")?);
    let project = args
        .windows(2)
        .find(|pair| pair[0] == "--project")
        .map(|pair| resolve_path(&root, &pair[1]))
        .unwrap_or_else(|| root.join("projects/demo"));
    let config = args
        .windows(2)
        .find(|pair| pair[0] == "--config")
        .map(|pair| resolve_path(&root, &pair[1]))
        .unwrap_or_else(|| root.join("configs/models.toml"));
    let terminal = terminal()?;
    let result = run(terminal, App::new(root, normalize_path(&project), config));
    if result.is_err() {
        let _ = disable_raw_mode();
        let mut stdout = io::stdout();
        let _ = execute!(
            stdout,
            DisableBracketedPaste,
            DisableMouseCapture,
            LeaveAlternateScreen
        );
    }
    result
}

#[cfg(test)]
mod tests {
    use super::{
        activity_elapsed, command_candidates, insert_text, localized_status, move_vertical,
        project_slug, read_snapshot, resolve_path, status_icon, transcript_lines,
        truncate_for_width, wrap_line, AnimationState, App, Focus, ProjectSession, TranscriptEntry,
        TranscriptKind, UiActivity,
    };
    use crossterm::event::{
        KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
    };
    use ratatui::layout::Rect;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::{Duration, Instant};

    #[test]
    fn slash_commands_are_filtered_for_popup() {
        assert_eq!(
            command_candidates("/swi")
                .iter()
                .map(|item| item.name)
                .collect::<Vec<_>>(),
            vec!["switch"]
        );
        assert!(command_candidates("hello").is_empty());
        assert!(command_candidates("/run target").is_empty());
        assert_eq!(
            command_candidates("/imp")
                .iter()
                .map(|item| item.name)
                .collect::<Vec<_>>(),
            vec!["import"]
        );
    }
    #[test]
    fn new_command_opens_multiline_project_goal_editor() {
        let project = PathBuf::from("/tmp/mathagent-tui-editor-test");
        let mut app = App::new(
            project.clone(),
            project.clone(),
            project.join("config.json"),
        );
        app.execute_command("/new primitive-pythagorean");
        let editor = app.project_editor.as_ref().expect("editor should open");
        assert_eq!(editor.id, "primitive-pythagorean");
        assert_eq!(editor.field, super::ProjectEditorField::Goal);
        assert!(app.project_picker.is_none());
    }
    #[test]
    fn switch_command_opens_existing_project_picker() {
        let project = PathBuf::from("/tmp/mathagent-tui-switch-test");
        let mut app = App::new(
            project.clone(),
            project.clone(),
            project.join("config.json"),
        );
        app.execute_command("/switch");
        assert!(app.project_picker.is_some());
        assert!(app.project_editor.is_none());
    }
    #[test]
    fn project_slug_is_safe_for_default_workspace_paths() {
        assert_eq!(project_slug("New Research Project"), "new-research-project");
        assert_eq!(project_slug("数学项目"), "new-project");
    }
    #[test]
    fn unicode_input_inserts_at_character_cursor() {
        let mut input = "数题".to_string();
        let mut cursor = 1;
        insert_text(&mut input, &mut cursor, "学研");
        assert_eq!(input, "数学研题");
        assert_eq!(cursor, 3);
    }
    #[test]
    fn multiline_cursor_moves_by_logical_column() {
        assert_eq!(move_vertical("abc\nx\n1234", 2, 1), 5);
        assert_eq!(move_vertical("abc\nx\n1234", 5, 1), 7);
    }
    #[test]
    fn cjk_lines_wrap_by_terminal_width() {
        assert_eq!(wrap_line("数学研究", 4), vec!["数学", "研究"]);
    }
    #[test]
    fn wrapped_transcript_lines_keep_their_entry_kind() {
        let entries = vec![TranscriptEntry::new(
            TranscriptKind::Error,
            "Target is already PROVED and must be re-audited",
            true,
        )];
        let lines = transcript_lines(&entries, 24, false);
        assert!(lines.len() > 1);
        assert!(lines.iter().all(|line| line.kind == TranscriptKind::Error));
        assert!(lines[0].text.starts_with("✕ 错误 · "));
        assert!(!lines[1].text.contains("错误"));
    }
    #[test]
    fn full_ui_event_details_are_kept_out_of_compact_transcript() {
        let mut session = ProjectSession::default();
        session.apply_ui_event(&serde_json::json!({
            "event_type": "research_ui_event",
            "event_id": "failure-1",
            "role": "planner",
            "action": "plan_project",
            "stage": "PLANNING",
            "title": "正在分析研究目标",
            "summary": "研究目标规划失败。",
            "status": "FAILED",
            "error": {
                "message": "项目或模型配置不可用。",
                "detail": "OPENROUTER_API_KEY is required by the configured role planner",
                "action": "检查 .env 和模型配置",
                "diagnostic": "runs/orchestrator/diagnostics.log"
            }
        }));
        assert!(session
            .transcript
            .iter()
            .any(|entry| entry.text.contains("OPENROUTER_API_KEY")));
        assert!(session.transcript.iter().all(|entry| !entry.compact));
        assert_eq!(session.activities[0].status, "FAILED");
    }
    #[test]
    fn terminal_activity_without_duration_does_not_keep_counting() {
        let now = Instant::now();
        let activity = UiActivity {
            event_id: "failure-1".into(),
            theorem_id: String::new(),
            run_id: String::new(),
            role: "system".into(),
            action: "runtime".into(),
            stage: "CLI".into(),
            title: "研究运行失败".into(),
            summary: "失败".into(),
            status: "FAILED".into(),
            elapsed_ms: None,
            started_at: now - Duration::from_secs(90),
            updated_at: now,
            flash_until: None,
            error: None,
            diagnostic: None,
            error_action: None,
            retryable: None,
            artifacts: Vec::new(),
            history: Vec::new(),
        };
        assert_eq!(activity_elapsed(&activity), "0.0s");
    }
    #[test]
    fn compact_transcript_hides_commands_and_raw_output() {
        let entries = vec![
            TranscriptEntry::new(TranscriptKind::Activity, "Run · theorem-a", true),
            TranscriptEntry::new(TranscriptKind::Output, "uv run ...", false),
            TranscriptEntry::new(TranscriptKind::Output, "raw output", false),
        ];
        assert_eq!(transcript_lines(&entries, 80, false).len(), 1);
        assert_eq!(transcript_lines(&entries, 80, true).len(), 3);
    }
    #[test]
    fn only_left_click_changes_mouse_focus() {
        let project = PathBuf::from("/tmp/mathagent-tui-focus-test");
        let mut app = App::new(
            project.clone(),
            project.clone(),
            project.join("config.json"),
        );
        app.regions.workspace = Rect::new(0, 0, 20, 20);
        app.regions.transcript = Rect::new(20, 0, 20, 20);
        app.regions.composer = Rect::new(20, 20, 20, 10);

        app.focus = Focus::Composer;
        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::Moved,
            column: 5,
            row: 5,
            modifiers: KeyModifiers::NONE,
        });
        assert_eq!(app.focus, Focus::Composer);

        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::ScrollDown,
            column: 25,
            row: 5,
            modifiers: KeyModifiers::NONE,
        });
        assert_eq!(app.focus, Focus::Composer);

        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 5,
            row: 5,
            modifiers: KeyModifiers::NONE,
        });
        assert_eq!(app.focus, Focus::Workspace);
    }
    #[test]
    fn relative_paths_are_rooted_at_repository() {
        assert_eq!(
            resolve_path(Path::new("/repo"), "projects/demo"),
            Path::new("/repo/projects/demo")
        );
    }
    #[test]
    fn status_icons_are_stable() {
        assert_eq!(status_icon("PROVED"), "✓");
        assert_eq!(status_icon("FAILED_ROUTE"), "×");
    }

    #[test]
    fn animation_frames_advance_and_reduced_motion_is_stable() {
        let animation = AnimationState::default();
        assert_eq!(animation.spinner(), "⠋");
        let mut reduced = AnimationState {
            reduced_motion: true,
            ..AnimationState::default()
        };
        assert_eq!(reduced.spinner(), "•");
        reduced.frame = 9;
        assert_eq!(reduced.spinner(), "•");
        let ascii = AnimationState {
            ascii_spinner: true,
            ..AnimationState::default()
        };
        assert_eq!(ascii.spinner(), "|");
    }

    #[test]
    fn long_chinese_titles_are_width_bounded() {
        let value = truncate_for_width("原始勾股数组的完全分类与欧几里得参数化", 12);
        assert!(value.ends_with('…'));
        assert!(value.chars().count() < 12);
    }

    #[test]
    fn ui_events_update_one_activity_item() {
        let mut session = ProjectSession::default();
        session.apply_ui_event(&serde_json::json!({
            "event_type": "research_ui_event",
            "event_id": "a1",
            "role": "planner",
            "action": "plan_project",
            "stage": "PLANNING",
            "title": "正在分析研究目标",
            "summary": "开始",
            "status": "STARTED"
        }));
        session.apply_ui_event(&serde_json::json!({
            "event_type": "research_ui_event",
            "event_id": "a1",
            "role": "planner",
            "action": "plan_project",
            "stage": "PLANNING",
            "title": "正在分析研究目标",
            "summary": "完成",
            "status": "COMPLETED"
        }));
        assert_eq!(session.activities.len(), 1);
        assert_eq!(session.activities[0].summary, "完成");
        assert_eq!(session.activities[0].history.len(), 2);
        assert_eq!(localized_status("PROVED"), "已完成");
    }

    #[test]
    fn project_session_restores_pipeline_history_from_timeline() {
        let root = std::env::temp_dir().join("mathagent-tui-timeline-test");
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("timeline directory");
        fs::write(
            root.join("timeline.jsonl"),
            r#"{"timeline_schema_version":1,"event_id":"run-1:event-1","kind":"PIPELINE_EVENT","action":"TASK_READY","status":"PROGRESS","run_id":"run-1","theorem_id":"lemma-a","role":"pipeline","payload":{"type":"TASK_READY"}}
"#,
        )
        .expect("timeline event");
        let mut session = ProjectSession::default();
        session.load_project_history(&root);
        assert_eq!(session.activities.len(), 1);
        assert_eq!(session.activities[0].action, "TASK_READY");
        assert_eq!(session.activities[0].theorem_id, "lemma-a");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn timeline_append_is_incremental_and_deduplicated() {
        let root = std::env::temp_dir().join(format!(
            "mathagent-tui-incremental-timeline-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("timeline directory");
        let timeline = root.join("timeline.jsonl");
        let first = r#"{"kind":"PIPELINE_EVENT","event_id":"e1","action":"TASK_READY","status":"PROGRESS","run_id":"run-1","theorem_id":"lemma-a","role":"pipeline","payload":{"type":"TASK_READY"}}
"#;
        fs::write(&timeline, first).expect("first timeline event");
        let mut session = ProjectSession::default();
        session.load_timeline(&timeline);
        let transcript_len = session.transcript.len();
        let offset = session.timeline_bytes;
        assert_eq!(session.activities.len(), 1);

        session.load_timeline(&timeline);
        assert_eq!(session.timeline_bytes, offset);
        assert_eq!(session.transcript.len(), transcript_len);

        let second = r#"{"kind":"PIPELINE_EVENT","event_id":"e2","action":"TASK_DONE","status":"COMPLETED","run_id":"run-1","theorem_id":"lemma-a","role":"pipeline","payload":{"type":"TASK_DONE"}}
"#;
        use std::io::Write;
        fs::OpenOptions::new()
            .append(true)
            .open(&timeline)
            .expect("append timeline")
            .write_all(second.as_bytes())
            .expect("second timeline event");
        session.load_timeline(&timeline);
        assert_eq!(session.activities.len(), 2);
        let after_append = session.transcript.len();
        session.load_timeline(&timeline);
        assert_eq!(session.transcript.len(), after_append);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn transcript_memory_is_bounded_for_streaming_output() {
        let mut session = ProjectSession::default();
        for index in 0..2500 {
            session.entry(
                TranscriptKind::Output,
                format!("stream chunk {index}"),
                false,
            );
        }
        assert!(session.transcript.len() <= 2400);
        assert!(session.transcript[0].text.contains("stream chunk 401"));
    }

    #[test]
    fn activity_scroll_pauses_follow_until_end() {
        let mut session = ProjectSession {
            activity_follow: true,
            activity_viewport: 1,
            ..ProjectSession::default()
        };
        for event_id in ["a1", "a2"] {
            session.apply_ui_event(&serde_json::json!({
                "event_type": "research_ui_event",
                "event_id": event_id,
                "role": "worker",
                "action": "prove_subproblem",
                "stage": "PROOF",
                "title": "正在研究子命题",
                "summary": "进行中",
                "status": "STARTED"
            }));
        }
        let mut app = App::new(
            PathBuf::from("/tmp/mathagent-tui-scroll-test"),
            PathBuf::from("/tmp/mathagent-tui-scroll-test"),
            PathBuf::from("/tmp/mathagent-tui-scroll-test/config.json"),
        );
        app.sessions.insert(app.project.clone(), session);
        app.scroll_activity(isize::MIN);
        assert!(!app.session().activity_follow);
        let before = app.session().activity_offset;
        app.session_mut().apply_ui_event(&serde_json::json!({
            "event_type": "research_ui_event",
            "event_id": "a3",
            "role": "auditor",
            "action": "audit_candidate",
            "stage": "AUDIT",
            "title": "正在审计子命题",
            "summary": "新事件",
            "status": "STARTED"
        }));
        assert_eq!(app.session().activity_offset, before);
        assert!(!app.session().activity_follow);
        app.scroll_activity(isize::MAX);
        assert!(app.session().activity_follow);
    }
    #[test]
    fn project_snapshot_reads_current_target_and_theorems() {
        let project =
            std::env::temp_dir().join(format!("mathagent-tui-snapshot-{}", std::process::id()));
        fs::create_dir_all(&project).expect("create temporary project");
        fs::write(
            project.join("project.json"),
            r#"{"id":"demo","name":"Demo","purpose":"Core goal","current_target":"t-1"}"#,
        )
        .expect("write project");
        fs::write(
            project.join("index.json"),
            r#"{"theorems":[{"id":"t-1","title":"A theorem","status":"OPEN"}]}"#,
        )
        .expect("write index");
        let snapshot = read_snapshot(&project);
        assert_eq!(snapshot.id, "demo");
        assert_eq!(snapshot.purpose, "Core goal");
        assert_eq!(snapshot.current_target, "t-1");
        assert_eq!(snapshot.theorems[0].title, "A theorem");
        assert!(snapshot.error.is_none());
        fs::remove_dir_all(project).expect("remove temporary project");
    }

    #[test]
    fn theorem_click_enter_and_escape_open_detail_panel() {
        let project =
            std::env::temp_dir().join(format!("mathagent-tui-detail-{}", std::process::id()));
        fs::create_dir_all(&project).expect("create detail project");
        fs::write(
            project.join("project.json"),
            r#"{"id":"detail","name":"Detail","display_title":"短标题","purpose":"goal"}"#,
        )
        .expect("write detail project");
        fs::write(
            project.join("index.json"),
            r#"{"theorems":[{"id":"t-1","title":"奇偶性引理","statement":"命题内容","status":"OPEN"}]}"#,
        )
        .expect("write detail index");
        let mut app = App::new(
            project.clone(),
            project.clone(),
            project.join("config.json"),
        );
        app.regions.workspace = Rect::new(0, 0, 30, 12);
        app.regions.theorem_list = Rect::new(0, 5, 30, 5);
        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 2,
            row: 6,
            modifiers: KeyModifiers::NONE,
        });
        assert!(app.session().detail_open);
        assert_eq!(app.focus, Focus::Transcript);
        app.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert!(!app.session().detail_open);
        app.focus = Focus::Workspace;
        app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert!(app.session().detail_open);
        fs::remove_dir_all(project).expect("remove detail project");
    }
}
