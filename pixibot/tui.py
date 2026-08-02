"""Split-pane TUI for Pixibot (M18) — a tmux-like layout in one window.

  ┌── shell (left) ──────┬── build status (top-right) ──┐
  │ command input + log  │  agents + states             │
  │                      ├── active agent (bottom-right)┤
  │                      │  each agent's actions/messages│
  └──────────────────────┴──────────────────────────────┘

Builds run in a background worker so the shell stays usable. Local commands
(ls/cd/cat/status/…) are handled inline by a ChatSession; model-touching commands
(build/revise/tell/ask/chat) run in worker threads and stream into the panes.
Requires ``textual`` (pip install textual).
"""

from __future__ import annotations

import os

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog, Static

from . import intake
from .cli import ChatSession
from .engine import default_input

_FAST = {"ls", "cd", "pwd", "cat", "tree", "status", "agents", "report",
         "form", "hard", "provider", "help", "?"}


class PixibotApp(App):
    CSS = """
    Screen { layout: horizontal; }
    #left  { width: 1fr; }
    #right { width: 1fr; }
    #shelllog { height: 1fr; border: round $accent; padding: 0 1; }
    #cmd { dock: bottom; border: round $accent; }
    #status { height: 45%; border: round green; padding: 0 1; }
    #agentlog { height: 1fr; border: round yellow; padding: 0 1; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("ctrl+d", "quit", "Quit")]

    def __init__(self, session: ChatSession, label: str):
        super().__init__()
        self.session = session
        self.label = label
        self.build_title = "(no build yet)"
        self.agent_order: list[str] = []
        self.agent_states: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="left"):
            yield RichLog(id="shelllog", wrap=True, markup=True, highlight=False)
            yield Input(placeholder="build <objective> · ls · cat <file> · ask <agent> <q> · help",
                        id="cmd")
        with Vertical(id="right"):
            yield Static(self._status_text(), id="status")
            yield RichLog(id="agentlog", wrap=True, markup=False)

    def on_mount(self) -> None:
        self._shell(f"[b magenta]Pixibot[/]  ·  provider: {self.label}")
        self._shell("Builds run in the background — watch the right panes. 'help' for commands.")
        self.query_one("#cmd", Input).focus()

    # ── pane helpers (main thread only) ─────────────────────────────────────
    def _shell(self, text: str) -> None:
        self.query_one("#shelllog", RichLog).write(text)

    def _agent(self, text: str) -> None:
        self.query_one("#agentlog", RichLog).write(text)

    def _status_text(self) -> str:
        lines = [f"[b]build:[/] {self.build_title}"]
        for a in self.agent_order:
            st = self.agent_states.get(a, "?")
            colour = {"working…": "yellow", "DONE": "green"}.get(st, "white")
            lines.append(f"  {a:16} [{colour}]{st}[/]")
        if not self.agent_order:
            lines.append("  (no agents yet)")
        return "\n".join(lines)

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())

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
        self._agent(f"  {aid} → {detail}" if kind == "tool" else f"  {aid}: {detail[:200]}")

    # ── input ───────────────────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        self._shell(f"[b]pixibot:/{self.session.cwd} ›[/] {line}")
        first = line.split(maxsplit=1)[0].lower()
        if first in ("exit", "quit"):
            self.exit()
            return
        if first == "clear":
            self.query_one("#shelllog", RichLog).clear()
            return
        if first in _FAST:
            out = self.session.handle(line)
            if out:
                self._shell(out)
            return
        self._run_async(line)   # build / revise / tell / ask / chat -> worker

    # ── background work ─────────────────────────────────────────────────────
    @work(thread=True)
    def _run_async(self, line: str) -> None:
        first = line.split(maxsplit=1)[0].lower()
        if first in ("build", "build-from", "buildfrom"):
            self._do_build(line)
        else:  # revise / tell / ask / plain chat — engine hooks persist from the build
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
        s.engine.workspace = s.workspace
        s.engine.on_agent_start = lambda a: self.call_from_thread(self._agent_start, a)
        s.engine.on_activation = lambda a, t, f: self.call_from_thread(self._agent_done, a, t, f)
        s.engine.on_event = lambda a, k, d: self.call_from_thread(self._agent_event, a, k, d)
        s.cwd = ""
        self.call_from_thread(self._set_build_title, objective)
        self.call_from_thread(self._shell, f"[dim]building: {objective} …[/]")
        try:
            res = s.engine.build(inp)
            self.call_from_thread(self._shell,
                                  f"[green]✓[/] built in {res['steps']} step(s). try: ls · report")
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._shell, f"[red]build failed:[/] {exc}")


def run_tui(session: ChatSession, label: str) -> None:
    PixibotApp(session, label).run()
