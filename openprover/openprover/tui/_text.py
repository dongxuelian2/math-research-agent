"""Text rendering utilities for the TUI."""

import re

from ._colors import DIM, BOLD, RESET, COLOR_MAP
from ._types import _LogEntry, _Tab


class TextMixin:

    @staticmethod
    def _planner_live_start(tab: _Tab) -> int:
        if tab.id != "planner":
            return 0
        last_step = -1
        for idx, entry in enumerate(tab.log_lines):
            if entry.step_idx >= 0:
                last_step = idx
        return last_step + 1

    @staticmethod
    def _wrap_visual_text(
            text: str, max_w: int, continuation_prefix: str = "") -> list[str]:
        """Wrap text by visible width while preserving ANSI sequences.

        Each wrapped line is self-contained: it carries the active ANSI
        state from prior segments so that lines render correctly even
        when the viewport starts mid-paragraph (e.g. after scrolling).
        """
        if max_w <= 0:
            return [text]
        cont = continuation_prefix
        cont_w = len(cont)
        if cont_w >= max_w:
            cont = ""
            cont_w = 0
        parts: list[str] = []
        buf: list[str] = []
        visible = 0
        # Track active SGR codes so continuation lines inherit styling.
        active_sgr: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            if text[i] == '\x1b':
                m = re.match(r'\x1b\[[0-9;?]*[ -/]*[@-~]', text[i:])
                if m:
                    seq = m.group(0)
                    buf.append(seq)
                    # Track SGR sequences (ending with 'm')
                    if seq.endswith('m'):
                        if seq == RESET:
                            active_sgr.clear()
                        else:
                            active_sgr.append(seq)
                    i += len(seq)
                    continue
            ch = text[i]
            buf.append(ch)
            i += 1
            visible += 1
            if visible >= max_w:
                # Close styling on this line
                line = "".join(buf)
                if active_sgr:
                    line += RESET
                parts.append(line)
                if i < n and cont:
                    # Re-apply active styling on next line
                    buf = list(active_sgr) + [cont]
                    visible = cont_w
                else:
                    buf = list(active_sgr)
                    visible = 0
        if buf or not parts:
            parts.append("".join(buf))
        return parts

    @staticmethod
    def _is_visually_blank(line: str) -> bool:
        """Check if a rendered line contains only whitespace (ignoring ANSI)."""
        i = 0
        while i < len(line):
            if line[i] == '\x1b':
                m = re.match(r'\x1b\[[0-9;?]*[ -/]*[@-~]', line[i:])
                if m:
                    i += len(m.group(0))
                    continue
            if line[i] not in ' \t':
                return False
            i += 1
        return True

    @classmethod
    def _collapse_blank_lines(cls, lines: list[str]) -> list[str]:
        """Remove consecutive blank lines, keeping at most one."""
        result: list[str] = []
        prev_blank = False
        for line in lines:
            blank = cls._is_visually_blank(line)
            if blank and prev_blank:
                continue
            result.append(line)
            prev_blank = blank
        return result

    @staticmethod
    def _visible_len(text: str) -> int:
        """Count visible characters, ignoring ANSI escape sequences."""
        n = 0
        i = 0
        while i < len(text):
            if text[i] == '\x1b':
                m = re.match(r'\x1b\[[0-9;?]*[ -/]*[@-~]', text[i:])
                if m:
                    i += len(m.group(0))
                    continue
            n += 1
            i += 1
        return n

    @classmethod
    def _pad_to_width(cls, text: str, width: int) -> str:
        """Pad text with spaces to reach target visible width."""
        vlen = cls._visible_len(text)
        if vlen >= width:
            return text
        return text + " " * (width - vlen)

    @staticmethod
    def _leading_visible_spaces(text: str) -> int:
        """Count visible leading spaces while ignoring ANSI escapes."""
        i = 0
        n = len(text)
        spaces = 0
        while i < n:
            if text[i] == '\x1b':
                m = re.match(r'\x1b\[[0-9;?]*[ -/]*[@-~]', text[i:])
                if m:
                    i += len(m.group(0))
                    continue
            if text[i] != " ":
                break
            spaces += 1
            i += 1
        return spaces

    @staticmethod
    def _approx_token_label(text: str) -> str:
        """Return a human-friendly approximate token count like '1.6k tokens'."""
        tokens = len(text) // 4
        if tokens >= 1000:
            return f"{tokens / 1000:.1f}k tokens"
        return f"{tokens} tokens"

    @staticmethod
    def _style(text: str, color: str = "", bold: bool = False,
               dim: bool = False) -> str:
        prefix = ""
        if color:
            prefix += COLOR_MAP.get(color, "")
        if bold:
            prefix += BOLD
        if dim:
            prefix += DIM
        return f'{prefix}{text}{RESET}' if prefix else text

    @staticmethod
    def _strip_toml_block(text: str) -> str:
        """Hide planner TOML decision blocks from rendered output."""
        cleaned = re.sub(
            r"<(?:OPENPROVER_ACTION|TOML_OUTPUT)>\s*\n?.*?</(?:OPENPROVER_ACTION|TOML_OUTPUT)>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip("\n")

    @staticmethod
    def _iter_toml_segments(text: str) -> list[tuple[bool, str]]:
        """Split output into plain vs TOML-tagged blocks."""
        segments: list[tuple[bool, str]] = []
        lowers = text.lower()
        open_close = (
            ("<toml_output>", "</toml_output>"),
            ("<openprover_action>", "</openprover_action>"),
        )
        i = 0
        while i < len(text):
            next_idx = -1
            next_open = ""
            next_close = ""
            for open_tag, close_tag in open_close:
                idx = lowers.find(open_tag, i)
                if idx >= 0 and (next_idx < 0 or idx < next_idx):
                    next_idx = idx
                    next_open = open_tag
                    next_close = close_tag

            if next_idx < 0:
                if i < len(text):
                    segments.append((False, text[i:]))
                break

            if next_idx > i:
                segments.append((False, text[i:next_idx]))

            close_idx = lowers.find(next_close, next_idx + len(next_open))
            if close_idx < 0:
                # Unclosed TOML block: treat until end as TOML output.
                segments.append((True, text[next_idx:]))
                break

            end = close_idx + len(next_close)
            segments.append((True, text[next_idx:end]))
            i = end

        return segments

    @staticmethod
    def _longest_partial_tag_suffix(text: str, tags: tuple[str, ...]) -> str:
        """Return longest suffix that is a prefix of any tag."""
        best = ""
        for tag in tags:
            max_len = min(len(text), len(tag) - 1)
            for n in range(max_len, 0, -1):
                if text.endswith(tag[:n]):
                    if n > len(best):
                        best = text[-n:]
                    break
        return best

    def _max_log_text_width(self) -> int:
        """Visible width available for plain log entry text."""
        # Regular log entries are rendered with one leading visible space.
        return max(max(self.cols - 4, 20) - 1, 1)

    def _dim_separator(self) -> str:
        """Separator line that never wraps as a regular log entry."""
        return f'{DIM}{"─" * self._max_log_text_width()}{RESET}'

    def _entry_render_lines(self, tab: _Tab, entry: _LogEntry, max_w: int) -> int:
        if entry.is_trace:
            if not self.trace_visible:
                return 0
            src = entry.text.splitlines() or [""]
            total = 0
            for line in src:
                text = f'  {DIM}{line}{RESET}'
                continuation = " " * self._leading_visible_spaces(text)
                total += len(self._wrap_visual_text(
                    text, max_w, continuation_prefix=continuation))
            return total
        if entry.is_output:
            src = entry.text.splitlines() or [""]
            total = 0
            for line in src:
                text = f'  {line}'
                continuation = " " * self._leading_visible_spaces(text)
                total += len(self._wrap_visual_text(
                    text, max_w, continuation_prefix=continuation))
            return total
        base = f' {entry.text}'
        continuation = " " * self._leading_visible_spaces(base)
        return len(self._wrap_visual_text(
            base, max_w, continuation_prefix=continuation
        ))

    def _main_avail_rows(self, tab: _Tab | None = None) -> int:
        if tab is None:
            tab = self._active_tab
        cs = self._content_start
        confirm_rows = 3 if (self._confirming and not self._browsing and self.active_tab_idx == 0) else 0
        spinner_active = (tab.streaming and tab.spinner_label
                          and not (tab.trace_buf and self.trace_visible))
        spinner_rows = 1 if spinner_active else 0
        return max(self.rows - cs + 1 - confirm_rows - spinner_rows, 1)
