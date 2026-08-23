use std::collections::VecDeque;
use std::env;
use std::fs;
use std::io::{self, Stdout};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Clear, Paragraph, Wrap};
use ratatui::{Frame, Terminal};
use serde_json::Value;

const MAX_LOG_LINES: usize = 500;

#[derive(Debug)]
enum BackendEvent {
    Finished {
        command: String,
        output: String,
        success: bool,
    },
}

#[derive(Debug, Default)]
struct ProjectSnapshot {
    name: String,
    id: String,
    current_target: String,
    theorems: Vec<TheoremRow>,
    error: Option<String>,
}

#[derive(Debug)]
struct TheoremRow {
    id: String,
    title: String,
    status: String,
}

struct App {
    root: PathBuf,
    project: PathBuf,
    config: PathBuf,
    input: String,
    history: Vec<String>,
    history_index: Option<usize>,
    logs: VecDeque<String>,
    snapshot: ProjectSnapshot,
    running: Option<String>,
    tx: Sender<BackendEvent>,
    rx: Receiver<BackendEvent>,
    should_quit: bool,
    show_help: bool,
}

impl App {
    fn new(root: PathBuf, project: PathBuf, config: PathBuf) -> Self {
        let (tx, rx) = mpsc::channel();
        let mut app = Self {
            root,
            project,
            config,
            input: String::new(),
            history: Vec::new(),
            history_index: None,
            logs: VecDeque::new(),
            snapshot: ProjectSnapshot::default(),
            running: None,
            tx,
            rx,
            should_quit: false,
            show_help: false,
        };
        app.refresh_snapshot();
        app.log("Welcome to MathAgent. Type /help for commands.");
        app
    }

    fn log(&mut self, message: impl Into<String>) {
        for line in message.into().lines() {
            if self.logs.len() >= MAX_LOG_LINES {
                self.logs.pop_front();
            }
            self.logs.push_back(line.to_string());
        }
    }

    fn refresh_snapshot(&mut self) {
        self.snapshot = read_snapshot(&self.project);
    }

