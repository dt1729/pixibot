"""Terminal styling for the Pixibot CLI — colors, banner, spinner, streaming.

Pure-stdlib ANSI. Degrades to plain text when stdout isn't a TTY or NO_COLOR is
set, so the same functions are safe in tests and pipes.
"""

from __future__ import annotations

import itertools
import os
import sys
import threading
import time

_ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

_CODES = {
    "reset": "0", "bold": "1", "dim": "2",
    "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "gray": "90",
}

_AGENT_COLORS = ["cyan", "green", "yellow", "magenta", "blue"]


def c(text: str, *styles: str) -> str:
    """Wrap text in ANSI styles (no-op when color is disabled)."""
    if not _ENABLED or not styles:
        return text
    codes = ";".join(_CODES[s] for s in styles if s in _CODES)
    return f"\033[{codes}m{text}\033[0m" if codes else text


def agent_color(name: str) -> str:
    return _AGENT_COLORS[sum(map(ord, name)) % len(_AGENT_COLORS)]


def label(agent: str) -> str:
    return c(agent, "bold", agent_color(agent))


def banner() -> str:
    return (c("Pixibot", "bold", "magenta")
            + c("  multi-agent dev, in your terminal", "dim"))


def write(text: str) -> None:
    """Raw stream write (used for live token streaming)."""
    sys.stdout.write(text)
    sys.stdout.flush()


class Spinner:
    """A tiny threaded spinner: `with Spinner('working'): ...`."""

    def __init__(self, text: str = "working"):
        self.text = text
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self) -> "Spinner":
        if _ENABLED:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self) -> None:
        for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if self._stop.is_set():
                break
            write(f"\r{c(ch, 'cyan')} {c(self.text, 'dim')} ")
            time.sleep(0.08)

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.3)
        if _ENABLED:
            write("\r" + " " * (len(self.text) + 6) + "\r")
