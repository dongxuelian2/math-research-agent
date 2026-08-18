"""Drawing and layout for the TUI."""

import sys
import time

from openprover import __version__
from ._colors import (
    DIM, BOLD, RESET, WHITE, BLUE, GREEN, YELLOW, CYAN, MAGENTA,
    SPINNER, HEADER_ROWS, HELP_TEXT,
)
from ._types import _Tab


class RenderMixin:

    def _draw_header(self):
        w = self.cols
        budget = getattr(self, '_budget_ref', None)
        bs = budget.status_str() if budget else self.budget_status
        step = f"step {self.step_num} · {bs}" if self.step_num else (bs or "")

        # Row 1
        model = getattr(self, 'model_name', '') or ''
        row1 = f'{BLUE}╭─{RESET} {BOLD}OpenProver{RESET} {DIM}v{__version__}{RESET}'
        if step:
            row1 += f' {BLUE}──{RESET} {DIM}{step}{RESET}'
        if model:
            row1 += f' {BLUE}·{RESET} {YELLOW}{model}{RESET}'
        fill1 = max(w - self._visible_len(row1) - 2, 0)
        row1 += f' {BLUE}{"─" * fill1}╮{RESET}'
        self._write_raw(f'\033[1;1H{self._pad_to_width(row1, w)}')

        # Row 2 - theorem
        name = (self.theorem_name or "").replace("\n", " ").replace("\r", "")
        # Build inner content (between │ borders), then pad to exact width
        inner = f' {WHITE}{name}{RESET}'
        inner_w = w - 2  # space for left and right │
        if self._visible_len(inner) > inner_w:
            # Truncate: rebuild with shortened name
            max_name = inner_w - 4  # " " prefix + "..."
            display_name = name[:max(max_name, 1)] + "..."
            inner = f' {WHITE}{display_name}{RESET}'
        row2 = f'{BLUE}│{RESET}{self._pad_to_width(inner, inner_w)}{BLUE}│{RESET}'
        self._write_raw(f'\033[2;1H{self._pad_to_width(row2, w)}')

        # Row 3 - hints
        help_style = BOLD if self.view == "help" else DIM
        auto_style = BOLD if self.autonomous else DIM
        trace_style = BOLD if self.trace_visible else DIM
        if self.active_tab_idx > 0:
            detail_style = BOLD if self.view == "detail" else DIM
            hints_styled = (f'{help_style}? help{RESET} {DIM}·{RESET} '
                            f'{trace_style}r reasoning{RESET} {DIM}·{RESET} '
                            f'{detail_style}d detail{RESET} {DIM}·{RESET} '
                            f'{auto_style}a autonomous{RESET}')
        else:
            if self.view == "whiteboard":
                wb_label = f'{BOLD}w whiteboard{RESET}'
            elif self.view == "whiteboard_split":
                wb_label = f'{DIM}w white{RESET}{WHITE}board{RESET}'
            else:
                wb_label = f'{DIM}w whiteboard{RESET}'
            hints_styled = (f'{help_style}? help{RESET} {DIM}·{RESET} '
                            f'{trace_style}r reasoning{RESET} {DIM}·{RESET} '
                            f'{wb_label} {DIM}·{RESET} '
                            f'{auto_style}a autonomous{RESET}')
        hints_inner = f'{hints_styled} '
        hints_pad = max(inner_w - self._visible_len(hints_inner), 0)
        row3 = f'{BLUE}│{RESET}{" " * hints_pad}{hints_inner}{BLUE}│{RESET}'
        self._write_raw(f'\033[3;1H{self._pad_to_width(row3, w)}')

        # Row 4 - bottom border + run dir + tab bar
        run_dir = getattr(self, 'work_dir', '') or ''
        tab_parts = []
        for i, tab in enumerate(self.tabs):
            name = tab.label
            if len(name) > 20:
                name = name[:17] + "..."
            if tab.done:
                name += " \u2713"
            elif self._tab_shows_spinner(tab) and not tab.is_waiting:
                name += f" {SPINNER[tab.spinner_tick]}"
            bracket = f"[{name}]"
            if i == self.active_tab_idx:
                tab_parts.append(f'{BOLD}{WHITE}{bracket}{RESET}')
            else:
                tab_parts.append(f'{DIM}{bracket}{RESET}')
        tab_str = " ".join(tab_parts)
        row4 = f'{BLUE}╰{RESET} {tab_str}'
        if run_dir:
            dir_text = run_dir
            remaining = w - self._visible_len(row4) - 2 - 1  # fill + space + dir + ╯
            max_dir = remaining - 1
            if max_dir > 0 and len(dir_text) > max_dir:
                dir_text = "\u2026" + dir_text[-(max_dir - 1):]
            fill = max(w - self._visible_len(row4) - 1 - len(dir_text) - 1, 0)
            row4 += f'{BLUE}{"─" * fill}{RESET} {DIM}{dir_text}{RESET}{BLUE}╯{RESET}'
        else:
            fill = max(w - self._visible_len(row4) - 1, 0)
            row4 += f'{BLUE}{"─" * fill}╯{RESET}'
        self._write_raw(f'\033[4;1H{self._pad_to_width(row4, w)}')

    def _draw_confirmation(self):
        fb = "".join(self._confirm_buf)
        lbl = self._confirm_accept_label
        cur = self._confirm_cursor
        self._write_raw('\n')
        if self._nav_step >= 0 or self._nav_proposal:
            self._write_raw(f' {DIM}○ {lbl}{RESET}\n')
            self._write_raw(f' {DIM}○ give feedback{RESET}')
        elif self._confirm_selected == 0:
            self._write_raw(f' {GREEN}●{RESET} {BOLD}{lbl}{RESET}\n')
            self._write_raw(f' {DIM}○ give feedback{RESET}')
        else:
            self._write_raw(f' {DIM}○ {lbl}{RESET}\n')
            self._write_raw(f' {GREEN}●{RESET} {fb}')
            # Position terminal cursor within the text
            chars_after = len(fb) - cur
            if chars_after > 0:
                self._write_raw(f'\033[{chars_after}D')

    def _build_main_lines(self, tab: _Tab | None = None,
                          max_w_override: int | None = None) -> list[str]:
        """Build flat list of rendered lines for the active tab."""
        if tab is None:
            tab = self._active_tab
        lines: list[str] = []
        max_w = max_w_override if max_w_override is not None else max(self.cols - 4, 20)
        planner_live_start = self._planner_live_start(tab)
        for idx, entry in enumerate(tab.log_lines):
            if entry.is_trace:
                if tab.id == "planner" and idx < planner_live_start:
                    continue
                if not self.trace_visible:
                    continue
                for tline in entry.text.splitlines():
                    text = f'  {DIM}{tline}{RESET}'
                    continuation = " " * self._leading_visible_spaces(text)
                    for wrapped in self._wrap_visual_text(
                            text, max_w, continuation_prefix=continuation):
                        lines.append(wrapped)
                if not entry.text.splitlines():
                    text = f'  {DIM}{RESET}'
                    for wrapped in self._wrap_visual_text(text, max_w):
                        lines.append(wrapped)
            elif entry.is_output:
                if tab.id == "planner" and idx < planner_live_start:
                    continue
                output_text = entry.text
                if (tab.id == "planner"
                        and self._confirming
                        and self._proposal_log_start >= 0):
                    output_text = self._strip_toml_block(output_text)
                rendered_any = False
                for is_toml, seg in self._iter_toml_segments(output_text):
                    if not seg:
                        continue
                    if is_toml and not self.trace_visible:
                        continue
                    for tline in seg.splitlines():
                        text = f'  {DIM}{tline}{RESET}' if is_toml else f'  {tline}'
                        continuation = " " * self._leading_visible_spaces(text)
                        for wrapped in self._wrap_visual_text(
                                text, max_w, continuation_prefix=continuation):
                            lines.append(wrapped)
                    if not seg.splitlines():
                        text = f'  {DIM}{RESET}' if is_toml else '  '
                        for wrapped in self._wrap_visual_text(text, max_w):
                            lines.append(wrapped)
                    rendered_any = True
                if not rendered_any:
                    continue
            else:
                is_entry = entry.step_idx >= 0
                # Split on embedded newlines so each sub-line wraps independently
                sub_lines = entry.text.split('\n')
                wrapped_lines: list[str] = []
                for j, sub in enumerate(sub_lines):
                    base = f' {sub}'
                    # Re-fit separator lines to current width
                    raw = sub.replace('\033[2m', '').replace('\033[0m', '').strip()
                    if raw and all(c == '─' for c in raw) and len(raw) > max_w - 2:
                        base = f' {DIM}{"─" * (max_w - 2)}{RESET}'
                    continuation = " " * self._leading_visible_spaces(base)
                    wrapped_lines.extend(self._wrap_visual_text(
                        base, max_w, continuation_prefix=continuation
                    ))
                nav = self._nav_step if tab.id == "planner" else tab.nav_idx
                is_proposal_line = (
                    self._nav_proposal and tab.id == "planner"
                    and self._proposal_log_start >= 0
                    and idx >= self._proposal_log_start
                )
                if (is_entry and entry.step_idx == nav) or is_proposal_line:
                    for wrapped in wrapped_lines:
                        lines.append(f' {GREEN}▎{RESET}{wrapped}')
                else:
                    lines.extend(wrapped_lines)
        # Active streaming content (not yet baked) - render in interleaved order
        if tab.streaming:
            for seg_kind, seg_chunks in tab.stream_segments:
                joined = "".join(seg_chunks)
                if not joined:
                    continue
                if seg_kind == "thinking":
                    if not self.trace_visible:
                        continue
                    for tline in joined.splitlines():
                        text = f'  {DIM}{tline}{RESET}'
                        continuation = " " * self._leading_visible_spaces(text)
                        for wrapped in self._wrap_visual_text(
                                text, max_w, continuation_prefix=continuation):
                            lines.append(wrapped)
                else:
                    for is_toml, seg in self._iter_toml_segments(joined):
                        if not seg:
                            continue
                        if is_toml and not self.trace_visible:
                            continue
                        for tline in seg.splitlines():
                            text = f'  {DIM}{tline}{RESET}' if is_toml else f'  {tline}'
                            continuation = " " * self._leading_visible_spaces(text)
                            for wrapped in self._wrap_visual_text(
                                    text, max_w, continuation_prefix=continuation):
                                lines.append(wrapped)
        return self._collapse_blank_lines(lines)

    def _build_step_detail_lines(self) -> list[str]:
        max_w = max(self.cols - 2, 20)
        lines: list[str] = []
        for dline in self._step_detail_text.splitlines() or [""]:
            continuation = " " * self._leading_visible_spaces(dline)
            lines.extend(self._wrap_visual_text(
                dline, max_w, continuation_prefix=continuation))
        return lines

    def _input_avail_rows(self) -> int:
        # One title line + one separator line.
        return max(self.rows - self._content_start + 1 - 2, 1)

    def _input_max_scroll(self) -> int:
        total = len(self._build_input_lines())
        avail = self._input_avail_rows()
        if total <= avail:
            return 0
        return total - avail + 1

    def _build_input_lines(self) -> list[str]:
        tab = self._active_tab
        sections: list[str] = []
        sep_w = max(self.cols - 4, 20)
        max_w = max(self.cols - 2, 20)

        def add_input_section(title: str, lines: list[str], color: str = BLUE):
            if not lines:
                return
            if sections:
                sections.append(f'  {DIM}{"─" * sep_w}{RESET}')
                sections.append("")
            sections.append(f"  {color}{BOLD}{title}{RESET}")
            for line in lines:
                text = f"  {line}" if line else ""
                continuation = " " * self._leading_visible_spaces(text)
                for wrapped in self._wrap_visual_text(
                        text, max_w, continuation_prefix=continuation):
                    sections.append(wrapped)

        summary_line = (tab.task_summary or "").strip()
        label_parts = [tab.label]
        if summary_line:
            label_parts.append(f"{DIM}-- {summary_line}{RESET}")
        add_input_section("Worker", label_parts, color=MAGENTA)

        is_verifier = bool(tab.worker_task or tab.worker_output)

        if is_verifier:
            # Verifier detail: show the original worker task and output being verified
            worker_task = (tab.worker_task or "").strip()
            add_input_section(
                "Worker Input",
                worker_task.splitlines() if worker_task else ["(no task description)"],
                color=CYAN,
            )
            worker_out = (tab.worker_output or "").strip()
            add_input_section(
                "Worker Output",
                worker_out.splitlines() if worker_out else ["(no output)"],
                color=CYAN,
            )
        else:
            desc = (tab.task_description or "").strip()
            add_input_section(
                "Input",
                desc.splitlines() if desc else ["(no task description)"],
                color=CYAN,
            )

        if tab.done:
            output_lines: list[str] = []
            for entry in tab.log_lines:
                if entry.is_trace:
                    if self.trace_visible:
                        for tline in entry.text.splitlines():
                            output_lines.append(f'{DIM}{tline}{RESET}')
                elif entry.is_output:
                    for tline in entry.text.splitlines():
                        output_lines.append(tline)
            title = "Verifier Output" if is_verifier else "Output"
            add_input_section(
                title,
                output_lines if output_lines else ["(no output)"],
                color=GREEN,
            )

        return sections

    def _step_detail_avail_rows(self) -> int:
        # One title line + one separator line.
        return max(self.rows - self._content_start + 1 - 2, 1)

    def _step_detail_max_scroll(self) -> int:
        total = len(self._build_step_detail_lines())
        avail = self._step_detail_avail_rows()
        if total <= avail:
            return 0
        # +1: when scrolled, indicator row takes 1 row from available space
        return total - avail + 1

    def _display_whiteboard(self) -> tuple[str, str]:
        """Return (whiteboard_content, label) based on navigation state.

        When a historical step is selected, returns that step's snapshot.
        Otherwise returns the current live whiteboard.
        """
        if self._nav_step >= 0 and self._nav_step < len(self.step_entries):
            entry = self.step_entries[self._nav_step]
            wb = entry.get("whiteboard", "")
            step_num = entry.get("step_num", "?")
            return wb, f"Step {step_num}"
        return self.whiteboard, "current"

    def _build_whiteboard_lines(self, max_w: int) -> list[str]:
        """Build rendered whiteboard lines from self.whiteboard."""
        from ._colors import CYAN
        lines: list[str] = []
        sections: list[str] = []
        current_title = "Notes"
        current_lines: list[str] = []

        def flush():
            if not current_lines:
                return
            if sections:
                sections.append("")
            sections.append(f"  {CYAN}{BOLD}{current_title}{RESET}")
            for line in current_lines:
                sections.append(f"  {line}" if line else "")

        wb_content, _ = self._display_whiteboard()
        source_lines = wb_content.splitlines() or ["(whiteboard is empty)"]
        for wline in source_lines:
            stripped = wline.strip()
            if stripped.startswith("## "):
                flush()
                current_title = stripped[3:].strip() or "Notes"
                current_lines = []
                continue
            if stripped.startswith("### "):
                sub_title = stripped[4:].strip()
                current_lines.append(f"{BOLD}{sub_title}{RESET}")
                continue
            current_lines.append(wline)
        flush()
        if not sections:
            sections.append("  (whiteboard is empty)")

        for sline in sections:
            continuation = " " * self._leading_visible_spaces(sline)
            for wrapped in self._wrap_visual_text(
                    sline, max_w, continuation_prefix=continuation):
                lines.append(wrapped)
        return lines

    def _redraw_split(self, cs: int):
        """Redraw split view using per-row cursor positioning (no bulk clear)."""
        tab = self._active_tab
        left_w = self.cols // 2 - 1
        right_w = self.cols - left_w - 1
        left_max_w = max(left_w - 4, 10)
        left_lines = self._build_main_lines(tab, max_w_override=left_max_w)
        _, wb_label = self._display_whiteboard()
        right_max_w = max(right_w - 2, 10)
        wb_header = f' {BOLD}WHITEBOARD{RESET} {DIM}[{wb_label}]{RESET}'
        wb_sep = f' {DIM}{"─" * right_max_w}{RESET}'
        all_right_lines = [wb_header, wb_sep] + self._build_whiteboard_lines(right_max_w)
        sep = f'{DIM}│{RESET}'

        confirming = (self._confirming and not self._browsing
                      and self.active_tab_idx == 0)
        spinner_active = (tab.streaming and tab.spinner_label
                          and not self._has_visible_stream_content(tab))
        total_rows = self.rows - cs + 1

        # Right column: whiteboard viewport
        if len(all_right_lines) > total_rows:
            wb_max = len(all_right_lines) - total_rows + 1
            if self.wb_scroll_offset > wb_max:
                self.wb_scroll_offset = wb_max
            wb_visible = total_rows - 1 if len(all_right_lines) > total_rows else total_rows
            wb_end = len(all_right_lines) - self.wb_scroll_offset
            wb_start = max(wb_end - wb_visible, 0)
            right_lines = all_right_lines[wb_start:wb_end]
        else:
            self.wb_scroll_offset = 0
            right_lines = all_right_lines

        # Left column: content viewport
        avail = self._main_avail_rows(tab)
        max_off = self._max_scroll_offset(left_lines, tab)
        if tab.scroll_offset > max_off:
            tab.scroll_offset = max_off
        visible = avail - 1 if len(left_lines) > avail else avail
        end = len(left_lines) - tab.scroll_offset
        start = max(end - visible, 0)
        left_view = left_lines[start:end]

        if spinner_active:
            ch = SPINNER[tab.spinner_tick]
            elapsed = int(time.monotonic() - tab.spinner_start)
            status = self._spinner_status(elapsed, tab.spinner_tokens)
            bar = f' {GREEN}▎{RESET}' if self._spinner_selected(tab) else '  '
            left_view.append(f'{bar}{DIM}{ch} {tab.spinner_label} {status}{RESET}')

        # Build confirmation lines for left column
        confirm_lines: list[str] = []
        if confirming:
            fb = "".join(self._confirm_buf)
            lbl = self._confirm_accept_label
            if self._nav_step >= 0 or self._nav_proposal:
                confirm_lines.append("")
                confirm_lines.append(f' {DIM}○ {lbl}{RESET}')
                confirm_lines.append(f' {DIM}○ give feedback{RESET}')
            elif self._confirm_selected == 0:
                confirm_lines.append("")
                confirm_lines.append(f' {GREEN}●{RESET} {BOLD}{lbl}{RESET}')
                confirm_lines.append(f' {DIM}○ give feedback{RESET}')
            else:
                confirm_lines.append("")
                confirm_lines.append(f' {DIM}○ {lbl}{RESET}')
                confirm_lines.append(f' {GREEN}●{RESET} {fb}')

        # Scroll indicator line
        above = start
        below = tab.scroll_offset
        scroll_line = ""
        if above > 0 or below > 0:
            parts = []
            if above > 0:
                parts.append(f'↑ {above} above')
            if below > 0:
                parts.append(f'↓ {below} below')
            scroll_line = f' {DIM}{" · ".join(parts)}{RESET}'

        # Render all rows: overwrite in place (no erase, no flicker)
        for i in range(total_rows):
            right = right_lines[i] if i < len(right_lines) else ""
            if i < len(left_view):
                left = left_view[i]
            elif i < len(left_view) + len(confirm_lines):
                left = confirm_lines[i - len(left_view)]
            elif i == total_rows - 1 and scroll_line:
                left = scroll_line
            else:
                left = ""
            row = cs + i
            padded_right = self._pad_to_width(right, right_w)
            self._write_raw(
                f'\033[{row};1H'
                f'{self._pad_to_width(left, left_w)}{sep}{padded_right}')

        # Position cursor for feedback editing
        if confirming and self._confirm_selected == 1:
            fb_row = cs + len(left_view) + len(confirm_lines) - 1
            cur = self._confirm_cursor
            fb_col = 3 + cur  # " ● " = 3 visible chars before text
            self._write_raw(f'\033[{fb_row};{fb_col + 1}H\033[?25h')

    def _redraw(self):
        with self._write_lock:
            # Buffer the entire frame and write it in one shot to avoid flicker
            self._buf = []
            self._write_raw('\033[?25l')
            self._draw_header()
            cs = self._content_start

            # Split view: skip bulk clear, use per-row cursor-addressed writes
            if self.view == "whiteboard_split":
                self._redraw_split(cs)
                frame = "".join(self._buf)
                self._buf = None
                sys.stdout.write(frame)
                sys.stdout.flush()
                return

            if self.view == "main":
                tab = self._active_tab
                lines = self._build_main_lines(tab)
                spinner_active = (tab.streaming and tab.spinner_label
                                  and not self._has_visible_stream_content(tab))
                avail = self._main_avail_rows(tab)
                confirming = (self._confirming and not self._browsing
                              and self.active_tab_idx == 0)

                # Clamp scroll offset
                max_off = self._max_scroll_offset(lines, tab)
                if tab.scroll_offset > max_off:
                    tab.scroll_offset = max_off

                # Viewport window (indicator takes 1 row when content overflows)
                visible = avail - 1 if len(lines) > avail else avail
                end = len(lines) - tab.scroll_offset
                start = max(end - visible, 0)

                # Build all content rows
                content_rows = list(lines[start:end])

                if spinner_active:
                    ch = SPINNER[tab.spinner_tick]
                    elapsed = int(time.monotonic() - tab.spinner_start)
                    status = self._spinner_status(elapsed, tab.spinner_tokens)
                    bar = f' {GREEN}▎{RESET}' if self._spinner_selected(tab) else '  '
                    content_rows.append(
                        f'{bar}{DIM}{ch} {tab.spinner_label} {status}{RESET}')

                # Confirmation lines (inlined, like _redraw_split)
                if confirming:
                    fb = "".join(self._confirm_buf)
                    lbl = self._confirm_accept_label
                    if self._nav_step >= 0 or self._nav_proposal:
                        content_rows.append("")
                        content_rows.append(f' {DIM}○ {lbl}{RESET}')
                        content_rows.append(f' {DIM}○ give feedback{RESET}')
                    elif self._confirm_selected == 0:
                        content_rows.append("")
                        content_rows.append(f' {GREEN}●{RESET} {BOLD}{lbl}{RESET}')
                        content_rows.append(f' {DIM}○ give feedback{RESET}')
                    else:
                        content_rows.append("")
                        content_rows.append(f' {DIM}○ {lbl}{RESET}')
                        content_rows.append(f' {GREEN}●{RESET} {fb}')

                # Scroll indicator
                above = start
                below = tab.scroll_offset
                scroll_line = ""
                if above > 0 or below > 0:
                    parts = []
                    if above > 0:
                        parts.append(f'↑ {above} above')
                    if below > 0:
                        parts.append(f'↓ {below} below')
                    scroll_line = f' {DIM}{" · ".join(parts)}{RESET}'

                # Render all rows: overwrite in place (no erase, no flicker)
                total_rows = self.rows - cs + 1
                for i in range(total_rows):
                    row = cs + i
                    if i < len(content_rows):
                        c = content_rows[i]
                    elif i == total_rows - 1 and scroll_line:
                        c = scroll_line
                    else:
                        c = ""
                    self._write_raw(
                        f'\033[{row};1H{self._pad_to_width(c, self.cols)}')

                # Position cursor for feedback editing
                if confirming and self._confirm_selected == 1:
                    fb_row = cs + len(content_rows) - 1
                    fb_col = 3 + self._confirm_cursor
                    self._write_raw(
                        f'\033[{fb_row};{fb_col + 1}H\033[?25h')
                elif confirming:
                    self._write_raw('\033[?25h')
                elif spinner_active:
                    # Leave cursor on spinner row for _update_spinner
                    spinner_row = cs + len(content_rows) - 1
                    self._write_raw(f'\033[{spinner_row};1H')
                else:
                    # Position cursor after last content for streaming writes
                    self._write_raw(
                        f'\033[{min(cs + len(content_rows), self.rows)};1H')
            else:
                # Non-main views: clear rows then render sequentially
                for row in range(cs, self.rows + 1):
                    self._write_raw(f'\033[{row};1H\033[2K')
                self._write_raw(f'\033[{cs};1H')

                if self.view == "whiteboard":
                    _, wb_label = self._display_whiteboard()
                    self._write_raw(f'  {BOLD}WHITEBOARD{RESET} {DIM}[{wb_label}] (esc to return){RESET}\n')
                    max_w = max(self.cols - 4, 20)
                    self._write_raw(f'  {DIM}{"─" * max_w}{RESET}\n')
                    wb_lines = self._build_whiteboard_lines(max_w)
                    avail = self._wb_avail_rows()
                    max_off = self._wb_max_scroll(wb_lines)
                    if self.wb_scroll_offset > max_off:
                        self.wb_scroll_offset = max_off
                    visible = avail - 1 if len(wb_lines) > avail else avail
                    wb_end = len(wb_lines) - self.wb_scroll_offset
                    wb_start = max(wb_end - visible, 0)
                    for iline in wb_lines[wb_start:wb_end]:
                        self._write_raw(f'{iline}\n')
                    above = wb_start
                    below = self.wb_scroll_offset
                    if above > 0 or below > 0:
                        parts = []
                        if above > 0:
                            parts.append(f'↑ {above} above')
                        if below > 0:
                            parts.append(f'↓ {below} below')
                        indicator = f' {DIM}{" · ".join(parts)}{RESET}'
                        self._write_raw(f'\033[{self.rows};1H\033[2K{indicator}')
                elif self.view == "detail":
                    tab = self._active_tab
                    status_badge = (
                        f"{GREEN}● completed{RESET}" if tab.done
                        else f"{CYAN}● running{RESET}"
                    )
                    self._write_raw(f'  {BOLD}Worker Detail{RESET}  {status_badge} {DIM}(esc to return){RESET}\n')
                    self._write_raw(f'  {DIM}{"─" * max(self.cols - 4, 20)}{RESET}\n')
                    lines = self._build_input_lines()
                    avail = self._input_avail_rows()
                    max_scroll = self._input_max_scroll()
                    if self._input_scroll > max_scroll:
                        self._input_scroll = max_scroll
                    visible = avail - 1 if len(lines) > avail else avail
                    start = self._input_scroll
                    end = min(start + visible, len(lines))
                    for dline in lines[start:end]:
                        self._write_raw(f'{dline}\n')

                    above = start
                    below = max(len(lines) - end, 0)
                    if above > 0 or below > 0:
                        parts = []
                        if above > 0:
                            parts.append(f'↑ {above} above')
                        if below > 0:
                            parts.append(f'↓ {below} below')
                        indicator = f' {DIM}{" · ".join(parts)}{RESET}'
                        self._write_raw(f'\033[{self.rows};1H\033[2K{indicator}')
                elif self.view == "help":
                    self._write_raw(HELP_TEXT)
                    budget = getattr(self, '_budget_ref', None)
                    if budget:
                        from ..budget import _fmt_tokens, _fmt_duration
                        import time as _time
                        elapsed = int(_time.monotonic() - budget.start_time)
                        tok_str = _fmt_tokens(budget.total_output_tokens)
                        self._write_raw(f'\n  {BOLD}Current usage{RESET}\n\n')
                        self._write_raw(f'    {DIM}{"elapsed":<16}{RESET}{_fmt_duration(elapsed)}\n')
                        self._write_raw(f'    {DIM}{"output tokens":<16}{RESET}{tok_str}\n')
                        self._write_raw(f'    {DIM}{"budget":<16}{RESET}{budget.summary_str()}\n')
                    if self.run_params:
                        self._write_raw(f'\n  {BOLD}Parameters{RESET}\n\n')
                        for key, val in self.run_params.items():
                            self._write_raw(f'    {DIM}{key:<16}{RESET}{val}\n')
                elif self.view == "step_detail":
                    self._write_raw(f'  {BOLD}{self._step_detail_title}{RESET}')
                    self._write_raw(f' {DIM}(esc to return){RESET}\n')
                    self._write_raw(f'  {DIM}{"─" * max(self.cols - 4, 20)}{RESET}\n')
                    lines = self._build_step_detail_lines()
                    avail = self._step_detail_avail_rows()
                    max_scroll = self._step_detail_max_scroll()
                    if self._step_detail_scroll > max_scroll:
                        self._step_detail_scroll = max_scroll
                    # Reserve 1 row for indicator when content overflows
                    visible = avail - 1 if len(lines) > avail else avail
                    start = self._step_detail_scroll
                    end = min(start + visible, len(lines))
                    for dline in lines[start:end]:
                        self._write_raw(f'{dline}\n')

                    above = start
                    below = max(len(lines) - end, 0)
                    if above > 0 or below > 0:
                        parts = []
                        if above > 0:
                            parts.append(f'↑ {above} above')
                        if below > 0:
                            parts.append(f'↓ {below} below')
                        indicator = f' {DIM}{" · ".join(parts)}{RESET}'
                        self._write_raw(f'\033[{self.rows};1H\033[2K{indicator}')

            frame = "".join(self._buf)
            self._buf = None
            sys.stdout.write(frame)
            sys.stdout.flush()