    fn handle_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            self.should_quit = true;
            return;
        }
        if self.show_help {
            if matches!(key.code, KeyCode::Esc | KeyCode::Char('?')) {
                self.show_help = false;
            }
            return;
        }
        match key.code {
            KeyCode::Enter => self.submit_input(),
            KeyCode::Backspace => {
                self.input.pop();
            }
            KeyCode::Char(ch) => self.input.push(ch),
            KeyCode::Up => self.history_up(),
            KeyCode::Down => self.history_down(),
            KeyCode::Esc => self.input.clear(),
            KeyCode::F(5) => {
                self.refresh_snapshot();
                self.log("Project status refreshed.");
            }
            _ => {}
        }
    }

    fn history_up(&mut self) {
        if self.history.is_empty() {
            return;
        }
        let index = self
            .history_index
            .unwrap_or(self.history.len())
            .saturating_sub(1);
        self.history_index = Some(index);
        self.input = self.history[index].clone();
    }

    fn history_down(&mut self) {
        let Some(index) = self.history_index else {
            return;
        };
        if index + 1 >= self.history.len() {
            self.history_index = None;
            self.input.clear();
        } else {
            self.history_index = Some(index + 1);
            self.input = self.history[index + 1].clone();
        }
    }

    fn submit_input(&mut self) {
        let command = self.input.trim().to_string();
        self.input.clear();
        self.history_index = None;
        if command.is_empty() {
            return;
        }
        self.history.push(command.clone());
        self.log(format!("> {command}"));
        self.execute_command(&command);
    }

    fn execute_command(&mut self, command: &str) {
        let mut parts = command.split_whitespace();
        let Some(name) = parts.next() else { return };
        match name {
            "/help" | "help" => self.show_help = true,
            "/quit" | "/exit" | "quit" | "exit" => self.should_quit = true,
            "/clear" => self.logs.clear(),
            "/status" | "status" | "/refresh" | "refresh" => {
                self.refresh_snapshot();
                self.log("Project status refreshed.");
            }
            "/project" | "project" => {
                let Some(path) = parts.next() else {
                    self.log("Usage: /project <path>");
                    return;
                };
                self.project = resolve_path(&self.root, path);
                self.refresh_snapshot();
                self.log(format!("Project switched to {}", self.project.display()));
            }
            "/config" | "config" => {
                let Some(path) = parts.next() else {
                    self.log("Usage: /config <path>");
                    return;
                };
                self.config = resolve_path(&self.root, path);
                self.log(format!("Model config set to {}", self.config.display()));
            }
            "/run" | "run" => {
                let target = parts.next().map(ToOwned::to_owned).or_else(|| {
                    (!self.snapshot.current_target.is_empty())
                        .then(|| self.snapshot.current_target.clone())
                });
                let Some(target) = target else {
                    self.log("No target selected. Usage: /run <theorem-id>");
                    return;
                };
                self.start_backend(
                    "research run",
                    vec![
                        "run".into(),
                        "--project".into(),
                        self.project.display().to_string(),
                        "--target".into(),
                        target,
                        "--config".into(),
                        self.config.display().to_string(),
                    ],
                );
            }
            "/demo" | "demo" => {
                let path = parts
                    .next()
                    .map(|value| resolve_path(&self.root, value))
                    .unwrap_or_else(|| self.root.join("projects/observatory-demo"));
                self.start_backend(
                    "showcase demo",
                    vec![
                        "demo".into(),
                        "--project".into(),
                        path.display().to_string(),
                    ],
                );
            }
            _ => self.log("Unknown command. Type /help to see available commands."),
        }
    }

    fn start_backend(&mut self, label: &str, args: Vec<String>) {
        if self.running.is_some() {
            self.log("A backend task is already running; wait for it to finish.");
            return;
        }
        let command = format!(
            "uv run python -m math_research_agent.research {}",
            args.join(" ")
        );
        self.running = Some(label.to_string());
        self.log(format!("Starting {label}..."));
        let root = self.root.clone();
        let tx = self.tx.clone();
        thread::spawn(move || {
            let result = Command::new("uv")
                .current_dir(&root)
                .args(["run", "--project"])
                .arg(&root)
                .args(["python", "-m", "math_research_agent.research"])
                .args(&args)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .output();
            let (output, success) = match result {
                Ok(output) => {
                    let stdout = String::from_utf8_lossy(&output.stdout);
                    let stderr = String::from_utf8_lossy(&output.stderr);
                    (
                        format!("{stdout}{stderr}").trim().to_string(),
                        output.status.success(),
                    )
                }
                Err(error) => (format!("failed to start uv: {error}"), false),
            };
            let _ = tx.send(BackendEvent::Finished {
                command,
                output,
                success,
            });
        });
    }

    fn drain_backend_events(&mut self) {
        while let Ok(event) = self.rx.try_recv() {
            match event {
                BackendEvent::Finished {
                    command,
                    output,
                    success,
                } => {
                    self.running = None;
                    self.log(format!(
                        "{}: {}",
                        if success { "Completed" } else { "Failed" },
                        command
                    ));
                    if !output.is_empty() {
                        self.log(format_backend_output(&output));
                    }
                    self.refresh_snapshot();
                }
            }
        }
    }
}

fn resolve_path(root: &Path, value: &str) -> PathBuf {
    let path = PathBuf::from(value);
    if path.is_absolute() {
        path
    } else {
        root.join(path)
    }
}

fn read_snapshot(project: &Path) -> ProjectSnapshot {
    let project_file = project.join("project.json");
    let index_file = project.join("index.json");
    let project_json = match read_json(&project_file) {
        Ok(value) => value,
        Err(error) => {
            return ProjectSnapshot {
                error: Some(error),
                ..Default::default()
            }
        }
    };
    let index_json = match read_json(&index_file) {
        Ok(value) => value,
        Err(error) => {
            return ProjectSnapshot {
                error: Some(error),
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
                    status: string_field(item, "status"),
                })
                .collect()
        })
        .unwrap_or_default();
    ProjectSnapshot {
        name: string_field(&project_json, "name"),
        id: string_field(&project_json, "id"),
        current_target: string_field(&project_json, "current_target"),
        theorems,
        error: None,
    }
}

fn read_json(path: &Path) -> Result<Value, String> {
    let body = fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))?;
    serde_json::from_str(&body).map_err(|error| format!("{}: {error}", path.display()))
}

