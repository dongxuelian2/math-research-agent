use super::*;

pub(super) fn truncate_for_width(value: &str, width: usize) -> String {
    if width == 0 {
        return String::new();
    }
    let current = value
        .chars()
        .map(|ch| UnicodeWidthChar::width(ch).unwrap_or(0))
        .sum::<usize>();
    if current <= width {
        return value.to_string();
    }
    let mut output = String::new();
    let mut used = 0;
    let limit = width.saturating_sub(1);
    for ch in value.chars() {
        let char_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + char_width > limit {
            break;
        }
        output.push(ch);
        used += char_width;
    }
    output.push('…');
    output
}

pub(super) fn format_elapsed(duration: Duration) -> String {
    let seconds = duration.as_secs();
    if seconds < 60 {
        format!("{}.{:01}s", seconds, duration.subsec_millis() / 100)
    } else {
        format!("{}m {:02}s", seconds / 60, seconds % 60)
    }
}

pub(super) fn activity_elapsed(activity: &UiActivity) -> String {
    if let Some(elapsed) = activity.elapsed_ms {
        return format_elapsed(Duration::from_millis(elapsed));
    }
    if matches!(
        activity.status.as_str(),
        "COMPLETED" | "FAILED" | "CANCELLED"
    ) {
        // CLI-level terminal events may not carry a provider duration. They
        // are already finished, so do not make them look like live timers.
        return format_elapsed(Duration::ZERO);
    }
    format_elapsed(activity.started_at.elapsed())
}

pub(super) fn localized_status(status: &str) -> String {
    match status {
        "STARTED" | "PROGRESS" | "RUNNING" | "PLANNING" | "IN_RESEARCH" => "进行中".into(),
        "COMPLETED" | "COMPLETE" | "PROVED" => "已完成".into(),
        "FAILED" | "FAILED_ROUTE" | "REJECTED" => "失败".into(),
        "PARTIAL" => "部分完成".into(),
        "BLOCKED_INFRASTRUCTURE" => "基础设施阻塞".into(),
        "CANCELLED" => "已停止".into(),
        "OPEN" => "待开始".into(),
        "NOT_AUDITED" => "未审计".into(),
        "PASS" => "通过".into(),
        "FAIL" => "未通过".into(),
        "" => "就绪".into(),
        other => other.to_string(),
    }
}

pub(super) fn localized_role(role: &str) -> &'static str {
    match role {
        "tool" => "工具",
        "planner" => "规划",
        "worker" => "证明",
        "verifier" => "验证",
        "auditor" => "审计",
        "system" => "系统",
        _ => "研究",
    }
}

pub(super) fn activity_color(status: &str, role: &str) -> Color {
    match status {
        "FAILED" => Color::Red,
        "COMPLETED" => Color::Green,
        "STARTED" | "PROGRESS" => match role {
            "tool" => Color::Cyan,
            "planner" => Color::Cyan,
            "worker" => Color::Yellow,
            "verifier" => Color::Magenta,
            "auditor" => Color::Blue,
            _ => Color::White,
        },
        _ => Color::DarkGray,
    }
}

pub(super) fn draw_ui(frame: &mut Frame, app: &mut App) {
    let outer = Layout::vertical([
        Constraint::Length(3),
        Constraint::Min(8),
        Constraint::Length(2),
    ])
    .split(frame.area());
    draw_header(frame, outer[0], app);
    if app.transcript_fullscreen {
        app.regions.workspace = Rect::default();
        app.regions.theorem_list = Rect::default();
        app.regions.composer = Rect::default();
        draw_transcript(frame, outer[1], app, true);
        let footer =
            Paragraph::new("完整诊断日志 · Ctrl-T / Esc 关闭 · ↑↓ PgUp/PgDn 滚动 · End 跟随最新")
                .style(Style::default().fg(Color::DarkGray));
        frame.render_widget(footer, outer[2]);
        return;
    }
    let wide = frame.area().width >= 92;
    let body = if wide {
        Layout::horizontal([Constraint::Length(38), Constraint::Min(48)]).split(outer[1])
    } else {
        Layout::vertical([Constraint::Length(12), Constraint::Min(8)]).split(outer[1])
    };
    draw_workspace(frame, body[0], app);
    draw_main(frame, body[1], app);
    let footer = Paragraph::new(format!(
        "焦点：{}   Ctrl-T 诊断日志   Tab 切换面板   / 命令   鼠标点击/滚动   Ctrl-C 清空/退出",
        app.focus.label()
    ))
    .style(Style::default().fg(Color::DarkGray));
    frame.render_widget(footer, outer[2]);
    if app.show_help {
        draw_help(frame, app);
    }
    if app.project_editor.is_some() {
        draw_project_editor(frame, app);
    } else if app.project_picker.is_some() {
        draw_project_picker(frame, app);
    }
}

