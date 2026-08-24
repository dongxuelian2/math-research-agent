use super::*;

impl App {
    pub(super) fn handle_key(&mut self, key: KeyEvent) {
        if key.kind == KeyEventKind::Release {
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            if self.project_editor.is_some() {
                self.project_editor = None;
            } else if self.project_picker.is_some() {
                self.project_picker = None;
            } else if self.show_help {
                self.show_help = false;
            } else if !self.session().input.is_empty() {
                self.edit_input(|input, cursor| {
                    input.clear();
                    *cursor = 0;
                });
            } else {
                self.should_quit = true;
            }
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('t') {
            self.toggle_transcript();
            return;
        }
        if self.project_editor.is_some() {
            self.handle_project_editor_key(key);
            return;
        }
        if self.project_picker.is_some() {
            self.handle_picker_key(key);
            return;
        }
        if self.show_help {
            match key.code {
                KeyCode::Esc | KeyCode::Char('?') => self.show_help = false,
                KeyCode::Up => self.help_scroll = self.help_scroll.saturating_sub(1),
                KeyCode::Down => self.help_scroll = self.help_scroll.saturating_add(1),
                KeyCode::PageUp => self.help_scroll = self.help_scroll.saturating_sub(10),
                KeyCode::PageDown => self.help_scroll = self.help_scroll.saturating_add(10),
                _ => {}
            }
            return;
        }

        if self.session().detail_open {
            match key.code {
                KeyCode::Esc => {
                    self.session_mut().detail_open = false;
                    self.session_mut().detail_scroll = 0;
                    self.session_mut().detail_follow = true;
                    self.focus = Focus::Workspace;
                }
                KeyCode::Up => self.scroll_detail(-1),
                KeyCode::Down => self.scroll_detail(1),
                KeyCode::PageUp => self.scroll_detail(-8),
                KeyCode::PageDown => self.scroll_detail(8),
                KeyCode::Home => self.scroll_detail(isize::MIN),
                KeyCode::End => self.scroll_detail(isize::MAX),
                _ => {}
            }
            return;
        }

        let completions_open = !self.completion_candidates().is_empty();
        if completions_open {
            match key.code {
                KeyCode::Up => {
                    let len = self.completion_candidates().len();
                    self.completion_index = (self.completion_index + len - 1) % len;
                    return;
                }
                KeyCode::Down => {
                    let len = self.completion_candidates().len();
                    self.completion_index = (self.completion_index + 1) % len;
                    return;
                }
                KeyCode::Tab | KeyCode::Enter => {
                    self.apply_completion();
                    return;
                }
                KeyCode::Esc => {
                    self.completion_hidden = true;
                    return;
                }
                _ => {}
            }
        }

        if key.code == KeyCode::Tab {
            self.focus = if key.modifiers.contains(KeyModifiers::SHIFT) {
                self.focus.previous()
            } else {
                self.focus.next()
            };
            return;
        }
        match self.focus {
            Focus::Composer => self.handle_composer_key(key),
            Focus::Transcript => self.handle_transcript_key(key),
            Focus::Workspace => self.handle_workspace_key(key),
        }
    }

    pub(super) fn handle_project_editor_key(&mut self, key: KeyEvent) {
        if key.code == KeyCode::F(2) {
            self.submit_project_editor();
            return;
        }
        if key.code == KeyCode::Esc {
            self.project_editor = None;
            return;
        }
        if key.code == KeyCode::Tab {
            if let Some(editor) = self.project_editor.as_mut() {
                editor.field = match editor.field {
                    ProjectEditorField::Id => ProjectEditorField::Goal,
                    ProjectEditorField::Goal => ProjectEditorField::Id,
                };
            }
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('a') => {
                    self.edit_project_editor(|text, cursor| *cursor = line_start(text, *cursor))
                }
                KeyCode::Char('e') => {
                    self.edit_project_editor(|text, cursor| *cursor = line_end(text, *cursor))
                }
                KeyCode::Char('u') => self.edit_project_editor(|text, cursor| {
                    let end = char_to_byte(text, *cursor);
                    text.replace_range(..end, "");
                    *cursor = 0;
                }),
                KeyCode::Char('w') => self.edit_project_editor(delete_previous_word),
                KeyCode::Char('j')
                    if self.project_editor_field() == Some(ProjectEditorField::Goal) =>
                {
                    self.edit_project_editor(|text, cursor| insert_char(text, cursor, '\n'))
                }
                _ => {}
            }
            return;
        }
        match key.code {
            KeyCode::Enter if self.project_editor_field() == Some(ProjectEditorField::Id) => {
                if let Some(editor) = self.project_editor.as_mut() {
                    editor.field = ProjectEditorField::Goal;
                }
            }
            KeyCode::Enter | KeyCode::Char('\n') => {
                self.edit_project_editor(|text, cursor| insert_char(text, cursor, '\n'))
            }
            KeyCode::Backspace => self.edit_project_editor(backspace),
            KeyCode::Delete => self.edit_project_editor(delete_at_cursor),
            KeyCode::Left => {
                self.edit_project_editor(|_, cursor| *cursor = cursor.saturating_sub(1))
            }
            KeyCode::Right => self.edit_project_editor(|text, cursor| {
                *cursor = (*cursor + 1).min(text.chars().count())
            }),
            KeyCode::Up => {
                if self.project_editor_field() == Some(ProjectEditorField::Goal) {
                    self.edit_project_editor(|text, cursor| {
                        *cursor = move_vertical(text, *cursor, -1)
                    });
                }
            }
            KeyCode::Down => {
                if self.project_editor_field() == Some(ProjectEditorField::Goal) {
                    self.edit_project_editor(|text, cursor| {
                        *cursor = move_vertical(text, *cursor, 1)
                    });
                }
            }
            KeyCode::PageUp => {
                if let Some(editor) = self.project_editor.as_mut() {
                    editor.goal_scroll = editor.goal_scroll.saturating_sub(5);
                }
            }
            KeyCode::PageDown => {
                if let Some(editor) = self.project_editor.as_mut() {
                    editor.goal_scroll = editor.goal_scroll.saturating_add(5);
                }
            }
            KeyCode::Home => {
                self.edit_project_editor(|text, cursor| *cursor = line_start(text, *cursor))
            }
            KeyCode::End => {
                self.edit_project_editor(|text, cursor| *cursor = line_end(text, *cursor))
            }
            KeyCode::Char(ch) => {
                self.edit_project_editor(|text, cursor| insert_char(text, cursor, ch))
            }
            _ => {}
        }
    }

