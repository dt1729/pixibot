"""Split-pane TUI for Pixibot (M31 revamp).

Keyboard-first, mouse-aware, tmux-like:
  ┌── shell (left) ──────┬── build status (top-right) ──┐
  │ command log + input  │  agents + states             │
  │                      ├── active agent (bottom-right)┤
  └──────────────────────┴──────────────────────────────┘

  • click a pane (or Ctrl+1/2/3) to focus it — focused pane is highlighted
  • Ctrl+Z zooms the focused pane to fullscreen (toggle) to see more text
  • Ctrl+←/→ resize the left|right split; Ctrl+↑/↓ resize status|agent (when a
    right pane is focused)
  • command input: ←/→/Home/End editing, ↑/↓ history, Tab completion
    (commands · workspace paths · agent ids), Ctrl-a/e/u/k/w emacs keys
  • text selection with the mouse; Ctrl+C copies the selection (Textual native,
    OSC-52 — works over SSH); Ctrl+Q quits (Ctrl+C no longer kills the app)

This module **renders**; it does not sequence builds. Every command goes to the
same :class:`~pixibot.cli.ChatSession` the plain shell drives, with the session's
progress hooks pointed at thread-safe pane writers — so the two front ends cannot
drift apart, which they had (the TUI carried its own copy of build-from parsing).
Completion lives in :mod:`pixibot.lib.completion`, where it is testable without a
terminal.

Builds run in a background worker so the shell stays usable. Requires a recent
``textual`` (pip install -U textual).

Contract: docs/contracts/pixibot.tui.md
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.dom import DOMNode
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Footer, Input, RichLog, Static

from .cli import COMMANDS, ChatSession
from .lib import completion
from .lib.logbook import get as _get_logger

_log = _get_logger("tui")

# Commands that are instant and local: handled on the UI thread, no worker.
_LOCAL = {"ls", "cd", "pwd", "cat", "tree", "status", "agents", "report",
          "form", "hard", "provider", "help", "?", "think", "updates"}
_HISTFILE = Path("~/.pixibot_history").expanduser()
_HISTORY_LINES = 1000
_PANES = ("p_shell", "p_status", "p_agent")
_THINK_CLIP = 800
_SAY_CLIP = 400


def _no_listdir(path: str) -> list[str] | None:  # noqa: ARG001 - no workspace yet
    return None


class Pane(Vertical):
    """A focusable, zoomable region (click or Ctrl+1/2/3 to focus)."""

    can_focus = True


class CommandInput(Input):
    """Shell input: ↑/↓ history, Tab completion, emacs line-editing keys."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.history: list[str] = []
        self._hidx = 0            # index into history (== len means "new line")
        self.completer: Callable[[str], tuple[str, list[str]]] | None = None
        self.on_complete_info: Callable[[list[str]], None] | None = None

    # -- history -------------------------------------------------------------
    def load_history(self) -> None:
        try:
            lines = _HISTFILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            return  # no history yet, or unreadable: start empty
        self.history = [ln for ln in lines if ln.strip()][-_HISTORY_LINES:]
        self._hidx = len(self.history)

    def remember(self, line: str) -> None:
        if not line.strip() or (self.history and self.history[-1] == line):
            self._hidx = len(self.history)
            return
        self.history.append(line)
        self._hidx = len(self.history)
        try:
            with _HISTFILE.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:  # a read-only home must not break the shell
            _log.warning("could not append to %s", _HISTFILE)

    def _recall(self, delta: int) -> None:
        if not self.history:
            return
        self._hidx = max(0, min(len(self.history), self._hidx + delta))
        self.value = "" if self._hidx == len(self.history) else self.history[self._hidx]
        self.cursor_position = len(self.value)

    # -- line editing --------------------------------------------------------
    def _home(self) -> None:
        self.cursor_position = 0

    def _end(self) -> None:
        self.cursor_position = len(self.value)

    def _kill_to_start(self) -> None:
        self.value = self.value[self.cursor_position:]
        self.cursor_position = 0

    def _kill_to_end(self) -> None:
        self.value = self.value[:self.cursor_position]

    def _kill_word(self) -> None:
        left, right = self.value[:self.cursor_position], self.value[self.cursor_position:]
        stripped = left.rstrip()
        cut = stripped.rfind(" ") + 1
        self.value = stripped[:cut] + right
        self.cursor_position = cut

    def _do_complete(self) -> None:
        if not self.completer:
            return
        new_text, candidates = self.completer(self.value)
        if new_text != self.value:
            self.value = new_text
            self.cursor_position = len(new_text)
        if len(candidates) > 1 and self.on_complete_info:
            self.on_complete_info(candidates)

    #: key -> what it does. Also the documentation of which keys this input owns;
    #: everything else falls through to Textual's own handling.
    KEYS: ClassVar[dict[str, Callable[[CommandInput], None]]] = {
        "up": lambda self: self._recall(-1),
        "down": lambda self: self._recall(1),
        "tab": lambda self: self._do_complete(),
        "ctrl+a": lambda self: self._home(),
        "ctrl+e": lambda self: self._end(),
        "ctrl+u": lambda self: self._kill_to_start(),
        "ctrl+k": lambda self: self._kill_to_end(),
        "ctrl+w": lambda self: self._kill_word(),
    }

    async def _on_key(self, event: events.Key) -> None:
        action = self.KEYS.get(event.key)
        if action is None:
            await super()._on_key(event)
            return
        event.stop()
        event.prevent_default()
        action(self)