fn string_field(value: &Value, field: &str) -> String {
    value
        .get(field)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn format_backend_output(output: &str) -> String {
    match serde_json::from_str::<Value>(output) {
        Ok(value) => serde_json::to_string_pretty(&value).unwrap_or_else(|_| output.to_string()),
        Err(_) => output.to_string(),
    }
}

fn draw_ui(frame: &mut Frame, app: &App) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(2),
        ])
        .split(frame.area());
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Length(34), Constraint::Min(40)])
        .split(outer[1]);

    let running = app
        .running
        .as_ref()
        .map(|value| format!("RUNNING · {value}"))
        .unwrap_or_else(|| "READY".to_string());
    let header = Paragraph::new(Line::from(vec![
        Span::styled(
            " MATHAGENT ",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw("  mathematical research terminal  "),
        Span::styled(running, status_style(app.running.is_some())),
    ]))
    .block(
        Block::default()
            .borders(Borders::BOTTOM)
            .border_style(Style::default().fg(Color::Blue)),
    );
    frame.render_widget(header, outer[0]);

    draw_sidebar(frame, body[0], app);
    draw_main(frame, body[1], app);

    let footer =
        Paragraph::new(" Ctrl-C quit   F5 refresh   ↑/↓ history   Enter execute   /help commands")
            .style(Style::default().fg(Color::DarkGray));
    frame.render_widget(footer, outer[2]);

    if app.show_help {
        draw_help(frame);
    }
}

