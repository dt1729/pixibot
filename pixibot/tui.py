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

Builds run in a background worker so the shell stays usable. Requires a recent
``textual`` (pip install -U textual).
"""

from __future__ import annotations

import os

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Input, RichLog, Static

from . import intake
from .cli import COMMANDS, ChatSession
from .engine import default_input

_LOCAL = {"ls", "cd", "pwd", "cat", "tree", "status", "agents", "report",
          "form", "hard", "provider", "help", "?", "think", "updates"}
_HISTFILE = os.path.expanduser("~/.pixibot_history")


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    s1, s2 = min(strings), max(strings)
    for i, c in enumerate(s1):
        if c != s2[i]:
            return s1[:i]
    return s1


class Pane(Vertical):
    """A focusable, zoomable region (click or Ctrl+1/2/3 to focus)."""
    can_focus = True


class CommandInput(Input):
    """Shell input: ↑/↓ history, Tab completion, emacs line-editing keys."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.history: list[str] = []
        self._hidx = 0            # index into history (== len means "new line")
        self.completer = None     # set by the app: fn(text) -> (new_text, candidates)
        self.on_complete_info = None  # fn(list[str]) -> None, to show candidates

    def load_history(self) -> None:
        try:
            with open(_HISTFILE, encoding="utf-8") as f:
                self.history = [ln.rstrip("\n") for ln in f if ln.strip()][-1000:]
        except OSError:
            self.history = []
        self._hidx = len(self.history)

    def remember(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
            try:
                with open(_HISTFILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        self._hidx = len(self.history)

    def _recall(self, delta: int) -> None:
        if not self.history:
            return
        self._hidx = max(0, min(len(self.history), self._hidx + delta))
        self.value = "" if self._hidx == len(self.history) else self.history[self._hidx]
        self.cursor_position = len(self.value)

    async def _on_key(self, event) -> None:  # noqa: C901 - a small key router
        k = event.key
        if k == "up":
            event.stop(); event.prevent_default(); self._recall(-1); return
        if k == "down":
            event.stop(); event.prevent_default(); self._recall(1); return
        if k == "tab":
            event.stop(); event.prevent_default(); self._do_complete(); return
        if k == "ctrl+a":
            event.stop(); event.prevent_default(); self.cursor_position = 0; return
        if k == "ctrl+e":
            event.stop(); event.prevent_default(); self.cursor_position = len(self.value); return
        if k == "ctrl+u":
            event.stop(); event.prevent_default()
            self.value = self.value[self.cursor_position:]; self.cursor_position = 0; return
        if k == "ctrl+k":
            event.stop(); event.prevent_default(); self.value = self.value[:self.cursor_position]; return
        if k == "ctrl+w":
            event.stop(); event.prevent_default()
            left, right = self.value[:self.cursor_position], self.value[self.cursor_position:]
            stripped = left.rstrip()
            cut = stripped.rfind(" ") + 1
            self.value = stripped[:cut] + right
            self.cursor_position = cut
            return
        await super()._on_key(event)

    def _do_complete(self) -> None:
        if not self.completer:
            return
        new_text, candidates = self.completer(self.value)
        if new_text != self.value:
            self.value = new_text
            self.cursor_position = len(new_text)
        if len(candidates) > 1 and self.on_complete_info:
            self.on_complete_info(candidates)


class PixibotApp(App):
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

    BINDINGS = [
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

    # ── layout ────────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        with Pane(id="p_shell", classes="pane"):
            yield RichLog(id="shelllog", wrap=True, markup=True, highlight=False)
            yield CommandInput(
                placeholder="build <objective> · ls · cat <file> · ask <agent> <q> · Tab · ↑↓ · help",
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
        self._shell(f"[b magenta]Pixibot[/]  ·  provider: {self.label}")
        self._shell("Ctrl+1/2/3 focus · Ctrl+Z zoom · Ctrl+←→↑↓ resize · "
                    "Ctrl+C copy selection-or-pane · Ctrl+Y copy pane · Ctrl+Q quit")
        if self.log_path:
            self._shell(f"[dim]run log: {self.log_path}[/]")
        cmd.focus()

    def watch_left_pct(self, v: int) -> None:
        try:
            self.query_one("#p_shell").styles.width = f"{v}%"
            self.query_one("#p_right").styles.width = f"{100 - v}%"
        except Exception:
            pass

    def watch_status_pct(self, v: int) -> None:
        try:
            self.query_one("#p_status").styles.height = f"{v}%"
        except Exception:
            pass

    # ── pane helpers ────────────────────────────────────────────────────────
    def _shell(self, text: str) -> None:
        self.query_one("#shelllog", RichLog).write(text)

    def _agent(self, text: str) -> None:
        self.query_one("#agentlog", RichLog).write(text)

    def _status_text(self) -> str:
        lines = [f"[b]build:[/] {self.build_title}"]
        for a in self.agent_order:
            st = self.agent_states.get(a, "?")
            colour = {"working…": "yellow", "DONE": "green", "BLOCKED": "red"}.get(st, "white")
            lines.append(f"  {a:16} [{colour}]{st}[/]")
        if not self.agent_order:
            lines.append("  (no agents yet)")
        return "\n".join(lines)

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())

    def _active_pane(self):
        """The p_shell / p_status / p_agent region containing the focused widget."""
        node = self.focused
        while node is not None:
            if getattr(node, "id", None) in ("p_shell", "p_status", "p_agent"):
                return node
            node = node.parent
        return self.query_one("#p_shell")

    # ── actions ───────────────────────────────────────────────────────────────
    def action_focus_pane(self, widget_id: str) -> None:
        try:
            self.query_one(f"#{widget_id}").focus()
        except Exception:
            pass

    def action_resize(self, axis: str, delta: int) -> None:
        if axis == "w":
            self.left_pct = max(20, min(80, self.left_pct + delta))
        else:
            self.status_pct = max(15, min(85, self.status_pct + delta))

    def action_zoom(self) -> None:
        pane = self._active_pane()
        pid = pane.id
        others = {"p_shell", "p_status", "p_agent"} - {pid}
        if self._zoomed == pid:  # restore
            for wid in ("p_shell", "p_right", "p_status", "p_agent"):
                w = self.query_one(f"#{wid}")
                w.remove_class("hidden")
            self._zoomed = None
        else:
            # hide everything, then reveal the chain down to the zoomed pane
            for wid in ("p_shell", "p_right", "p_status", "p_agent"):
                self.query_one(f"#{wid}").remove_class("hidden")
            if pid == "p_shell":
                self.query_one("#p_right").add_class("hidden")
            else:  # a right-column pane
                self.query_one("#p_shell").add_class("hidden")
                for wid in others & {"p_status", "p_agent"}:
                    self.query_one(f"#{wid}").add_class("hidden")
            self._zoomed = pid
        pane.focus()

    @staticmethod
    def _pane_text(pane) -> str:
        try:
            log = pane.query_one(RichLog)
            return "\n".join(str(getattr(ln, "text", ln)) for ln in log.lines)
        except Exception:
            try:
                return str(pane.query_one(Static).renderable)
            except Exception:
                return ""

    def _to_clipboard(self, text: str) -> str:
        """Copy to the *Windows* clipboard via clip.exe (reliable in WSL, no OSC-52
        needed); fall back to OSC-52, then to a file."""
        import shutil
        import subprocess
        clip = shutil.which("clip.exe")
        if clip:
            try:
                subprocess.run([clip], input=text.encode("utf-8"), check=True, timeout=5)
                return "windows clipboard (clip.exe)"
            except Exception:
                pass
        try:
            self.copy_to_clipboard(text)
            return "OSC-52 (terminal clipboard)"
        except Exception:
            pass
        try:
            p = os.path.expanduser("~/pixibot-clip.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return f"file {p}"
        except Exception:
            return "FAILED"

    def action_copy(self) -> None:
        """Ctrl+C: copy the current text selection; if there's none, copy the whole
        focused pane. Always targets the Windows clipboard in WSL."""
        text = ""
        try:
            text = self.screen.get_selected_text() or ""
        except Exception:
            text = ""
        source = "selection"
        if not text.strip():
            text = self._pane_text(self._active_pane())
            source = self._active_pane().id
        if not text.strip():
            self._shell("[dim]nothing to copy[/]")
            return
        how = self._to_clipboard(text)
        self._shell(f"[dim]copied {source} ({len(text)} chars) → {how}[/]")

    def action_copy_pane(self) -> None:
        """Ctrl+Y: copy the whole focused pane (ignores any selection)."""
        text = self._pane_text(self._active_pane())
        if text.strip():
            how = self._to_clipboard(text)
            self._shell(f"[dim]copied {self._active_pane().id} ({len(text)} chars) → {how}[/]")

    # ── completion ─────────────────────────────────────────────────────────────
    def _complete(self, text: str):
        """Return (completed_text, candidates). Completes commands, then — by the
        first word — workspace paths (cd/cat/ls/tree) or agent ids (ask/tell/think)."""
        parts = text.split(" ")
        if len(parts) <= 1:                       # completing the command word
            cands = sorted(c for c in (COMMANDS | _LOCAL) if c.startswith(text))
            prefix = ""
        else:                                     # completing the last argument
            cmd, frag = parts[0].lower(), parts[-1]
            prefix = text[: len(text) - len(frag)]
            if cmd in ("cd", "cat", "ls", "tree"):
                cands = self._path_candidates(frag)
            elif cmd in ("ask", "tell", "think"):
                cands = sorted(a["agent_id"] for a in self.session.bb.list_agents()
                               if a["agent_id"].startswith(frag))
            else:
                cands = []
        if not cands:
            return text, []
        completed = (cands[0] + " ") if len(cands) == 1 else _common_prefix(cands)
        return prefix + completed, cands

    def _path_candidates(self, frag: str) -> list[str]:
        ws = self.session.workspace
        if ws is None:
            return []
        base = self.session.cwd
        directory = os.path.dirname(frag)
        look = os.path.join(base, directory) if directory else base
        try:
            entries = ws.listdir(look) or []
        except Exception:
            entries = []
        leaf = os.path.basename(frag)
        return sorted((os.path.join(directory, e) if directory else e)
                      for e in entries if e.startswith(leaf))

    # ── live build hooks (called from the worker via call_from_thread) ─────────
    def _set_build_title(self, objective: str) -> None:
        self.build_title = objective
        self.agent_order, self.agent_states = [], {}
        self._refresh_status()

    def _agent_start(self, aid: str) -> None:
        if aid not in self.agent_order:
            self.agent_order.append(aid)
        self.agent_states[aid] = "working…"
        self._refresh_status()
        self._agent(f"▸ {aid} started")

    def _agent_done(self, aid: str, terminal: str, files) -> None:
        self.agent_states[aid] = terminal
        self._refresh_status()
        if files:
            self._agent(f"◉ {aid} wrote: {', '.join(files)}")

    def _agent_event(self, aid: str, kind: str, detail: str) -> None:
        if kind == "tool":
            self._agent(f"  {aid} → {detail}")
        elif kind == "think":
            self._agent(f"  🧠 {aid} thinking: {detail[:800]}")
        else:
            self._agent(f"  {aid}: {detail[:400]}")

    # ── input dispatch ─────────────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        self.query_one("#cmd", CommandInput).remember(line)
        self._shell(f"[b]pixibot:/{self.session.cwd} ›[/] {line}")
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
        first = line.split(maxsplit=1)[0].lower()
        if first in ("build", "build-from", "buildfrom"):
            self._do_build(line)
        else:
            try:
                out = self.session.handle(line)
            except Exception as exc:  # noqa: BLE001
                out = f"error: {exc}"
            self.call_from_thread(self._shell, out or "")

    def _do_build(self, line: str) -> None:
        s = self.session
        parts = line.split(maxsplit=1)
        cmd, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if cmd in ("build-from", "buildfrom"):
            path = os.path.expanduser(rest.strip())
            try:
                with open(path, encoding="utf-8") as f:
                    md = f.read()
            except OSError as exc:
                self.call_from_thread(self._shell, f"cannot read {path}: {exc}")
                return
            from .schema import validate_input
            inp = intake.parse_form(md)
            errs = validate_input(inp)
            if errs:
                self.call_from_thread(self._shell, "form invalid: " + "; ".join(errs))
                return
            objective = inp.get("objective", "build")
            s.hard = s.hard or bool(inp.get("hard"))
        else:
            objective = rest.strip()
            if not objective:
                self.call_from_thread(self._shell, "usage: build <objective>")
                return
            inp = default_input(objective, hard=s.hard)

        s.engine = s.engine_factory(s.hard, objective)
        s.engine.on_agent_start = lambda a: self.call_from_thread(self._agent_start, a)
        s.engine.on_activation = lambda a, t, f: self.call_from_thread(self._agent_done, a, t, f)
        s.engine.on_event = lambda a, k, d: self.call_from_thread(self._agent_event, a, k, d)
        s.cwd = ""
        self.call_from_thread(self._set_build_title, objective)
        self.call_from_thread(self._shell, f"[dim]building: {objective} …[/]")
        try:
            res = s.engine.build(inp)
            s.workspace = s.engine.workspace
            self.call_from_thread(
                self._shell,
                f"[green]✓[/] built in {res['steps']} step(s) → {res['run_id']}. try: ls · updates · report")
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._shell, f"[red]build failed:[/] {exc}")


def run_tui(session: ChatSession, label: str, log_path: str = "") -> None:
    PixibotApp(session, label, log_path).run()