pub(super) fn draw_header(frame: &mut Frame, area: Rect, app: &App) {
    let task = app.running.get(&app.project);
    let active = app
        .session()
        .activities
        .iter()
        .rev()
        .find(|item| matches!(item.status.as_str(), "STARTED" | "PROGRESS"));
    let state = if let Some(task) = task {
        let elapsed = format_elapsed(task.started_at.elapsed());
        let action = active
            .map(|item| item.title.as_str())
            .unwrap_or("正在准备研究步骤");
        format!("{} {} · {}", app.animation.spinner(), action, elapsed)
    } else if let Some(active) = active {
        format!("{} {}", app.animation.spinner(), active.title)
    } else {
        localized_status(&app.session().snapshot.orchestrator_status)
    };
    let snapshot = &app.session().snapshot;
    let header = Paragraph::new(Line::from(vec![
        Span::styled(
            " MATHAGENT ",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(format!(
            " {}  ·  {}  ",
            short_activity(if snapshot.display_title.is_empty() {
                &snapshot.name
            } else {
                &snapshot.display_title
            }),
            if snapshot.current_target.is_empty() {
                "未选择子命题"
            } else {
                "已选择子命题"
            }
        )),
        Span::styled(
            state.clone(),
            status_style(task.is_some() || active.is_some()),
        ),
    ]))
    .block(
        Block::default()
            .borders(Borders::BOTTOM)
            .border_style(Style::default().fg(Color::Blue)),
    );
    frame.render_widget(header, area);
}

pub(super) fn focused_block(title: &str, focused: bool) -> Block<'_> {
    Block::default()
        .title(format!(" {title} "))
        .borders(Borders::ALL)
        .border_style(
            Style::default()
                .fg(if focused {
                    Color::Cyan
                } else {
                    Color::DarkGray
                })
                .add_modifier(if focused {
                    Modifier::BOLD
                } else {
                    Modifier::empty()
                }),
        )
}