fn draw_sidebar(frame: &mut Frame, area: Rect, app: &App) {
    let mut lines = vec![
        Line::from(Span::styled(
            "PROJECT",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(app.snapshot.name.as_str()),
        Line::from(format!("id: {}", app.snapshot.id)),
        Line::from(format!("path: {}", app.project.display())),
        Line::from(format!("config: {}", app.config.display())),
        Line::from(""),
        Line::from(Span::styled(
            "TARGET",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(if app.snapshot.current_target.is_empty() {
            "none".into()
        } else {
            app.snapshot.current_target.clone()
        }),
        Line::from(""),
        Line::from(Span::styled(
            "THEOREMS",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
    ];
    if let Some(error) = &app.snapshot.error {
        lines.push(Line::from(Span::styled(
            error,
            Style::default().fg(Color::Red),
        )));
    } else if app.snapshot.theorems.is_empty() {
        lines.push(Line::from("(none)"));
    } else {
        for theorem in &app.snapshot.theorems {
            let color = match theorem.status.as_str() {
                "PROVED" => Color::Green,
                "FAILED_ROUTE" => Color::Red,
                "IN_RESEARCH" => Color::Yellow,
                _ => Color::White,
            };
            lines.push(Line::from(vec![
                Span::styled(
                    format!("{} ", status_icon(&theorem.status)),
                    Style::default().fg(color),
                ),
                Span::raw(format!("{} [{}]", theorem.id, theorem.status)),
            ]));
            if !theorem.title.is_empty() {
                lines.push(Line::from(Span::styled(
                    format!("  {}", theorem.title),
                    Style::default().fg(Color::DarkGray),
                )));
            }
        }
    }
    let widget = Paragraph::new(Text::from(lines))
        .block(
            Block::default()
                .title(" Workspace ")
                .borders(Borders::ALL)
                .border_style(Style::default().fg(Color::Blue)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(widget, area);
}

fn draw_main(frame: &mut Frame, area: Rect, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(4), Constraint::Length(3)])
        .split(area);
    let log_lines: Vec<Line> = app
        .logs
        .iter()
        .map(|line| Line::from(line.as_str()))
        .collect();
    let log = Paragraph::new(Text::from(log_lines))
        .block(
            Block::default()
                .title(" Research session ")
                .borders(Borders::ALL)
                .border_style(Style::default().fg(Color::Blue)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(log, chunks[0]);

    let prompt = Paragraph::new(format!("❯ {}", app.input))
        .style(Style::default().fg(Color::White))
        .block(
            Block::default()
                .title(" Command ")
                .borders(Borders::ALL)
                .border_style(Style::default().fg(Color::Cyan)),
        );
    frame.render_widget(prompt, chunks[1]);
}

fn draw_help(frame: &mut Frame) {
    let area = centered_rect(70, 70, frame.area());
    frame.render_widget(Clear, area);
    let help = Paragraph::new(vec![
        Line::from(Span::styled(
            "MathAgent commands",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from("/status                 refresh project state"),
        Line::from("/project <path>         switch project"),
        Line::from("/config <path>          switch model config"),
        Line::from("/run [theorem-id]       start a research run"),
        Line::from("/demo [path]            generate the deterministic showcase"),
        Line::from("/refresh                reload files"),
        Line::from("/clear                  clear session log"),
        Line::from("/quit                   exit the terminal"),
        Line::from(""),
        Line::from("Esc or ? closes this help."),
    ])
    .block(
        Block::default()
            .title(" Help ")
            .borders(Borders::ALL)
            .border_style(Style::default().fg(Color::Cyan)),
    )
    .style(Style::default().bg(Color::Rgb(15, 23, 42)))
    .wrap(Wrap { trim: false });
    frame.render_widget(help, area);
}

fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(vertical[1])[1]
}

fn status_icon(status: &str) -> &'static str {
    match status {
        "PROVED" => "✓",
        "FAILED_ROUTE" => "×",
        "IN_RESEARCH" => "→",
        _ => "·",
    }
}

fn status_style(running: bool) -> Style {
    Style::default()
        .fg(if running { Color::Yellow } else { Color::Green })
        .add_modifier(Modifier::BOLD)
}

fn terminal() -> Result<Terminal<CrosstermBackend<Stdout>>> {
    enable_raw_mode().context("enable terminal raw mode")?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen).context("enter alternate screen")?;
    Terminal::new(CrosstermBackend::new(stdout)).context("create terminal")
}

fn restore_terminal(mut terminal: Terminal<CrosstermBackend<Stdout>>) -> Result<()> {
    disable_raw_mode().context("disable terminal raw mode")?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen).context("leave alternate screen")?;
    terminal.show_cursor().context("show cursor")?;
    Ok(())
}

fn run(mut terminal: Terminal<CrosstermBackend<Stdout>>, mut app: App) -> Result<()> {
    loop {
        app.drain_backend_events();
        terminal.draw(|frame| draw_ui(frame, &app))?;
        if app.should_quit {
            break;
        }
        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                app.handle_key(key);
            }
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
        .unwrap_or_else(|| root.join("configs/models.mock.json"));

    let terminal = terminal()?;
    let app = App::new(root, project, config);
    let result = run(terminal, app);
    if result.is_err() {
        let _ = disable_raw_mode();
        let mut stdout = io::stdout();
        let _ = execute!(stdout, LeaveAlternateScreen);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::{format_backend_output, read_snapshot, resolve_path, status_icon};
    use std::fs;
    use std::path::Path;

    #[test]
    fn relative_paths_are_rooted_at_repository() {
        assert_eq!(
            resolve_path(Path::new("/repo"), "projects/demo"),
            Path::new("/repo/projects/demo")
        );
    }

    #[test]
    fn absolute_paths_are_preserved() {
        assert_eq!(
            resolve_path(Path::new("/repo"), "/tmp/project"),
            Path::new("/tmp/project")
        );
    }

    #[test]
    fn backend_json_is_pretty_printed() {
        assert_eq!(
            format_backend_output("{\"status\":\"PROVED\"}"),
            "{\n  \"status\": \"PROVED\"\n}"
        );
        assert_eq!(format_backend_output("plain output"), "plain output");
    }

    #[test]
    fn status_icons_are_stable() {
        assert_eq!(status_icon("PROVED"), "✓");
        assert_eq!(status_icon("FAILED_ROUTE"), "×");
        assert_eq!(status_icon("OPEN"), "·");
    }

    #[test]
    fn project_snapshot_reads_current_target_and_theorems() {
        let project =
            std::env::temp_dir().join(format!("mathagent-tui-snapshot-{}", std::process::id()));
        fs::create_dir_all(&project).expect("create temporary project");
        fs::write(
            project.join("project.json"),
            r#"{"id":"demo","name":"Demo","current_target":"t-1"}"#,
        )
        .expect("write project");
        fs::write(
            project.join("index.json"),
            r#"{"theorems":[{"id":"t-1","title":"A theorem","status":"OPEN"}]}"#,
        )
        .expect("write index");

        let snapshot = read_snapshot(&project);

        assert_eq!(snapshot.id, "demo");
        assert_eq!(snapshot.current_target, "t-1");
        assert_eq!(snapshot.theorems.len(), 1);
        assert_eq!(snapshot.theorems[0].title, "A theorem");
        assert!(snapshot.error.is_none());
        fs::remove_dir_all(project).expect("remove temporary project");
    }
}