    pub(super) fn project_editor_field(&self) -> Option<ProjectEditorField> {
        self.project_editor.as_ref().map(|editor| editor.field)
    }

    pub(super) fn edit_project_editor(&mut self, edit: impl FnOnce(&mut String, &mut usize)) {
        let Some(editor) = self.project_editor.as_mut() else {
            return;
        };
        match editor.field {
            ProjectEditorField::Id => edit(&mut editor.id, &mut editor.id_cursor),
            ProjectEditorField::Goal => edit(&mut editor.goal, &mut editor.goal_cursor),
        }
    }

    pub(super) fn submit_project_editor(&mut self) {
        let Some(editor) = self.project_editor.as_ref() else {
            return;
        };
        let name = editor.id.trim().to_string();
        let purpose = editor.goal.trim().to_string();
        if name.is_empty() || purpose.is_empty() {
            self.log("System · 项目 ID 和核心目标都不能为空；请继续填写，F2 创建。");
            return;
        }
        self.project_editor = None;
        self.create_project(&name, &purpose);
    }

    pub(super) fn handle_composer_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('a') => {
                    self.edit_input(|input, cursor| *cursor = line_start(input, *cursor))
                }
                KeyCode::Char('e') => {
                    self.edit_input(|input, cursor| *cursor = line_end(input, *cursor))
                }
                KeyCode::Char('u') => self.edit_input(|input, cursor| {
                    let end = char_to_byte(input, *cursor);
                    input.replace_range(..end, "");
                    *cursor = 0;
                }),
                KeyCode::Char('w') => self.edit_input(delete_previous_word),
                KeyCode::Char('j') => {
                    self.edit_input(|input, cursor| insert_char(input, cursor, '\n'))
                }
                _ => {}
            }
            return;
        }
        match key.code {
            KeyCode::Enter if key.modifiers.contains(KeyModifiers::SHIFT) => {
                self.edit_input(|input, cursor| insert_char(input, cursor, '\n'))
            }
            KeyCode::Enter => self.submit_input(),
            KeyCode::Backspace => self.edit_input(backspace),
            KeyCode::Delete => self.edit_input(delete_at_cursor),
            KeyCode::Left => self.edit_input(|_, cursor| *cursor = cursor.saturating_sub(1)),
            KeyCode::Right => {
                self.edit_input(|input, cursor| *cursor = (*cursor + 1).min(input.chars().count()))
            }
            KeyCode::Home => self.edit_input(|input, cursor| *cursor = line_start(input, *cursor)),
            KeyCode::End => self.edit_input(|input, cursor| *cursor = line_end(input, *cursor)),
            KeyCode::Up => {
                if self.session().input.contains('\n') {
                    self.edit_input(|input, cursor| *cursor = move_vertical(input, *cursor, -1));
                } else {
                    self.history_up();
                }
            }
            KeyCode::Down => {
                if self.session().input.contains('\n') {
                    self.edit_input(|input, cursor| *cursor = move_vertical(input, *cursor, 1));
                } else {
                    self.history_down();
                }
            }
            KeyCode::Esc => {
                if self.session().input.is_empty() {
                    self.focus = Focus::Transcript;
                } else {
                    self.edit_input(|input, cursor| {
                        input.clear();
                        *cursor = 0;
                    });
                }
            }
            KeyCode::Char(ch) => self.edit_input(|input, cursor| insert_char(input, cursor, ch)),
            _ => {}
        }
    }

    pub(super) fn handle_transcript_key(&mut self, key: KeyEvent) {
        let scroll_activity = !self.transcript_fullscreen;
        match key.code {
            KeyCode::Up => {
                if scroll_activity {
                    self.scroll_activity(-1)
                } else {
                    self.scroll_transcript(-1)
                }
            }
            KeyCode::Down => {
                if scroll_activity {
                    self.scroll_activity(1)
                } else {
                    self.scroll_transcript(1)
                }
            }
            KeyCode::PageUp => {
                if scroll_activity {
                    self.scroll_activity(-(self.session().activity_viewport as isize / 2).max(1))
                } else {
                    self.scroll_transcript(
                        -(self.session().transcript_viewport as isize / 2).max(1),
                    )
                }
            }
            KeyCode::PageDown => {
                if scroll_activity {
                    self.scroll_activity((self.session().activity_viewport as isize / 2).max(1))
                } else {
                    self.scroll_transcript((self.session().transcript_viewport as isize / 2).max(1))
                }
            }
            KeyCode::Home => {
                if scroll_activity {
                    self.scroll_activity(isize::MIN)
                } else {
                    self.scroll_transcript(isize::MIN)
                }
            }
            KeyCode::Esc if self.transcript_fullscreen => self.toggle_transcript(),
            KeyCode::End => {
                if scroll_activity {
                    self.scroll_activity(isize::MAX);
                } else {
                    self.session_mut().follow_transcript = true;
                }
                self.focus = Focus::Composer;
            }
            KeyCode::Esc => self.focus = Focus::Composer,
            _ => {}
        }
    }

    pub(super) fn handle_workspace_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Up => self.select_theorem(-1),
            KeyCode::Down => self.select_theorem(1),
            KeyCode::Enter => {
                let target = self
                    .session()
                    .snapshot
                    .theorems
                    .get(self.session().theorem_selected)
                    .map(|row| row.id.clone());
                if let Some(target) = target {
                    self.session_mut().snapshot.current_target = target;
                    self.session_mut().detail_open = true;
                    self.session_mut().detail_scroll = 0;
                    self.session_mut().detail_follow = true;
                    self.focus = Focus::Transcript;
                }
            }
            KeyCode::Char('p') => self.open_project_picker(),
            KeyCode::Esc => self.focus = Focus::Composer,
            _ => {}
        }
    }

    pub(super) fn handle_picker_key(&mut self, key: KeyEvent) {
        let mut chosen = None;
        if let Some(picker) = self.project_picker.as_mut() {
            let len = picker.filtered().len();
            match key.code {
                KeyCode::Esc => {
                    self.project_picker = None;
                    return;
                }
                KeyCode::Up if len > 0 => picker.selected = (picker.selected + len - 1) % len,
                KeyCode::Down if len > 0 => picker.selected = (picker.selected + 1) % len,
                KeyCode::PageUp if len > 0 => picker.selected = picker.selected.saturating_sub(5),
                KeyCode::PageDown if len > 0 => {
                    picker.selected = (picker.selected + 5).min(len - 1)
                }
                KeyCode::Backspace => {
                    picker.query.pop();
                    picker.selected = 0;
                    picker.scroll = 0;
                }
                KeyCode::Enter if len > 0 => {
                    chosen = picker
                        .filtered()
                        .get(picker.selected)
                        .map(|choice| choice.path.clone())
                }
                KeyCode::Char(ch) => {
                    picker.query.push(ch);
                    picker.selected = 0;
                    picker.scroll = 0;
                }
                _ => {}
            }
        }
        if let Some(path) = chosen {
            self.switch_project(path);
        }
    }

    pub(super) fn handle_mouse(&mut self, mouse: MouseEvent) {
        if self.project_editor.is_some() {
            self.handle_project_editor_mouse(mouse);
            return;
        }
        if self.project_picker.is_some() {
            if self
                .regions
                .picker
                .contains((mouse.column, mouse.row).into())
            {
                match mouse.kind {
                    MouseEventKind::ScrollUp => {
                        self.handle_picker_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE))
                    }
                    MouseEventKind::ScrollDown => {
                        self.handle_picker_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE))
                    }
                    MouseEventKind::Down(MouseButton::Left)
                        if mouse.row >= self.regions.picker.y + 3 =>
                    {
                        let list_row = mouse.row.saturating_sub(self.regions.picker.y + 3) as usize;
                        if let Some(picker) = self.project_picker.as_mut() {
                            let index = picker.scroll + list_row;
                            if index < picker.filtered().len() {
                                picker.selected = index;
                            }
                        }
                    }
                    _ => {}
                }
            }
            return;
        }
        let point = (mouse.column, mouse.row).into();
        if self.regions.completion.contains(point) && self.regions.completion != Rect::default() {
            match mouse.kind {
                MouseEventKind::ScrollUp => {
                    self.handle_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE))
                }
                MouseEventKind::ScrollDown => {
                    self.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE))
                }
                MouseEventKind::Down(MouseButton::Left) => {
                    let row = mouse.row.saturating_sub(self.regions.completion.y + 1) as usize;
                    let len = self.completion_candidates().len();
                    let start = self
                        .completion_index
                        .saturating_add(1)
                        .saturating_sub(MAX_VISIBLE_COMPLETIONS);
                    if start + row < len {
                        self.completion_index = start + row;
                        self.apply_completion();
                    }
                }
                _ => {}
            }
            return;
        }
        if self.regions.workspace.contains(point) {
            match mouse.kind {
                MouseEventKind::ScrollUp => self.select_theorem(-(SCROLL_STEP as isize)),
                MouseEventKind::ScrollDown => self.select_theorem(SCROLL_STEP as isize),
                MouseEventKind::Down(MouseButton::Left) => {
                    self.focus = Focus::Workspace;
                    if self.regions.theorem_list.contains(point) {
                        let row =
                            mouse.row.saturating_sub(self.regions.theorem_list.y + 1) as usize;
                        let session = self.session_mut();
                        let index = session.theorem_scroll + row;
                        if index < session.snapshot.theorems.len() {
                            session.theorem_selected = index;
                            session.detail_open = true;
                            session.detail_scroll = 0;
                            session.detail_follow = true;
                            self.focus = Focus::Transcript;
                        }
                    }
                }
                _ => {}
            }
        } else if self.regions.transcript.contains(point) {
            match mouse.kind {
                MouseEventKind::ScrollUp => {
                    if self.session().detail_open {
                        self.scroll_detail(-(SCROLL_STEP as isize));
                    } else if self.transcript_fullscreen {
                        self.scroll_transcript(-(SCROLL_STEP as isize));
                    } else {
                        self.scroll_activity(-(SCROLL_STEP as isize));
                    }
                }
                MouseEventKind::ScrollDown => {
                    if self.session().detail_open {
                        self.scroll_detail(SCROLL_STEP as isize);
                    } else if self.transcript_fullscreen {
                        self.scroll_transcript(SCROLL_STEP as isize);
                    } else {
                        self.scroll_activity(SCROLL_STEP as isize);
                    }
                }
                MouseEventKind::Down(MouseButton::Left) => self.focus = Focus::Transcript,
                _ => {}
            }
        } else if self.regions.composer.contains(point)
            && matches!(mouse.kind, MouseEventKind::Down(MouseButton::Left))
        {
            self.focus = Focus::Composer;
            let area = self.regions.composer;
            let session = self.session_mut();
            let cursor_line = session
                .input
                .chars()
                .take(session.cursor)
                .filter(|ch| *ch == '\n')
                .count();
            let inner_height = area.height.saturating_sub(2).max(1) as usize;
            let vscroll = cursor_line.saturating_sub(inner_height - 1);
            let target_line = vscroll + mouse.row.saturating_sub(area.y + 1) as usize;
            let lines = session.input.split('\n').collect::<Vec<_>>();
            if !lines.is_empty() {
                let target_line = target_line.min(lines.len() - 1);
                let line_offset = lines
                    .iter()
                    .take(target_line)
                    .map(|line| line.chars().count() + 1)
                    .sum::<usize>();
                let current_line_start = line_start(&session.input, session.cursor);
                let cursor_column = session
                    .input
                    .chars()
                    .skip(current_line_start)
                    .take(session.cursor - current_line_start)
                    .map(|ch| UnicodeWidthChar::width(ch).unwrap_or(0))
                    .sum::<usize>();
                let inner_width = area.width.saturating_sub(4).max(1) as usize;
                let hscroll = cursor_column.saturating_sub(inner_width - 1);
                let target_width = hscroll + mouse.column.saturating_sub(area.x + 1) as usize;
                session.cursor =
                    line_offset + char_index_at_width(lines[target_line], target_width);
                session.invalidate_input_layout();
            }
        }
    }

    pub(super) fn handle_project_editor_mouse(&mut self, mouse: MouseEvent) {
        let point = (mouse.column, mouse.row).into();
        if matches!(
            mouse.kind,
            MouseEventKind::ScrollUp | MouseEventKind::ScrollDown
        ) && self.regions.editor_goal.contains(point)
        {
            if let Some(editor) = self.project_editor.as_mut() {
                editor.goal_scroll = match mouse.kind {
                    MouseEventKind::ScrollUp => editor.goal_scroll.saturating_sub(3),
                    MouseEventKind::ScrollDown => editor.goal_scroll.saturating_add(3),
                    _ => editor.goal_scroll,
                };
            }
            return;
        }
        if !matches!(mouse.kind, MouseEventKind::Down(MouseButton::Left)) {
            return;
        }
        if self.regions.editor_id.contains(point) {
            if let Some(editor) = self.project_editor.as_mut() {
                editor.field = ProjectEditorField::Id;
                let width = mouse.column.saturating_sub(self.regions.editor_id.x + 1) as usize;
                editor.id_cursor = char_index_at_width(&editor.id, width);
            }
        } else if self.regions.editor_goal.contains(point) {
            if let Some(editor) = self.project_editor.as_mut() {
                editor.field = ProjectEditorField::Goal;
                let line = editor.goal_scroll
                    + mouse.row.saturating_sub(self.regions.editor_goal.y + 1) as usize;
                let lines = editor.goal.split('\n').collect::<Vec<_>>();
                let line = line.min(lines.len().saturating_sub(1));
                let line_offset = lines
                    .iter()
                    .take(line)
                    .map(|value| value.chars().count() + 1)
                    .sum::<usize>();
                let width = mouse.column.saturating_sub(self.regions.editor_goal.x + 1) as usize;
                editor.goal_cursor = line_offset + char_index_at_width(lines[line], width);
            }
        }
    }

    pub(super) fn handle_paste(&mut self, value: String) {
        if self.project_editor.is_some() {
            self.edit_project_editor(|input, cursor| insert_text(input, cursor, &value));
            return;
        }
        if self.project_picker.is_some() {
            if let Some(picker) = self.project_picker.as_mut() {
                picker.query.push_str(&value);
            }
            return;
        }
        self.focus = Focus::Composer;
        self.edit_input(|input, cursor| insert_text(input, cursor, &value));
    }

    pub(super) fn history_up(&mut self) {
        let session = self.session_mut();
        if session.history.is_empty() {
            return;
        }
        let index = session
            .history_index
            .unwrap_or(session.history.len())
            .saturating_sub(1);
        session.history_index = Some(index);
        session.input = session.history[index].clone();
        session.cursor = session.input.chars().count();
        session.invalidate_input_layout();
    }

    pub(super) fn history_down(&mut self) {
        let session = self.session_mut();
        let Some(index) = session.history_index else {
            return;
        };
        if index + 1 >= session.history.len() {
            session.history_index = None;
            session.input.clear();
        } else {
            session.history_index = Some(index + 1);
            session.input = session.history[index + 1].clone();
        }
        session.cursor = session.input.chars().count();
        session.invalidate_input_layout();
    }

    pub(super) fn select_theorem(&mut self, delta: isize) {
        let session = self.session_mut();
        let len = session.snapshot.theorems.len();
        if len == 0 {
            return;
        }
        session.theorem_selected = session
            .theorem_selected
            .saturating_add_signed(delta)
            .min(len - 1);
    }

    pub(super) fn scroll_transcript(&mut self, delta: isize) {
        let session = self.session_mut();
        let max = session
            .transcript_visual_lines
            .saturating_sub(session.transcript_viewport);
        let current = if session.follow_transcript {
            max
        } else {
            session.transcript_offset.min(max)
        };
        if delta == isize::MIN {
            session.transcript_offset = 0;
            session.follow_transcript = false;
            return;
        }
        let next = current.saturating_add_signed(delta).min(max);
        session.transcript_offset = next;
        session.follow_transcript = next == max;
    }

    pub(super) fn scroll_activity(&mut self, delta: isize) {
        let session = self.session_mut();
        let max = session
            .activities
            .len()
            .saturating_sub(session.activity_viewport.max(1));
        let current = if session.activity_follow {
            max
        } else {
            session.activity_offset.min(max)
        };
        if delta == isize::MIN {
            session.activity_offset = 0;
            session.activity_follow = false;
            return;
        }
        if delta == isize::MAX {
            session.activity_offset = max;
            session.activity_follow = true;
            return;
        }
        let next = current.saturating_add_signed(delta).min(max);
        session.activity_offset = next;
        session.activity_follow = next == max;
    }

    pub(super) fn scroll_detail(&mut self, delta: isize) {
        let session = self.session_mut();
        let max = session
            .snapshot
            .theorems
            .get(session.theorem_selected)
            .map(|theorem| {
                let body = theorem.statement.chars().count()
                    + theorem.dependencies.len() * 18
                    + theorem.tags.len() * 12
                    + session
                        .activities
                        .iter()
                        .filter(|item| item.theorem_id == theorem.id)
                        .count()
                        * 3;
                body.saturating_sub(session.activity_viewport.max(1))
            })
            .unwrap_or(0);
        if delta == isize::MIN {
            session.detail_scroll = 0;
            session.detail_follow = false;
        } else if delta == isize::MAX {
            session.detail_scroll = max;
            session.detail_follow = true;
        } else {
            session.detail_scroll = session.detail_scroll.saturating_add_signed(delta).min(max);
            session.detail_follow = session.detail_scroll == max;
        }
    }

    pub(super) fn submit_input(&mut self) {
        let command = self.session().input.trim().to_string();
        {
            let session = self.session_mut();
            session.input.clear();
            session.cursor = 0;
            session.history_index = None;
            session.invalidate_input_layout();
        }
        self.completion_hidden = false;
        if command.is_empty() {
            return;
        }
        self.session_mut().history.push(command.clone());
        if command.starts_with('/') {
            self.execute_command(&command);
        } else {
            self.log(format!("You · {command}"));
            self.log("System · 当前后端尚未提供项目内对话协议；该输入保留在此项目会话中。运行证明请用 /run，查看设计边界请用 /help。");
        }
    }

    pub(super) fn execute_command(&mut self, command: &str) {
        self.log(format!("You · {command}"));
        let mut parts = command.split_whitespace();
        let Some(name) = parts.next() else { return };
        match name {
            "/help" => self.show_help = true,
            "/quit" | "/exit" => self.should_quit = true,
            "/clear" => self.session_mut().transcript.clear(),
            "/motion" => {
                let reduced = matches!(parts.next(), Some("reduced"));
                self.animation.reduced_motion = reduced;
                self.log(if reduced {
                    "System · 已切换为减少动画模式。"
                } else {
                    "System · 已切换为完整动画模式。"
                });
            }
            "/status" | "/refresh" => {
                self.refresh_snapshot();
                self.log("System · 项目状态已刷新。");
            }
            "/switch" | "/project" => {
                let subcommand = parts.next();
                if subcommand == Some("select") {
                    self.open_project_picker();
                } else if subcommand == Some("new") {
                    let Some(name) = parts.next() else {
                        self.open_project_editor("new-project");
                        return;
                    };
                    let purpose = parts.collect::<Vec<_>>().join(" ");
                    if purpose.is_empty() {
                        self.open_project_editor(name);
                    } else {
                        self.create_project(name, &purpose);
                    }
                } else if let Some(path) = subcommand {
                    let path = resolve_path(&self.root, path);
                    if path.join("project.json").is_file() && path.join("index.json").is_file() {
                        self.switch_project(path);
                    } else {
                        self.log(format!(
                            "System · 不是有效的 MathAgent 项目：{}",
                            short_activity(
                                path.file_name()
                                    .and_then(|value| value.to_str())
                                    .unwrap_or("目标")
                            )
                        ));
                    }
                } else {
                    self.open_project_picker();
                }
            }
            "/new" => {
                let Some(name) = parts.next() else {
                    self.open_project_editor("new-project");
                    return;
                };
                let purpose = parts.collect::<Vec<_>>().join(" ");
                if purpose.is_empty() {
                    self.open_project_editor(name);
                } else {
                    self.create_project(name, &purpose);
                }
            }
            "/config" => {
                let Some(path) = parts.next() else {
                    self.log("System · 用法：/config <path>");
                    return;
                };
                self.config = resolve_path(&self.root, path);
                self.log(format!(
                    "System · 模型配置已切换：{}",
                    display_location(&self.root, &self.config)
                ));
            }
            "/run" => {
                if let Some(target) = parts.next().map(ToOwned::to_owned) {
                    self.log(format!(
                        "System · /run 不接受手动 target；项目研究由 orchestrator 从 purpose 自己分解。忽略：{target}"
                    ));
                }
                let purpose = self.session().snapshot.purpose.clone();
                if purpose.is_empty() {
                    self.log("System · 当前项目没有核心 purpose，无法启动项目级 orchestrator。用 /new 打开目标编辑器创建项目。");
                    return;
                }
                self.start_backend(
                    "Orchestrate",
                    &purpose,
                    vec![
                        "orchestrate".into(),
                        "--project".into(),
                        self.project.display().to_string(),
                        "--config".into(),
                        self.config.display().to_string(),
                    ],
                );
            }
            "/import" => {
                let raw = command.strip_prefix("/import").unwrap_or("").trim();
                let raw = raw.trim_matches(|character| character == '"' || character == '\'');
                if raw.is_empty() {
                    self.log("System · 用法：/import <论文、Markdown、文本或 PDF 路径>");
                    return;
                }
                let file = resolve_path(&self.root, raw);
                let subject = file
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or("项目文件")
                    .to_string();
                self.start_backend(
                    "Import",
                    &subject,
                    vec![
                        "add-file".into(),
                        "--project".into(),
                        self.project.display().to_string(),
                        "--file".into(),
                        file.display().to_string(),
                    ],
                );
            }
            "/stop" => self.stop_backend(),
            "/steps" => self.load_steps(),
            "/details" => self.toggle_transcript(),
            "/demo" => {
                let path = parts
                    .next()
                    .map(|value| resolve_path(&self.root, value))
                    .unwrap_or_else(|| self.root.join("projects/observatory-demo"));
                let subject = path
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or("showcase")
                    .to_string();
                self.start_backend(
                    "Explore",
                    &subject,
                    vec![
                        "demo".into(),
                        "--project".into(),
                        path.display().to_string(),
                    ],
                );
            }
            _ => self.log("System · 未知命令。输入 / 打开命令菜单。"),
        }
    }

    pub(super) fn create_project(&mut self, name: &str, purpose: &str) {
        let name = name.trim();
        let purpose = purpose.trim();
        if name.is_empty() || purpose.is_empty() {
            self.log("System · 项目 ID 和核心目标都不能为空；可用 /new 打开大号编辑器。");
            return;
        }
        let path = self.root.join("projects").join(project_slug(name));
        if path.exists() {
            self.log(format!(
                "Error · 项目 {} 已存在；请换一个名称或路径。",
                short_activity(name)
            ));
            return;
        }
        self.log(format!("System · 正在创建项目：{}", short_activity(name)));
        let result = Command::new("uv")
            .current_dir(&self.root)
            .args(["run", "--project"])
            .arg(&self.root)
            .args([
                "python",
                "-m",
                "math_research_agent.research",
                "init",
                "--project",
            ])
            .arg(&path)
            .args(["--name", name, "--purpose", purpose])
            .output();
        match result {
            Ok(output) if output.status.success() => {
                let project = normalize_path(&path);
                self.switch_project(project.clone());
                self.log(format!(
                    "System · 新项目“{}”已创建并切换；核心目标已记录。",
                    short_activity(name)
                ));
            }
            Ok(_output) => {
                self.log(
                    "Error · 创建项目失败：初始化命令未完成；请检查项目名称、目录权限和磁盘空间。",
                );
            }
            Err(_) => self.log("Error · 无法启动项目初始化；请检查 uv 和项目环境。"),
        }
    }

    pub(super) fn load_steps(&mut self) {
        let path = if self.project.join("timeline.jsonl").is_file() {
            self.project.join("timeline.jsonl")
        } else {
            self.project.join("logs").join("ui-events.jsonl")
        };
        match fs::read_to_string(&path) {
            Ok(body) => {
                self.log("System · 已载入当前项目步骤流。");
                for line in body
                    .lines()
                    .rev()
                    .take(100)
                    .collect::<Vec<_>>()
                    .into_iter()
                    .rev()
                {
                    let formatted = serde_json::from_str::<Value>(line)
                        .ok()
                        .map(|value| {
                            let kind = {
                                let action = string_field(&value, "action");
                                if action.is_empty() {
                                    string_field(&value, "kind")
                                } else {
                                    action
                                }
                            };
                            let status = string_field(&value, "status");
                            let run = string_field(&value, "run_id");
                            let theorem = string_field(&value, "theorem_id");
                            format!("Step · {kind:<28} {status:<12} {run} {theorem}")
                        })
                        .unwrap_or_else(|| {
                            "Step · 步骤记录格式无法解析；原始内容已保留在诊断日志。".to_string()
                        });
                    self.log(formatted);
                }
                self.focus = Focus::Transcript;
                self.session_mut().follow_transcript = true;
            }
            Err(error) => self.log(format!("System · 当前项目还没有统一步骤流：{error}")),
        }
    }
}
