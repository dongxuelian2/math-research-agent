"""Terminal UI for OpenProver."""

from .headless import HeadlessTUI

try:
    from .tui import TUI
except ModuleNotFoundError as exc:
    # The full-screen TUI currently depends on Unix-only termios/tty. Keep
    # the proving engine importable and usable in headless environments by falling
    # back to the already-supported non-interactive UI.
    if exc.name not in {"termios", "tty"}:
        raise
    TUI = HeadlessTUI

__all__ = ["TUI", "HeadlessTUI"]
