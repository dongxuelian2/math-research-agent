"""Navigation and scrolling for the TUI."""

from ._types import _Tab


class NavMixin:

    def _switch_tab(self, delta: int):
        if len(self.tabs) <= 1:
            return
        self._active_tab.view = self.view
        self.active_tab_idx = (self.active_tab_idx + delta) % len(self.tabs)
        self.view = self._active_tab.view
        self._redraw()

    def _nav_up(self):
        if self._nav_step == -1 and not self._nav_proposal:
            # At accept/feedback → go to proposal if available
            if self._current_proposal is not None:
                self._nav_proposal = True
                self._scroll_selection_into_view()
            elif self.step_entries:
                self._saved_worker_tabs = [t for t in self.tabs[1:] if t.id != "logs"]
                self._nav_step = len(self.step_entries) - 1
                self._load_historical_workers()
                self._scroll_selection_into_view()
        elif self._nav_proposal:
            # At proposal → go to last history entry
            self._nav_proposal = False
            if self.step_entries:
                self._saved_worker_tabs = [t for t in self.tabs[1:] if t.id != "logs"]
                self._nav_step = len(self.step_entries) - 1
                self._load_historical_workers()
                self._scroll_selection_into_view()
        elif self._nav_step > 0:
            self._nav_step -= 1
            self._load_historical_workers()
            self._scroll_selection_into_view()

    def _nav_down(self):
        if self._nav_step >= 0:
            if self._nav_step < len(self.step_entries) - 1:
                self._nav_step += 1
                self._load_historical_workers()
                self._scroll_selection_into_view()
            else:
                # Last history entry → go to proposal if available
                self._nav_step = -1
                self._restore_worker_tabs()
                if self._current_proposal is not None:
                    self._nav_proposal = True
                    self._scroll_selection_into_view()
                else:
                    # No proposal - scroll to bottom
                    self._active_tab.scroll_offset = 0
        elif self._nav_proposal:
            # Proposal → back to accept/feedback, scroll to bottom
            self._nav_proposal = False
            self._active_tab.scroll_offset = 0

    def _tab_nav_up(self, tab: _Tab):
        """Navigate up in a non-planner tab's entries."""
        if not tab.entries:
            return
        if tab.nav_idx == -1:
            tab.nav_idx = len(tab.entries) - 1
        elif tab.nav_idx > 0:
            tab.nav_idx -= 1
        self._scroll_selection_into_view()

    def _tab_nav_down(self, tab: _Tab):
        """Navigate down in a non-planner tab's entries."""
        if not tab.entries:
            return
        if tab.nav_idx >= 0:
            if tab.nav_idx < len(tab.entries) - 1:
                tab.nav_idx += 1
                self._scroll_selection_into_view()
            else:
                # Last entry → deselect and scroll to bottom
                tab.nav_idx = -1
                tab.scroll_offset = 0

    def _open_selected_action_detail(self):
        """Open detail view for the selected action in a worker tab."""
        tab = self._active_tab
        if tab.nav_idx < 0 or tab.nav_idx >= len(tab.entries):
            return
        self._step_detail_idx = tab.nav_idx
        self._step_detail_scroll = 0
        self._refresh_action_detail(tab)
        self.view = "step_detail"

    def _selection_render_range(self, tab: _Tab) -> tuple[int, int] | None:
        override = self._main_lines_max_w()
        max_w = override if override is not None else max(self.cols - 4, 20)
        planner_live_start = self._planner_live_start(tab)
        line_idx = 0
        nav = self._nav_step if tab.id == "planner" else tab.nav_idx
        if nav >= 0:
            for idx, entry in enumerate(tab.log_lines):
                if (tab.id == "planner"
                        and (entry.is_trace or entry.is_output)
                        and idx < planner_live_start):
                    continue
                rendered = self._entry_render_lines(tab, entry, max_w)
                if rendered <= 0:
                    continue
                if entry.step_idx == nav:
                    return (line_idx, line_idx + rendered - 1)
                line_idx += rendered
            return None
        if not (self._nav_proposal and tab.id == "planner" and self._proposal_log_start >= 0):
            return None
        start = None
        for idx, entry in enumerate(tab.log_lines):
            if (tab.id == "planner"
                    and (entry.is_trace or entry.is_output)
                    and idx < planner_live_start):
                continue
            rendered = self._entry_render_lines(tab, entry, max_w)
            if rendered <= 0:
                continue
            if idx >= self._proposal_log_start and start is None:
                start = line_idx
            line_idx += rendered
        if start is None:
            return None
        return (start, max(line_idx - 1, start))

    def _scroll_selection_into_view(self):
        if not self._main_visible:
            return
        tab = self._active_tab
        lines = self._build_main_lines(tab, max_w_override=self._main_lines_max_w())
        if not lines:
            tab.scroll_offset = 0
            return
        sel = self._selection_render_range(tab)
        if sel is None:
            return

        avail = self._main_avail_rows(tab)
        total = len(lines)
        max_off = self._max_scroll_offset(lines, tab)
        if tab.scroll_offset > max_off:
            tab.scroll_offset = max_off
        visible = avail - 1 if total > avail else avail
        end = total - tab.scroll_offset
        start = max(end - visible, 0)
        target_start, target_end = sel

        if target_end >= end:
            # Selection is below viewport - scroll so target_end is at bottom
            tab.scroll_offset = max(total - target_end - 1, 0)
        elif target_start < start:
            # Selection is above viewport - scroll so target_start is at top
            new_end = min(total, target_start + visible)
            tab.scroll_offset = max(total - new_end, 0)

        if tab.scroll_offset > max_off:
            tab.scroll_offset = max_off

    def _main_lines_max_w(self) -> int | None:
        """Return max_w_override for _build_main_lines based on current view."""
        if self.view == "whiteboard_split":
            return max(self.cols // 2 - 1 - 4, 10)
        return None

    def _max_scroll_offset(self, lines: list[str], tab: _Tab | None = None) -> int:
        """Max scroll offset accounting for scroll indicator row."""
        avail = self._main_avail_rows(tab)
        if len(lines) <= avail:
            return 0
        # When scrolled, indicator takes 1 row, so only avail-1 content rows
        return len(lines) - avail + 1

    def _wb_avail_rows(self) -> int:
        """Available rows for whiteboard content (full view)."""
        cs = self._content_start
        # 2 header lines: title + separator
        return max(self.rows - cs + 1 - 2, 1)

    def _wb_max_scroll(self, lines: list[str]) -> int:
        avail = self._wb_avail_rows()
        if len(lines) <= avail:
            return 0
        return len(lines) - avail + 1

    def _scroll_up(self):
        if self.view == "step_detail":
            page = max(self._step_detail_avail_rows() - 1, 1)
            self._step_detail_scroll = max(self._step_detail_scroll - page, 0)
            self._redraw()
            return
        if self.view == "detail":
            page = max(self._input_avail_rows() - 1, 1)
            self._input_scroll = max(self._input_scroll - page, 0)
            self._redraw()
            return
        if self.view == "whiteboard":
            avail = self._wb_avail_rows()
            page = max(avail - 1, 1)
            lines = self._build_whiteboard_lines(max(self.cols - 4, 20))
            max_off = self._wb_max_scroll(lines)
            self.wb_scroll_offset = min(self.wb_scroll_offset + page, max_off)
            self._redraw()
            return
        tab = self._active_tab
        page = max(self._main_avail_rows(tab) - 1, 1)
        lines = self._build_main_lines(tab, max_w_override=self._main_lines_max_w())
        max_off = self._max_scroll_offset(lines, tab)
        tab.scroll_offset = min(tab.scroll_offset + page, max_off)
        self._redraw()

    def _scroll_down(self):
        if self.view == "step_detail":
            page = max(self._step_detail_avail_rows() - 1, 1)
            max_scroll = self._step_detail_max_scroll()
            self._step_detail_scroll = min(self._step_detail_scroll + page, max_scroll)
            self._redraw()
            return
        if self.view == "detail":
            page = max(self._input_avail_rows() - 1, 1)
            max_scroll = self._input_max_scroll()
            self._input_scroll = min(self._input_scroll + page, max_scroll)
            self._redraw()
            return
        if self.view == "whiteboard":
            avail = self._wb_avail_rows()
            page = max(avail - 1, 1)
            self.wb_scroll_offset = max(self.wb_scroll_offset - page, 0)
            self._redraw()
            return
        tab = self._active_tab
        page = max(self._main_avail_rows(tab) - 1, 1)
        tab.scroll_offset = max(tab.scroll_offset - page, 0)
        self._redraw()

    def _scroll_lines_up(self, n: int = 3):
        if self.view == "step_detail":
            self._step_detail_scroll = max(self._step_detail_scroll - n, 0)
            self._redraw()
            return
        if self.view == "detail":
            self._input_scroll = max(self._input_scroll - n, 0)
            self._redraw()
            return
        if self.view == "whiteboard":
            lines = self._build_whiteboard_lines(max(self.cols - 4, 20))
            max_off = self._wb_max_scroll(lines)
            self.wb_scroll_offset = min(self.wb_scroll_offset + n, max_off)
            self._redraw()
            return
        tab = self._active_tab
        lines = self._build_main_lines(tab, max_w_override=self._main_lines_max_w())
        max_off = self._max_scroll_offset(lines, tab)
        tab.scroll_offset = min(tab.scroll_offset + n, max_off)
        self._redraw()

    def _scroll_lines_down(self, n: int = 3):
        if self.view == "step_detail":
            max_scroll = self._step_detail_max_scroll()
            self._step_detail_scroll = min(self._step_detail_scroll + n, max_scroll)
            self._redraw()
            return
        if self.view == "detail":
            max_scroll = self._input_max_scroll()
            self._input_scroll = min(self._input_scroll + n, max_scroll)
            self._redraw()
            return
        if self.view == "whiteboard":
            self.wb_scroll_offset = max(self.wb_scroll_offset - n, 0)
            self._redraw()
            return
        tab = self._active_tab
        tab.scroll_offset = max(tab.scroll_offset - n, 0)
        self._redraw()