pub(super) fn draw_workspace(frame: &mut Frame, area: Rect, app: &mut App) {
    app.regions.workspace = area;
    let chunks = Layout::vertical([Constraint::Length(8), Constraint::Min(3)]).split(area);
    app.regions.theorem_list = chunks[1];
    let session = app.session();
    let selected = session.snapshot.theorems.get(session.theorem_selected);
    let selected_title = selected
        .map(|row| truncate_for_width(&row.title, 20))
        .unwrap_or_else(|| "未选择".to_string());
    let summary = vec![
        Line::from(vec![
            Span::styled("项目  ", Style::default().fg(Color::DarkGray)),
            Span::styled(
                short_activity(if session.snapshot.display_title.is_empty() {
                    &session.snapshot.name
                } else {
                    &session.snapshot.display_title
                }),
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled("子命题  ", Style::default().fg(Color::DarkGray)),
            Span::raw(format!("{} 个", session.snapshot.theorems.len())),
        ]),
        Line::from(vec![
            Span::styled("当前    ", Style::default().fg(Color::DarkGray)),
            Span::styled(selected_title, Style::default().fg(Color::Yellow)),
        ]),
        Line::from(vec![
            Span::styled("状态    ", Style::default().fg(Color::DarkGray)),
            Span::styled(
                localized_status(&session.snapshot.orchestrator_status),
                status_style(!app.running.is_empty()),
            ),
        ]),
        Line::from(vec![
            Span::styled("提示    ", Style::default().fg(Color::DarkGray)),
            Span::raw(if session.detail_open {
                "右侧显示详情 · Esc 返回活动"
            } else {
                "点击子命题查看详情"
            }),
        ]),
    ];
    frame.render_widget(
        Paragraph::new(summary)
            .block(focused_block(
                "项目 · p 打开选择器",
                app.focus == Focus::Workspace,
            ))
            .wrap(Wrap { trim: false }),
        chunks[0],
    );
    let height = chunks[1].height.saturating_sub(2) as usize;
    let session = app.session_mut();
    if session.theorem_selected < session.theorem_scroll {
        session.theorem_scroll = session.theorem_selected;
    }
    if session.theorem_selected >= session.theorem_scroll + height.max(1) {
        session.theorem_scroll = session.theorem_selected + 1 - height.max(1);
    }
    let mut lines = Vec::new();
    if let Some(error) = &session.snapshot.error {
        lines.push(Line::from(Span::styled(
            error.clone(),
            Style::default().fg(Color::Red),
        )));
    }
    for (index, theorem) in session
        .snapshot
        .theorems
        .iter()
        .enumerate()
        .skip(session.theorem_scroll)
        .take(height)
    {
        let selected = index == session.theorem_selected;
        let color = status_color(&theorem.status);
        lines.push(
            Line::from(vec![
                Span::styled(
                    if selected { "▶ " } else { "  " },
                    Style::default().fg(Color::Cyan),
                ),
                Span::styled(
                    format!("{} ", status_icon(&theorem.status)),
                    Style::default().fg(color),
                ),
                Span::styled(
                    truncate_for_width(&theorem.title, 20),
                    Style::default().add_modifier(if selected {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    }),
                ),
                Span::styled(
                    format!("  {}", localized_status(&theorem.status)),
                    Style::default().fg(color),
                ),
            ])
            .style(if selected {
                Style::default().bg(Color::Rgb(30, 41, 59))
            } else {
                Style::default()
            }),
        );
    }
    frame.render_widget(
        Paragraph::new(lines).block(focused_block(
            "子命题 · ↑↓ 选择 · Enter 打开",
            app.focus == Focus::Workspace,
        )),
        chunks[1],
    );
}

pub(super) fn draw_main(frame: &mut Frame, area: Rect, app: &mut App) {
    // Keep the editor bounded.  Pi renders only the visible editor window;
    // letting a pasted prompt grow the whole layout makes every redraw walk
    // and allocate the complete input buffer.
    let input_lines = app.session_mut().input_layout().line_count.clamp(1, 6) as u16;
    let chunks =
        Layout::vertical([Constraint::Min(4), Constraint::Length(input_lines + 2)]).split(area);
    if app.session().detail_open {
        draw_theorem_detail(frame, chunks[0], app);
    } else {
        draw_activity(frame, chunks[0], app);
    }
    draw_composer(frame, chunks[1], app);
}

pub(super) fn draw_activity(frame: &mut Frame, area: Rect, app: &mut App) {
    app.regions.transcript = area;
    let viewport = area.height.saturating_sub(2) as usize;
    let width = area.width.saturating_sub(2).max(1) as usize;
    // Every activity is intentionally two stable rows (action + summary), so
    // scrolling never tears an action unit apart while the stream is moving.
    let visible_items = (viewport / 2).max(1);
    {
        let session = app.session_mut();
        session.activity_viewport = visible_items;
        let max = session.activities.len().saturating_sub(visible_items);
        if session.activity_follow {
            session.activity_offset = max;
        } else {
            session.activity_offset = session.activity_offset.min(max);
        }
    }
    let start = app.session().activity_offset;
    let activities = app
        .session()
        .activities
        .iter()
        .enumerate()
        .skip(start)
        .take(visible_items)
        .collect::<Vec<_>>();
    let mut lines = Vec::new();
    if activities.is_empty() {
        lines.push(Line::from(Span::styled(
            "等待研究动作 · 使用 /run 启动项目级证明",
            Style::default().fg(Color::DarkGray),
        )));
    }
    for (index, activity) in activities {
        let active = matches!(activity.status.as_str(), "STARTED" | "PROGRESS");
        let marker = if active {
            app.animation.spinner()
        } else if activity.status == "COMPLETED" {
            "✓"
        } else if activity.status == "FAILED" {
            "✕"
        } else {
            "·"
        };
        let color = activity_color(&activity.status, &activity.role);
        let elapsed = activity_elapsed(activity);
        let flash = activity
            .flash_until
            .is_some_and(|until| until > Instant::now());
        let title = truncate_for_width(&activity.title, width.saturating_sub(22).max(12));
        let summary = if activity.summary.is_empty() {
            String::new()
        } else {
            format!(
                "  {}",
                truncate_for_width(&activity.summary, width.saturating_sub(8).max(12))
            )
        };
        let mut line = Line::from(vec![
            Span::styled(
                format!("{} ", marker),
                Style::default().fg(color).add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("{}  ", localized_role(&activity.role)),
                Style::default().fg(Color::DarkGray),
            ),
            Span::styled(title, app.animation.pulse_style(active || flash)),
            Span::styled(
                format!("  {}", elapsed),
                Style::default().fg(Color::DarkGray),
            ),
        ]);
        if index == app.session().activity_selected.unwrap_or(usize::MAX) {
            line = line.style(Style::default().bg(Color::Rgb(30, 41, 59)));
        }
        lines.push(line);
        if !summary.trim().is_empty() {
            lines.push(Line::from(Span::styled(
                summary,
                Style::default().fg(if activity.status == "FAILED" {
                    Color::Red
                } else {
                    Color::Gray
                }),
            )));
        }
    }
    let title = if app.running.contains_key(&app.project) {
        format!("项目活动 · {} 运行中", app.animation.spinner())
    } else {
        "Agent 活动 · 动作与工具调用".to_string()
    };
    frame.render_widget(
        Paragraph::new(lines)
            .block(focused_block(&title, app.focus == Focus::Transcript))
            .wrap(Wrap { trim: true }),
        area,
    );
}

pub(super) fn draw_theorem_detail(frame: &mut Frame, area: Rect, app: &mut App) {
    app.regions.transcript = area;
    let Some(theorem) = app
        .session()
        .snapshot
        .theorems
        .get(app.session().theorem_selected)
    else {
        frame.render_widget(
            Paragraph::new("没有可显示的子命题详情").block(focused_block("子命题详情", true)),
            area,
        );
        return;
    };
    let theorem = read_theorem_detail(&app.project, theorem);
    let mut lines = vec![
        Line::from(Span::styled(
            theorem.title.clone(),
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(vec![
            Span::styled("状态  ", Style::default().fg(Color::DarkGray)),
            Span::styled(
                format!(
                    "{}  ({})",
                    localized_status(&theorem.status),
                    theorem.status
                ),
                Style::default().fg(status_color(&theorem.status)),
            ),
        ]),
        Line::from(vec![
            Span::styled("编号  ", Style::default().fg(Color::DarkGray)),
            Span::raw(theorem.id.clone()),
        ]),
        Line::from(vec![
            Span::styled("命题  ", Style::default().fg(Color::DarkGray)),
            Span::raw(theorem.statement.clone()),
        ]),
        Line::from(vec![
            Span::styled("依赖  ", Style::default().fg(Color::DarkGray)),
            Span::raw(if theorem.dependencies.is_empty() {
                "无".to_string()
            } else {
                theorem.dependencies.join("、")
            }),
        ]),
        Line::from(vec![
            Span::styled("标签  ", Style::default().fg(Color::DarkGray)),
            Span::raw(if theorem.tags.is_empty() {
                "无".to_string()
            } else {
                theorem.tags.join("、")
            }),
        ]),
        Line::from(vec![
            Span::styled("审计  ", Style::default().fg(Color::DarkGray)),
            Span::raw(localized_status(&theorem.audit_status)),
        ]),
        Line::from(vec![
            Span::styled("更新  ", Style::default().fg(Color::DarkGray)),
            Span::raw(theorem.last_updated.clone()),
        ]),
        Line::from(vec![
            Span::styled("来源  ", Style::default().fg(Color::DarkGray)),
            Span::raw(if theorem.source_file.is_empty() {
                "项目记录".to_string()
            } else {
                theorem.source_file.clone()
            }),
        ]),
        Line::from(""),
    ];
    lines.push(Line::from(Span::styled(
        "该子命题的实际运行步骤",
        Style::default()
            .fg(Color::Yellow)
            .add_modifier(Modifier::BOLD),
    )));
    let activities = app
        .session()
        .activities
        .iter()
        .filter(|item| item.theorem_id == theorem.id)
        .collect::<Vec<_>>();
    if activities.is_empty() {
        lines.push(Line::from(Span::styled(
            "尚未收到该子命题的动作事件。",
            Style::default().fg(Color::DarkGray),
        )));
    } else {
        for activity in activities {
            let marker = if activity.status == "COMPLETED" {
                "✓"
            } else if activity.status == "FAILED" {
                "✕"
            } else {
                app.animation.spinner()
            };
            lines.push(Line::from(vec![
                Span::styled(
                    format!("{} ", marker),
                    Style::default().fg(activity_color(&activity.status, &activity.role)),
                ),
                Span::styled(
                    format!("{}  ", localized_role(&activity.role)),
                    Style::default().fg(Color::DarkGray),
                ),
                Span::styled(
                    activity.title.clone(),
                    app.animation
                        .pulse_style(activity.status == "STARTED" || activity.status == "PROGRESS"),
                ),
            ]));
            lines.push(Line::from(Span::styled(
                format!(
                    "   动作：{} · 阶段：{} · 状态：{} · 用时：{}{}",
                    activity.action,
                    if activity.stage.is_empty() {
                        "未标注"
                    } else {
                        &activity.stage
                    },
                    activity.status,
                    activity_elapsed(activity),
                    if activity.run_id.is_empty() {
                        String::new()
                    } else {
                        format!(" · 运行：{}", activity.run_id)
                    }
                ),
                Style::default().fg(Color::DarkGray),
            )));
            if activity.history.len() > 1 {
                for entry in activity.history.iter().rev().take(4).rev() {
                    lines.push(Line::from(Span::styled(
                        format!("   历史：{}", entry),
                        Style::default().fg(Color::DarkGray),
                    )));
                }
            }
            if !activity.summary.is_empty() {
                lines.push(Line::from(Span::styled(
                    format!("   {}", activity.summary),
                    Style::default().fg(Color::Gray),
                )));
            }
            if let Some(error) = &activity.error {
                lines.push(Line::from(Span::styled(
                    format!("   错误：{}", error),
                    Style::default().fg(Color::Red),
                )));
            }
            if let Some(diagnostic) = &activity.diagnostic {
                lines.push(Line::from(Span::styled(
                    format!("   诊断：{}", diagnostic),
                    Style::default().fg(Color::DarkGray),
                )));
            }
            if let Some(action) = &activity.error_action {
                lines.push(Line::from(Span::styled(
                    format!("   建议：{}", action),
                    Style::default().fg(Color::Yellow),
                )));
            }
            if let Some(retryable) = activity.retryable {
                lines.push(Line::from(Span::styled(
                    format!("   可重试：{}", if retryable { "是" } else { "否" }),
                    Style::default().fg(Color::DarkGray),
                )));
            }
            if !activity.artifacts.is_empty() {
                lines.push(Line::from(Span::styled(
                    format!("   产物：{}", activity.artifacts.join("、")),
                    Style::default().fg(Color::DarkGray),
                )));
            }
        }
    }
    let wrapped = lines
        .into_iter()
        .flat_map(|line| {
            let text = line.to_string();
            wrap_line(&text, area.width.saturating_sub(2).max(1) as usize)
                .into_iter()
                .map(move |part| Line::from(Span::raw(part)))
        })
        .collect::<Vec<_>>();
    let viewport = area.height.saturating_sub(2) as usize;
    let max_scroll = wrapped.len().saturating_sub(viewport);
    {
        let session = app.session_mut();
        session.activity_viewport = viewport;
        if session.detail_follow {
            session.detail_scroll = max_scroll;
        } else {
            session.detail_scroll = session.detail_scroll.min(max_scroll);
        }
    }
    frame.render_widget(
        Paragraph::new(wrapped)
            .block(focused_block("子命题详情 · ↑↓ 滚动 · Esc 返回", true))
            .scroll((app.session().detail_scroll as u16, 0)),
        area,
    );
}

pub(super) fn draw_transcript(frame: &mut Frame, area: Rect, app: &mut App, full: bool) {
    app.regions.transcript = area;
    let width = area.width.saturating_sub(2).max(1) as usize;
    let cache_valid = app.transcript_cache.as_ref().is_some_and(|cache| {
        cache.project == app.project && cache.width == width && cache.full == full
    });
    if !cache_valid {
        let lines = transcript_lines(&app.session().transcript, width, full);
        app.transcript_cache = Some(super::TranscriptCache {
            project: app.project.clone(),
            width,
            full,
            lines,
        });
    }
    let cached_line_count = app
        .transcript_cache
        .as_ref()
        .expect("transcript cache initialized")
        .lines
        .len();
    let viewport = area.height.saturating_sub(2) as usize;
    {
        let session = app.session_mut();
        session.transcript_visual_lines = cached_line_count;
        session.transcript_viewport = viewport;
    }
    let cache = app
        .transcript_cache
        .as_ref()
        .expect("transcript cache initialized");
    let max = cache.lines.len().saturating_sub(viewport);
    let offset = if app.session().follow_transcript {
        max
    } else {
        app.session().transcript_offset.min(max)
    };
    let title = if full {
        if app.session().follow_transcript {
            "诊断日志 · 自动跟随 · Ctrl-T 关闭"
        } else {
            "诊断日志 · 已暂停滚动 · End 恢复"
        }
    } else if app.session().follow_transcript {
        "诊断摘要 · 自动跟随 · Ctrl-T 打开"
    } else {
        "诊断摘要 · 已暂停滚动 · End 恢复"
    };
    let styled = cache
        .lines
        .iter()
        .map(|line| {
            Line::from(Span::styled(
                line.text.as_str(),
                transcript_style(line.kind),
            ))
        })
        .collect::<Vec<_>>();
    frame.render_widget(
        Paragraph::new(styled)
            .block(focused_block(title, app.focus == Focus::Transcript))
            .scroll((offset.min(u16::MAX as usize) as u16, 0)),
        area,
    );
}

pub(super) fn draw_composer(frame: &mut Frame, area: Rect, app: &mut App) {
    app.regions.composer = area;
    let layout = app.session_mut().input_layout();
    let session = app.session();
    let cursor_line = layout.cursor_line;
    let cursor_column = layout.cursor_column;
    let inner_height = area.height.saturating_sub(2).max(1) as usize;
    let vscroll = cursor_line.saturating_sub(inner_height - 1);
    let inner_width = area.width.saturating_sub(4).max(1) as usize;
    let hscroll = cursor_column.saturating_sub(inner_width - 1);
    let color = if session.input.starts_with('/') {
        if command_candidates(&session.input).is_empty()
            && !session.input.contains(char::is_whitespace)
        {
            Color::Red
        } else {
            Color::Cyan
        }
    } else {
        Color::White
    };
    let prompt_lines = session
        .input
        .split('\n')
        .skip(vscroll)
        .take(inner_height)
        .map(|line| {
            Line::from(Span::styled(
                visible_text(line, hscroll, inner_width),
                Style::default().fg(color),
            ))
        })
        .collect::<Vec<_>>();
    let prompt = Paragraph::new(prompt_lines).block(focused_block(
        "输入 · Enter 发送 · Shift-Enter 换行",
        app.focus == Focus::Composer,
    ));
    frame.render_widget(prompt, area);
    if app.focus == Focus::Composer
        && !app.show_help
        && app.project_picker.is_none()
        && app.project_editor.is_none()
    {
        frame.set_cursor_position((
            area.x + 1 + cursor_column.saturating_sub(hscroll) as u16,
            area.y + 1 + cursor_line.saturating_sub(vscroll) as u16,
        ));
    }
    draw_completion(frame, app);
}

/// Return only the horizontal window visible in a single-line editor row.
/// This prevents a large pasted prompt from being cloned and handed to
/// Paragraph on every frame while the model is streaming.
fn visible_text(value: &str, start_width: usize, max_width: usize) -> String {
    if max_width == 0 {
        return String::new();
    }
    let mut used = 0;
    let mut visible = 0;
    let mut output = String::new();
    for ch in value.chars() {
        let width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + width <= start_width {
            used += width;
            continue;
        }
        if visible + width > max_width {
            break;
        }
        output.push(ch);
        visible += width;
        used += width;
    }
    output
}

pub(super) fn draw_project_editor(frame: &mut Frame, app: &mut App) {
    let area = centered_rect(88, 84, frame.area());
    app.regions.project_editor = area;
    frame.render_widget(Clear, area);
    let body = Layout::vertical([
        Constraint::Length(3),
        Constraint::Min(8),
        Constraint::Length(2),
    ])
    .split(area);
    app.regions.editor_id = body[0];
    app.regions.editor_goal = body[1];

    let editor = app.project_editor.as_mut().expect("project editor visible");
    let goal_line = editor
        .goal
        .chars()
        .take(editor.goal_cursor)
        .filter(|ch| *ch == '\n')
        .count();
    let goal_viewport = body[1].height.saturating_sub(2).max(1) as usize;
    if goal_line < editor.goal_scroll {
        editor.goal_scroll = goal_line;
    }
    if goal_line >= editor.goal_scroll + goal_viewport {
        editor.goal_scroll = goal_line + 1 - goal_viewport;
    }

    let id_column = editor
        .id
        .chars()
        .take(editor.id_cursor)
        .map(|ch| UnicodeWidthChar::width(ch).unwrap_or(0))
        .sum::<usize>();
    let goal_line_start = line_start(&editor.goal, editor.goal_cursor);
    let goal_column = editor
        .goal
        .chars()
        .skip(goal_line_start)
        .take(editor.goal_cursor - goal_line_start)
        .map(|ch| UnicodeWidthChar::width(ch).unwrap_or(0))
        .sum::<usize>();
    let id_width = body[0].width.saturating_sub(4).max(1) as usize;
    let goal_width = body[1].width.saturating_sub(4).max(1) as usize;
    let id_hscroll = id_column.saturating_sub(id_width - 1);
    let goal_hscroll = goal_column.saturating_sub(goal_width - 1);

    let id_focused = editor.field == ProjectEditorField::Id;
    let goal_focused = editor.field == ProjectEditorField::Goal;
    frame.render_widget(
        Paragraph::new(Text::styled(
            editor.id.clone(),
            Style::default().fg(Color::Cyan),
        ))
        .block(focused_block("项目 ID · Tab 进入目标", id_focused))
        .scroll((0, id_hscroll as u16)),
        body[0],
    );
    frame.render_widget(
        Paragraph::new(Text::styled(
            editor.goal.clone(),
            Style::default().fg(Color::White),
        ))
        .block(focused_block("核心目标 · 多行 · F2 创建", goal_focused))
        .wrap(Wrap { trim: false })
        .scroll((editor.goal_scroll as u16, goal_hscroll as u16)),
        body[1],
    );
    frame.render_widget(
        Paragraph::new(
            " Tab / Shift-Tab 切换字段   ID 中 Enter 进入目标   Enter 或 Ctrl-J 换行   Esc 取消 ",
        )
        .style(Style::default().fg(Color::DarkGray)),
        body[2],
    );
    let (cursor_x, cursor_y) = match editor.field {
        ProjectEditorField::Id => (
            body[0].x + 1 + id_column.saturating_sub(id_hscroll) as u16,
            body[0].y + 1,
        ),
        ProjectEditorField::Goal => (
            body[1].x + 1 + goal_column.saturating_sub(goal_hscroll) as u16,
            body[1].y + 1 + goal_line.saturating_sub(editor.goal_scroll) as u16,
        ),
    };
    frame.set_cursor_position((cursor_x, cursor_y));
    frame.render_widget(
        Block::default()
            .title(" 新建项目 · 写下可持久化的研究目标 ")
            .borders(Borders::ALL)
            .border_style(Style::default().fg(Color::Cyan)),
        area,
    );
}

pub(super) fn draw_completion(frame: &mut Frame, app: &mut App) {
    let candidates = app.completion_candidates();
    if candidates.is_empty() {
        app.regions.completion = Rect::default();
        return;
    }
    let height = candidates.len().min(MAX_VISIBLE_COMPLETIONS) as u16 + 2;
    let composer = app.regions.composer;
    let area = Rect::new(
        composer.x + 1,
        composer.y.saturating_sub(height),
        composer.width.saturating_sub(2),
        height,
    );
    app.regions.completion = area;
    frame.render_widget(Clear, area);
    let start = app
        .completion_index
        .saturating_add(1)
        .saturating_sub(MAX_VISIBLE_COMPLETIONS);
    let lines = candidates
        .iter()
        .skip(start)
        .take(MAX_VISIBLE_COMPLETIONS)
        .enumerate()
        .map(|(row, command)| {
            let index = start + row;
            let selected = index == app.completion_index;
            Line::from(vec![
                Span::styled(
                    if selected { "▶ " } else { "  " },
                    Style::default().fg(Color::Cyan),
                ),
                Span::styled(
                    format!("/{:<10}", command.name),
                    Style::default().fg(Color::Cyan).add_modifier(if selected {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    }),
                ),
                Span::styled(
                    command.description,
                    Style::default().fg(if selected {
                        Color::White
                    } else {
                        Color::DarkGray
                    }),
                ),
            ])
            .style(if selected {
                Style::default().bg(Color::Rgb(30, 41, 59))
            } else {
                Style::default()
            })
        })
        .collect::<Vec<_>>();
    frame.render_widget(
        Paragraph::new(lines)
            .block(
                Block::default()
                    .title(" 命令 · ↑↓ 选择 · Tab 补全 ")
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(Color::Cyan)),
            )
            .style(Style::default().bg(Color::Rgb(15, 23, 42))),
        area,
    );
}

pub(super) fn draw_project_picker(frame: &mut Frame, app: &mut App) {
    let area = centered_rect(78, 72, frame.area());
    app.regions.picker = area;
    frame.render_widget(Clear, area);
    let picker = app.project_picker.as_mut().expect("picker visible");
    let height = area.height.saturating_sub(5) as usize;
    let filtered = picker.filtered().into_iter().cloned().collect::<Vec<_>>();
    if picker.selected < picker.scroll {
        picker.scroll = picker.selected;
    }
    if picker.selected >= picker.scroll + height.max(1) {
        picker.scroll = picker.selected + 1 - height.max(1);
    }
    let mut lines = vec![
        Line::from(vec![
            Span::styled("筛选  ", Style::default().fg(Color::DarkGray)),
            Span::styled(
                format!("{}▌", picker.query),
                Style::default().fg(Color::White),
            ),
        ]),
        Line::from(""),
    ];
    for (index, choice) in filtered.iter().enumerate().skip(picker.scroll).take(height) {
        let selected = index == picker.selected;
        lines.push(
            Line::from(vec![
                Span::styled(
                    if selected { "▶ " } else { "  " },
                    Style::default().fg(Color::Cyan),
                ),
                Span::styled(
                    format!("{:<24}", choice.name),
                    Style::default().add_modifier(if selected {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    }),
                ),
                Span::styled(
                    format!("{:<18} 当前：{}", choice.id, choice.target),
                    Style::default().fg(Color::DarkGray),
                ),
            ])
            .style(if selected {
                Style::default().bg(Color::Rgb(30, 41, 59))
            } else {
                Style::default()
            }),
        );
    }
    if filtered.is_empty() {
        lines.push(Line::from(Span::styled(
            "  没有匹配项目",
            Style::default().fg(Color::Yellow),
        )));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(
                Block::default()
                    .title(" 切换项目 · /new 打开编辑器 · Enter 打开 · Esc 取消 ")
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(Color::Cyan)),
            )
            .style(Style::default().bg(Color::Rgb(15, 23, 42))),
        area,
    );
}

pub(super) fn draw_help(frame: &mut Frame, app: &App) {
    let area = centered_rect(80, 82, frame.area());
    frame.render_widget(Clear, area);
    let mut lines = vec![
        Line::from(Span::styled(
            "MathAgent 交互说明",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from("项目是隔离工作区：项目状态、会话草稿、历史、滚动位置和运行任务互不混用。"),
        Line::from("/new 打开大号多行目标编辑器；/switch 打开已有项目选择器。"),
        Line::from("/switch <path> 切换项目；/project 仍作为旧命令别名保留。"),
        Line::from(""),
        Line::from("焦点与滚动"),
        Line::from("  Tab / Shift-Tab       切换面板焦点"),
        Line::from("  鼠标点击               聚焦项目 / 活动 / 输入"),
        Line::from("  滚轮、↑↓、PgUp/PgDn    滚动当前面板"),
        Line::from("  End                    回到步骤流底部并恢复自动跟随"),
        Line::from("  Ctrl-T                 打开/关闭完整诊断日志"),
        Line::from("  /details               与 Ctrl-T 相同"),
        Line::from(""),
        Line::from("输入与补全"),
        Line::from("  /                      打开上拉命令菜单"),
        Line::from("  ↑↓ + Tab/Enter         选择并补全命令"),
        Line::from("  Shift-Enter / Ctrl-J   输入换行"),
        Line::from("  新建编辑器中 F2 创建项目；Tab 切换 ID 与目标"),
        Line::from("  Ctrl-A/E/U/W           行首、行尾、删至行首、删前词"),
        Line::from(""),
    ];
    for command in COMMANDS {
        lines.push(Line::from(vec![
            Span::styled(
                format!("  {:<24}", command.usage),
                Style::default().fg(Color::Cyan),
            ),
            Span::raw(command.description),
        ]));
    }
    lines.extend([Line::from(""), Line::from(Span::styled("说明：自由文本编辑和项目内会话隔离已经建立；自由文本到研究 Agent 的对话协议仍需后端提供，TUI 不会把普通聊天伪装成证明指令。", Style::default().fg(Color::Yellow))), Line::from(""), Line::from("Esc 或 ? 关闭帮助")]);
    frame.render_widget(
        Paragraph::new(lines)
            .block(
                Block::default()
                    .title(" 帮助 ")
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(Color::Cyan)),
            )
            .style(Style::default().bg(Color::Rgb(15, 23, 42)))
            .wrap(Wrap { trim: false })
            .scroll((app.help_scroll, 0)),
        area,
    );
}

#[derive(Debug, Eq, PartialEq)]
pub(super) struct TranscriptLine {
    pub(super) kind: TranscriptKind,
    pub(super) text: String,
}

pub(super) fn transcript_lines(
    entries: &[TranscriptEntry],
    width: usize,
    full: bool,
) -> Vec<TranscriptLine> {
    const MAX_RENDERED_ENTRIES: usize = 1200;
    let mut lines = Vec::new();
    let filtered = entries
        .iter()
        .filter(|entry| full || entry.compact)
        .rev()
        .take(MAX_RENDERED_ENTRIES)
        .collect::<Vec<_>>();
    for entry in filtered.into_iter().rev() {
        let prefix = transcript_prefix(entry.kind);
        let prefix_width = prefix
            .chars()
            .map(|ch| UnicodeWidthChar::width(ch).unwrap_or(0))
            .sum::<usize>();
        let content_width = width.saturating_sub(prefix_width).max(1);
        let wrapped = wrap_line(&entry.text, content_width);
        for (index, part) in wrapped.into_iter().enumerate() {
            lines.push(TranscriptLine {
                kind: entry.kind,
                text: if index == 0 {
                    format!("{prefix}{part}")
                } else {
                    format!("{}{part}", " ".repeat(prefix_width))
                },
            });
        }
    }
    lines
}

pub(super) fn transcript_prefix(kind: TranscriptKind) -> &'static str {
    match kind {
        TranscriptKind::User => "› ",
        TranscriptKind::System => "◆ 系统 · ",
        TranscriptKind::Activity => "● ",
        TranscriptKind::Success => "✓ ",
        TranscriptKind::Warning => "▲ ",
        TranscriptKind::Error => "✕ 错误 · ",
        TranscriptKind::Failure => "✕ ",
        TranscriptKind::Step => "├ 步骤 · ",
        TranscriptKind::Output => "  │ ",
    }
}

pub(super) fn transcript_style(kind: TranscriptKind) -> Style {
    let color = match kind {
        TranscriptKind::User | TranscriptKind::Activity => Color::Cyan,
        TranscriptKind::System => Color::DarkGray,
        TranscriptKind::Success => Color::Green,
        TranscriptKind::Warning | TranscriptKind::Step => Color::Yellow,
        TranscriptKind::Error => Color::Red,
        TranscriptKind::Failure => Color::Red,
        TranscriptKind::Output => Color::White,
    };
    let modifier = if matches!(
        kind,
        TranscriptKind::Activity
            | TranscriptKind::Success
            | TranscriptKind::Warning
            | TranscriptKind::Error
            | TranscriptKind::Failure
    ) {
        Modifier::BOLD
    } else {
        Modifier::empty()
    };
    Style::default().fg(color).add_modifier(modifier)
}

pub(super) fn wrap_line(line: &str, width: usize) -> Vec<String> {
    if line.is_empty() {
        return vec![String::new()];
    }
    let mut result = Vec::new();
    let mut current = String::new();
    let mut used = 0;
    for ch in line.chars() {
        let char_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + char_width > width && !current.is_empty() {
            result.push(std::mem::take(&mut current));
            used = 0;
        }
        current.push(ch);
        used += char_width;
    }
    if !current.is_empty() {
        result.push(current);
    }
    result
}

pub(super) fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let vertical = Layout::vertical([
        Constraint::Percentage((100 - percent_y) / 2),
        Constraint::Percentage(percent_y),
        Constraint::Percentage((100 - percent_y) / 2),
    ])
    .split(area);
    Layout::horizontal([
        Constraint::Percentage((100 - percent_x) / 2),
        Constraint::Percentage(percent_x),
        Constraint::Percentage((100 - percent_x) / 2),
    ])
    .split(vertical[1])[1]
}

pub(super) fn status_icon(status: &str) -> &'static str {
    match status {
        "PROVED" => "✓",
        "FAILED_ROUTE" => "×",
        "IN_RESEARCH" | "RUNNING" => "→",
        "PARTIAL" | "CANDIDATE_READY" => "◐",
        _ => "·",
    }
}
pub(super) fn status_color(status: &str) -> Color {
    match status {
        "PROVED" => Color::Green,
        "FAILED_ROUTE" => Color::Red,
        "IN_RESEARCH" | "RUNNING" => Color::Yellow,
        "PARTIAL" | "CANDIDATE_READY" => Color::Magenta,
        _ => Color::White,
    }
}
pub(super) fn status_style(running: bool) -> Style {
    Style::default()
        .fg(if running { Color::Yellow } else { Color::Green })
        .add_modifier(Modifier::BOLD)
}