class PixibotApp(App):  # type: ignore[type-arg]
    CSS = """
    Screen { layout: horizontal; }
    #p_shell { width: 1fr; }
    #p_right { width: 1fr; }
    .pane { border: round $panel; padding: 0 1; }
    .pane:focus, .pane:focus-within { border: round $accent; }
    #shelllog { height: 1fr; }
    #cmd { dock: bottom; border: round $panel-darken-1; }
    #p_status { height: 45%; }
    #p_agent { height: 1fr; }
    .hidden { display: none; }
    .zoom-w { width: 100% !important; }
    .zoom-h { height: 1fr !important; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+z", "zoom", "Zoom pane"),
        Binding("ctrl+1", "focus_pane('cmd')", "Shell"),
        Binding("ctrl+2", "focus_pane('p_status')", "Status"),
        Binding("ctrl+3", "focus_pane('agentlog')", "Agent"),
        Binding("ctrl+left", "resize('w', -6)", "Narrower", show=False),
        Binding("ctrl+right", "resize('w', 6)", "Wider", show=False),
        Binding("ctrl+up", "resize('h', -6)", "Shorter", show=False),
        Binding("ctrl+down", "resize('h', 6)", "Taller", show=False),
        Binding("ctrl+c", "copy", "Copy", priority=True),
        Binding("ctrl+y", "copy_pane", "Copy pane"),
    ]

    left_pct = reactive(50)
    status_pct = reactive(45)

    def __init__(self, session: ChatSession, label: str, log_path: str = ""):
        super().__init__()
        self.session = session
        self.label = label
        self.log_path = log_path
        self.build_title = "(no build yet)"
        self.agent_order: list[str] = []
        self.agent_states: dict[str, str] = {}
        self._zoomed: str | None = None

    # ── layout ──────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        with Pane(id="p_shell", classes="pane"):
            yield RichLog(id="shelllog", wrap=True, markup=True, highlight=False)
            yield CommandInput(
                placeholder="build <objective> · ls · cat <file> · ask <agent> <q> · "
                            "Tab · ↑↓ · help",
                id="cmd")
        with Vertical(id="p_right"):
            with Pane(id="p_status", classes="pane"):
                yield Static(self._status_text(), id="status")
            with Pane(id="p_agent", classes="pane"):
                yield RichLog(id="agentlog", wrap=True, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        cmd = self.query_one("#cmd", CommandInput)
        cmd.load_history()
        cmd.completer = self._complete
        cmd.on_complete_info = lambda cands: self._shell("  " + "  ".join(cands))
        self._wire_session()
        self._shell(f"[b magenta]Pixibot[/]  ·  provider: {self.label}")
        self._shell("Ctrl+1/2/3 focus · Ctrl+Z zoom · Ctrl+←→↑↓ resize · "
                    "Ctrl+C copy selection-or-pane · Ctrl+Y copy pane · Ctrl+Q quit")
        if self.log_path:
            self._shell(f"[dim]run log: {self.log_path}[/]")
        cmd.focus()

    def _wire_session(self) -> None:
        """Point the session's progress hooks at this app, across the worker thread.

        The session owns build sequencing; this is the only place the TUI couples
        to it, which is what keeps the two front ends in step.
        """
        s = self.session
        s.on_build_start = lambda objective: self.call_from_thread(
            self._set_build_title, objective)
        s.progress_start = lambda aid: self.call_from_thread(self._agent_start, aid)
        s.progress = lambda aid, terminal, files: self.call_from_thread(
            self._agent_done, aid, terminal, files)
        s.on_event = lambda aid, kind, detail: self.call_from_thread(
            self._agent_event, aid, kind, detail)

    def watch_left_pct(self, v: int) -> None:
        with contextlib.suppress(Exception):   # a resize before mount is harmless
            self.query_one("#p_shell").styles.width = f"{v}%"
            self.query_one("#p_right").styles.width = f"{100 - v}%"

    def watch_status_pct(self, v: int) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#p_status").styles.height = f"{v}%"

    # ── pane helpers ────────────────────────────────────────────────────────
    def _shell(self, text: str) -> None:
        self.query_one("#shelllog", RichLog).write(text)

    def _agent(self, text: str) -> None:
        self.query_one("#agentlog", RichLog).write(text)

    def _status_text(self) -> str:
        lines = [f"[b]build:[/] {self.build_title}"]
        for a in self.agent_order:
            state = self.agent_states.get(a, "?")
            colour = {"working…": "yellow", "DONE": "green",
                      "BLOCKED": "red"}.get(state, "white")
            lines.append(f"  {a:16} [{colour}]{state}[/]")
        if not self.agent_order:
            lines.append("  (no agents yet)")
        return "\n".join(lines)

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())

    def _active_pane(self) -> Widget:
        """The p_shell / p_status / p_agent region containing the focused widget."""
        node: DOMNode | None = self.focused
        while node is not None:
            if isinstance(node, Widget) and node.id in _PANES:
                return node
            node = node.parent
        return self.query_one("#p_shell")

    # ── actions ─────────────────────────────────────────────────────────────
    def action_focus_pane(self, widget_id: str) -> None:
        with contextlib.suppress(Exception):   # a pane may be hidden by a zoom
            self.query_one(f"#{widget_id}").focus()

    def action_resize(self, axis: str, delta: int) -> None:
        if axis == "w":
            self.left_pct = max(20, min(80, self.left_pct + delta))
        else:
            self.status_pct = max(15, min(85, self.status_pct + delta))

    def action_zoom(self) -> None:
        pane = self._active_pane()
        pid = pane.id
        others = set(_PANES) - {pid}
        for wid in ("p_shell", "p_right", *_PANES[1:]):
            self.query_one(f"#{wid}").remove_class("hidden")
        if self._zoomed == pid:          # restore
            self._zoomed = None
        else:                            # hide everything but the chain to this pane
            if pid == "p_shell":
                self.query_one("#p_right").add_class("hidden")
            else:                        # a right-column pane
                self.query_one("#p_shell").add_class("hidden")
                for wid in others & {"p_status", "p_agent"}:
                    self.query_one(f"#{wid}").add_class("hidden")
            self._zoomed = pid
        pane.focus()

    @staticmethod
    def _pane_text(pane: Widget) -> str:
        try:
            log = pane.query_one(RichLog)
        except Exception:  # noqa: BLE001 - a Static pane has no RichLog
            try:
                return str(getattr(pane.query_one(Static), "renderable", ""))
            except Exception:  # noqa: BLE001 - nor anything else readable
                return ""
        return "\n".join(str(getattr(ln, "text", ln)) for ln in log.lines)

    def _to_clipboard(self, text: str) -> str:
        """Copy to the *Windows* clipboard via clip.exe (reliable in WSL, no OSC-52
        needed); fall back to OSC-52, then to a file."""
        clip = shutil.which("clip.exe")
        if clip:
            try:
                subprocess.run([clip], input=text.encode("utf-8"),
                               check=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                _log.warning("clip.exe failed; falling back to OSC-52")
            else:
                return "windows clipboard (clip.exe)"
        try:
            self.copy_to_clipboard(text)
        except Exception:  # noqa: BLE001 - terminal may not support OSC-52
            _log.warning("OSC-52 copy failed; falling back to a file")
        else:
            return "OSC-52 (terminal clipboard)"
        target = Path("~/pixibot-clip.txt").expanduser()
        try:
            target.write_text(text, encoding="utf-8")
        except OSError:
            _log.exception("could not write the clipboard fallback file")
            return "FAILED"
        return f"file {target}"

    def action_copy(self) -> None:
        """Ctrl+C: copy the current text selection; if there's none, copy the whole
        focused pane. Always targets the Windows clipboard in WSL."""
        try:
            text = self.screen.get_selected_text() or ""
        except Exception:  # noqa: BLE001 - older textual has no selection API
            text = ""
        source = "selection"
        if not text.strip():
            pane = self._active_pane()
            text = self._pane_text(pane)
            source = str(pane.id)
        if not text.strip():
            self._shell("[dim]nothing to copy[/]")
            return
        how = self._to_clipboard(text)
        self._shell(f"[dim]copied {source} ({len(text)} chars) → {how}[/]")

    def action_copy_pane(self) -> None:
        """Ctrl+Y: copy the whole focused pane (ignores any selection)."""
        pane = self._active_pane()
        text = self._pane_text(pane)
        if text.strip():
            how = self._to_clipboard(text)
            self._shell(f"[dim]copied {pane.id} ({len(text)} chars) → {how}[/]")

    # ── completion ──────────────────────────────────────────────────────────
    def _complete(self, text: str) -> tuple[str, list[str]]:
        ws = self.session.workspace
        listdir: Callable[[str], list[str] | None] = _no_listdir
        if ws is not None:
            listdir = ws.listdir
        return completion.complete(text, completion.Sources(
            commands=COMMANDS | _LOCAL,
            cwd=self.session.cwd,
            agent_ids=lambda: [a["agent_id"] for a in self.session.bb.list_agents()],
            listdir=listdir,
        ))

    # ── live build hooks (called from the worker via call_from_thread) ───────
    def _set_build_title(self, objective: str) -> None:
        self.build_title = objective
        self.agent_order, self.agent_states = [], {}
        self._refresh_status()
        self._shell(f"[dim]building: {objective} …[/]")

    def _agent_start(self, aid: str) -> None:
        if aid not in self.agent_order:
            self.agent_order.append(aid)
        self.agent_states[aid] = "working…"
        self._refresh_status()
        self._agent(f"▸ {aid} started")

    def _agent_done(self, aid: str, terminal: str, files: list[str | None]) -> None:
        self.agent_states[aid] = terminal
        self._refresh_status()
        if files:
            self._agent(f"◉ {aid} wrote: {', '.join(str(f) for f in files)}")

    def _agent_event(self, aid: str, kind: str, detail: str) -> None:
        if kind == "tool":
            self._agent(f"  {aid} → {detail}")
        elif kind == "think":
            self._agent(f"  🧠 {aid} thinking: {detail[:_THINK_CLIP]}")
        else:
            self._agent(f"  {aid}: {detail[:_SAY_CLIP]}")

    # ── input dispatch ──────────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        self.query_one("#cmd", CommandInput).remember(line)
        self._shell(f"[b]pixibot:/{self.session.cwd} ›[/] {line}")  # noqa: RUF001 - a prompt chevron, not an operator
        first = line.split(maxsplit=1)[0].lower()
        if first in ("exit", "quit"):
            self.exit()
            return
        if first == "clear":
            self.query_one("#shelllog", RichLog).clear()
            return
        if first in _LOCAL:
            out = self.session.handle(line)
            if out:
                self._shell(out)
            return
        self._run_async(line)  # build / revise / tell / ask / chat -> worker

    @work(thread=True)
    def _run_async(self, line: str) -> None:
        """Run one slow command off the UI thread. The session does the work; the
        panes update through the hooks wired in _wire_session."""
        try:
            out = self.session.handle(line)
        except Exception as exc:  # noqa: BLE001 - a failed command must not kill the UI
            _log.exception("command failed: %s", line.split(maxsplit=1)[0])
            out = f"[red]error:[/] {exc}"
        self.call_from_thread(self._shell, out or "")


def run_tui(session: ChatSession, label: str, log_path: str = "") -> None:
    PixibotApp(session, label, log_path).run()
