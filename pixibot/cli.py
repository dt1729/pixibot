"""Pixibot CLI — a friendly shell over your multi-agent build (DESIGN.md §12).

Navigate the project like bash (ls, cd, pwd, cat, tree). Run bare-word actions
(build, revise, tell, ask, status, provider, ...). Anything else you type is a
question to the project overseer. Live (with a provider key) it streams replies
and shows per-agent progress; offline it runs a canned multi-agent mock.

:class:`ChatSession` is the router and holds no terminal: it takes a line and
returns text, which is why both the plain shell and the TUI drive the same object
and why its behaviour is testable without an input loop.

Contract: docs/contracts/pixibot.cli.md
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Protocol

from . import console, intake, logbook, observer, providers
from .blackboard import KIND_CONTROL, Blackboard
from .checkpoints import Checkpoint, Review
from .engine import Engine
from .interaction import Broker, latest_reports
from .providers import Provider
from .schema import validate_input
from .workspace import Workspace

HELP = """Navigate the project (bash-style):
  ls [dir]              list files             cd <dir>       change directory
  pwd                   current directory      cat <file>     print a file
  tree                  full file tree         clear          clear the screen

Build & steer:
  build <objective>          plan + build it
  build-from <file.md>       build from a filled intake form  (see: form)
  revise <feedback>          re-plan from feedback
  tell <agent> <message>     steer an agent (non-blocking)
  ask <agent> <question>     ask an agent's spokesperson (never pauses it)

Inspect & configure:
  updates               latest report from every agent
  think <agent>         show an agent's latest reasoning
  status                project status         agents         list the agents
  report                full run report        form           show the intake form
  gate                  run the mechanical gate (tests/build)
  provider [name]       switch model           hard [on|off]  hard-dev routing
  help                  this help              exit / quit    leave

Anything else you type is a question to the project overseer.
"""

class EngineFactory(Protocol):
    """Builds a fresh Engine for one build (the provider is already chosen)."""

    def __call__(self, objective: str, *, hard: bool = False) -> Engine: ...

# Sentinels the shell and the TUI act on rather than print.
QUIT = "__quit__"
CLEAR = "__clear__"

HEAVY = {"build", "build-from", "buildfrom", "revise", "tell"}  # spin while these run

_NO_WORKSPACE = "(no workspace yet — run a build first)"


class ChatSession:
    """Command router / shell (testable without the input loop)."""

    #: command -> method. The single source of what the shell understands: the
    #: tab-completer, the "is this a command or a question?" test and the
    #: dispatcher all read this, so a new command is one entry and one method.
    DISPATCH: ClassVar[dict[str, str]] = {
        "ls": "_ls", "cd": "_cd", "pwd": "_pwd", "cat": "_cat", "tree": "_tree",
        "clear": "_clear", "help": "_help", "exit": "_exit", "quit": "_exit",
        "build": "_build", "build-from": "_build_from", "revise": "_revise",
        "tell": "_tell", "ask": "_ask", "gate": "_gate",
        "status": "_status", "agents": "_agents", "report": "_report",
        "form": "_form", "provider": "_provider", "hard": "_hard",
        "think": "_think", "updates": "_updates",
    }
    ALIASES: ClassVar[dict[str, str]] = {"?": "help", "buildfrom": "build-from"}

    def __init__(self, bb: Blackboard, broker: Broker, engine_factory: EngineFactory, *,
                 workspace: Workspace | None = None, provider_name: str = "custom"):
        self.bb = bb
        self.broker = broker
        self.engine_factory = engine_factory
        self.workspace = workspace
        self.cwd = ""                # workspace-relative current dir
        self.hard = False
        self.engine: Engine | None = None
        self.progress: Callable[[str, str, list[str | None]], None] | None = None
        self.progress_start: Callable[[str], None] | None = None
        self.on_event: Callable[[str, str, str], None] | None = None
        self.on_build_start: Callable[[str], None] | None = None
        self.reviewer: Callable[[Checkpoint, str], Review] | None = None
        self.provider_name = provider_name
        self.sandbox = "auto"        # passed to each engine this session builds

    # ── dispatch ────────────────────────────────────────────────────────────
    def handle(self, line: str) -> str:
        """Run one line of input and return what to show. Never raises for bad input."""
        line = line.strip()
        if not line:
            return ""
        word, _, rest = line.partition(" ")
        cmd = self.ALIASES.get(word.lower(), word.lower())
        method = self.DISPATCH.get(cmd)
        if method is None:
            return self.broker.talk("tpm", line)  # anything else -> the overseer
        handler: Callable[[str], str] = getattr(self, method)
        return handler(rest)

    # ── bash-style navigation ───────────────────────────────────────────────
    def _resolve(self, arg: str) -> str | None:
        """Normalize an arg against cwd to a workspace-relative path (None if it escapes).

        No arg / '.' -> current dir; '~' or '/' -> workspace root.
        """
        if arg in ("~", "/"):
            return ""
        if not arg or arg == ".":
            return self.cwd
        joined = arg.lstrip("/") if arg.startswith("/") else (
            f"{self.cwd}/{arg}" if self.cwd else arg)
        norm = _normalize(joined)
        if norm in (".", ""):
            return ""
        if norm.startswith(".."):
            return None
        return norm

    def _ls(self, rest: str) -> str:
        arg = rest.strip()
        if not self.workspace:
            return _NO_WORKSPACE
        target = self._resolve(arg)
        if target is None:
            return "ls: path is outside the workspace"
        entries = self.workspace.listdir(target)
        if entries is None:
            if self.workspace.read_file(target) is not None:
                return arg
            return f"ls: no such directory: {arg}"
        return "  ".join(entries) if entries else "(empty)"

    def _cd(self, rest: str) -> str:
        arg = rest.strip()
        if not self.workspace:
            return _NO_WORKSPACE
        if not arg or arg in ("~", "/"):     # `cd` with no arg -> workspace root
            self.cwd = ""
            return "/"
        target = self._resolve(arg)
        if target is None:
            return "cd: cannot go above the workspace root"
        if target and not self.workspace.isdir(target):
            return f"cd: no such directory: {arg}"
        self.cwd = target
        return "/" + self.cwd

    def _pwd(self, rest: str) -> str:  # noqa: ARG002 - uniform handler signature
        return "/" + self.cwd

    def _cat(self, rest: str) -> str:
        arg = rest.strip()
        if not arg:
            return "usage: cat <file>"
        target = self._resolve(arg)
        if target is None or not self.workspace:
            return "cat: invalid path"
        content = self.workspace.read_file(target)
        return content if content is not None else f"cat: no such file: {arg}"

    def _tree(self, rest: str) -> str:  # noqa: ARG002
        if not self.workspace:
            return "(no workspace yet)"
        files = self.workspace.list_files()
        return "\n".join(files) if files else "(empty)"

    # ── inspection ──────────────────────────────────────────────────────────
    def _agents(self, rest: str) -> str:  # noqa: ARG002
        rows = self.bb.list_agents()
        if not rows:
            return "(no agents yet — run: build <objective>)"
        return "\n".join(f"{r['agent_id']:18} role={r.get('role')} depth={r.get('depth')} "
                         f"state={r.get('state')}" for r in rows)

    def _status(self, rest: str) -> str:  # noqa: ARG002
        agents = self.bb.list_agents()
        files = self.workspace.list_files() if self.workspace else []
        head = f"provider: {self.provider_name}   agents: {len(agents)}   files: {len(files)}"
        if not agents and not files:
            return head + "\n(no build yet — run: build <objective>)"
        rows = [f"  {a['agent_id']:18} {a.get('role')!s:11} {a.get('state')}" for a in agents]
        return head + "\n" + "\n".join(rows)

    def _report(self, rest: str) -> str:  # noqa: ARG002
        return observer.report(self.bb)

    def _form(self, rest: str) -> str:  # noqa: ARG002
        return intake.render_form()

    def _help(self, rest: str) -> str:  # noqa: ARG002
        return HELP

    def _exit(self, rest: str) -> str:  # noqa: ARG002
        return QUIT

    def _clear(self, rest: str) -> str:  # noqa: ARG002
        return CLEAR

    def _updates(self, rest: str) -> str:  # noqa: ARG002
        """Raw latest report from each agent — no LLM in the loop."""
        reports = latest_reports(self.bb)
        agents = self.bb.list_agents()
        if not agents:
            return "no build has run yet — try: build <objective>"
        out = ["=== agent updates ==="]
        for a in agents:
            aid, state = a["agent_id"], a.get("state")
            rep = reports.get(aid)
            out.append(f"\n[{aid}] ({state})")
            out.append(f"  {rep.strip()}" if rep
                       else "  (no report yet — still working or never ran)")
        return "\n".join(out)

    def _think(self, rest: str) -> str:
        """An agent's most recent recorded reasoning (works cross-instance, read
        straight from the shared blackboard)."""
        agent = rest.strip()
        if not agent:
            return "usage: think <agent>   (e.g. think architect)"
        thoughts = [e for e in self.bb.history(from_agent=agent, kind=KIND_CONTROL)
                    if e.payload.startswith("[thinking]")]
        if not thoughts:
            return (f"no recorded thinking for '{agent}' yet — it may not have run, or is "
                    "running on a model without exposed thinking.")
        latest = thoughts[-1].payload[len("[thinking]"):].strip()
        return f"🧠 {agent} — latest reasoning:\n{latest}"

    # ── build / steer ───────────────────────────────────────────────────────
    def _start_engine(self, objective: str) -> Engine:
        engine = self.engine_factory(objective, hard=self.hard)
        engine.sandbox = self.sandbox
        engine.on_activation = self.progress
        engine.on_agent_start = self.progress_start
        engine.on_event = self.on_event
        self.cwd = ""  # a fresh build wipes the workspace; go back to root
        if self.workspace is not None:
            engine.workspace = self.workspace
        if self.on_build_start:
            self.on_build_start(objective)
        self.engine = engine
        return engine

    def _built_msg(self, res: dict[str, object]) -> str:
        return (f"Built in {res['steps']} step(s). Agents: "
                + (", ".join(a["agent_id"] for a in self.bb.list_agents()) or "(none)")
                + ".  Try:  ls  ·  status  ·  report")

    def _build(self, rest: str) -> str:
        if not rest.strip():
            return "usage: build <what to build>"
        engine = self._start_engine(rest)
        res = engine.build_objective(rest, hard=self.hard, reviewer=self.reviewer)
        self.workspace = engine.workspace   # adopt this build's isolated workspace
        return self._built_msg(res)

    def _build_from(self, rest: str) -> str:
        path = rest.strip()
        if not path:
            return "usage: build-from <file.md>"
        try:
            md = Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            return f"could not read {path}: {exc}"
        inp = intake.parse_form(md)
        errs = validate_input(inp)
        if errs:
            return "the form is missing/invalid:\n- " + "\n- ".join(errs)
        self.hard = self.hard or bool(inp.get("hard"))
        engine = self._start_engine(inp.get("objective", "build"))
        res = engine.build(inp, reviewer=self.reviewer)
        self.workspace = engine.workspace
        return self._built_msg(res)

    def _revise(self, rest: str) -> str:
        if not self.engine:
            return "nothing to revise yet — run a build first"
        if not rest.strip():
            return "usage: revise <feedback>"
        steps = self.engine.revise(rest)
        return f"Revised in {steps} step(s). Agents: " + (
            ", ".join(a["agent_id"] for a in self.bb.list_agents()) or "(none)")

    def _tell(self, rest: str) -> str:
        agent, _, msg = rest.partition(" ")
        if not agent or not msg.strip():
            return "usage: tell <agent> <message>"
        if self.engine:
            steps = self.engine.tell(agent, msg.strip())
            return f"directive sent to {agent}; resumed {steps} step(s)"
        return f"directive #{self.broker.tell(agent, msg.strip())} sent to {agent}"

    def _ask(self, rest: str) -> str:
        agent, _, question = rest.partition(" ")
        if not agent or not question.strip():
            return "usage: ask <agent> <question>"
        return self.broker.talk(agent, question.strip())

    def _gate(self, rest: str) -> str:  # noqa: ARG002
        """Run the mechanical gate over the current workspace and report (§10)."""
        if not self.engine:
            return "nothing to gate yet — run a build first"
        report = self.engine.gate()
        if not report.applicable:
            return ("nothing mechanical to check: no test suite and no build file in the "
                    "workspace. The gate covers Python (pytest) and C/C++ (cmake/make).")
        head = f"gate [{report.language}]: {'PASSED' if report.passed else 'FAILED'}"
        rows = [f"  {'ok ' if r.passed else 'RED'} {r.name}" for r in report.results]
        body = "" if report.passed else "\n\n" + report.summary()
        return head + "\n" + "\n".join(rows) + body

    # ── configuration ───────────────────────────────────────────────────────
    def _hard(self, rest: str) -> str:
        arg = rest.strip().lower()
        self.hard = (arg in ("on", "true", "yes")) if arg else (not self.hard)
        return f"hard development: {'on' if self.hard else 'off'}"

    def _provider(self, rest: str) -> str:
        arg = rest.strip().lower()
        if not arg:
            return (f"provider: {self.provider_name}.  "
                    f"switch with: provider <{' | '.join(providers.NAMES)}>")
        if arg not in providers.NAMES:
            return f"unknown provider '{arg}'. options: {', '.join(providers.NAMES)}"
        provider = providers.get(arg)
        self.broker, self.engine_factory = wire(self.bb, provider)
        self.provider_name, self.engine = provider.name, None
        self.progress = _progress if provider.live else None
        return f"switched to {provider.label} — run a fresh build"


# First words that are commands (everything else = a question to the overseer).
COMMANDS = frozenset(ChatSession.DISPATCH) | frozenset(ChatSession.ALIASES)


def _normalize(path: str) -> str:
    """Collapse '.' and '..' textually, keeping a leading '..' visible to the caller."""
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append("..")
        else:
            parts.append(seg)
    return "/".join(parts)


# ── provider wiring ──────────────────────────────────────────────────────────
# Everything provider-specific lives in pixibot.providers; this is the only place
# that turns a provider into the two objects a session holds.
def wire(bb: Blackboard, provider: Provider) -> tuple[Broker, EngineFactory]:
    """Build the broker and engine-maker for `provider` over blackboard `bb`."""

    def make_engine(objective: str, *, hard: bool = False) -> Engine:
        return Engine(bb, provider.tpm_model(objective),
                      provider.model_factory(hard=hard))

    return Broker(bb, provider.spokesbot), make_engine


ACCEPTS = ("", "ok", "y", "yes", "accept", "accepted", "ship", "done", "lgtm")


def ask_at_checkpoint(checkpoint: Checkpoint, gate_summary: str) -> Review:
    """Show the demo and read the verdict — the human half of the gate (§10).

    Blocking, deliberately: this is the one moment a build is meant to wait. An
    empty line accepts; anything else is feedback and the team re-plans with it.
    """
    console.write(console.c(f"{chr(10)}── checkpoint {checkpoint.id} "
                            f"────────────────────────{chr(10)}", "bold", "magenta"))
    console.write(f"demo: {checkpoint.demo}{chr(10)}")
    console.write(console.c(f"gate: {gate_summary}{chr(10)}", "dim"))
    try:
        answer = input(console.c("accept? [Enter to accept, or type what to change] ",
                                 "bold")).strip()
    except (EOFError, KeyboardInterrupt):
        console.write(chr(10))
        return Review.accept()   # a closed stdin is not a request for changes
    if answer.lower() in ACCEPTS:
        return Review.accept()
    return Review.revise(answer)


def _planning_line(objective: str) -> None:  # noqa: ARG001 - the title is the TUI's job
    console.write(console.c("  … planning the team (TPM)" + chr(10), "dim"))


def _progress_start(agent_id: str) -> None:
    console.write(f"  {console.c('▸', 'yellow')} {console.label(agent_id)} "
                  f"{console.c('working…', 'dim')}\n")


def _progress(agent_id: str, terminal: str, wrote: list[str | None]) -> None:
    tag = console.label(agent_id)
    if wrote:
        shown = ", ".join(str(w) for w in wrote[:6])
        if len(wrote) > 6:
            shown += f" (+{len(wrote) - 6} more)"
        console.write(f"  {console.c('◉', 'green')} {tag} → {console.c(shown, 'cyan')}\n")
    else:
        console.write(f"  {console.c('•', 'gray')} {tag} {console.c(terminal.lower(), 'dim')}\n")


# ── entry point ──────────────────────────────────────────────────────────────
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="pixibot", description="Pixibot shell")
    ap.add_argument("--db", default="pixibot.db")
    ap.add_argument("--provider", choices=("auto", *providers.NAMES), default="auto")
    ap.add_argument("--objective", help="one-shot: build this, then drop into the shell")
    ap.add_argument("--sandbox", choices=("auto", "docker", "local"), default="auto",
                    help="where agent commands run (default: container when available)")
    ap.add_argument("--tui", action="store_true", help="force the split-pane TUI")
    ap.add_argument("--plain", action="store_true", help="force the plain shell (no TUI)")
    ap.add_argument("--log", help="path to the run log (default ~/.pixibot/pixibot.log)")
    ap.add_argument("--review", action="store_true",
                    help="pause at each checkpoint for a demo and your verdict (§10)")
    return ap.parse_args(argv)


def _setup_history() -> None:
    """Arrow-key history + line editing, when the platform has readline."""
    try:
        import atexit
        import readline
    except ImportError:
        return
    histfile = str(Path("~/.pixibot_history").expanduser())
    with contextlib.suppress(OSError):   # first run, or unreadable: start empty
        readline.read_history_file(histfile)
    readline.set_history_length(1000)
    atexit.register(readline.write_history_file, histfile)


def _build_session(args: argparse.Namespace, bb: Blackboard) -> tuple[ChatSession, Provider]:
    workspace = Workspace(str(Path("~/pixibot-workspace").expanduser() / "cli"))
    provider = providers.get(args.provider if args.provider != "auto" else providers.detect())
    broker, engine_factory = wire(bb, provider)
    session = ChatSession(bb, broker, engine_factory, workspace=workspace,
                          provider_name=provider.name)
    session.sandbox = args.sandbox
    if provider.live:
        session.progress = _progress
        session.progress_start = _progress_start
        session.on_build_start = _planning_line
    if args.review and sys.stdin.isatty():
        # The checkpoint pause needs a person at a terminal; piped input would
        # answer every demo with EOF.
        session.reviewer = ask_at_checkpoint
    return session, provider


def _stream_reply(session: ChatSession, line: str) -> bool:
    """Stream a chat reply token-by-token in live mode. True when handled here."""
    first, _, rest = line.partition(" ")
    if first.lower() == "ask" and len(rest.split(maxsplit=1)) == 2:
        agent, question = rest.split(maxsplit=1)
        console.write(console.label(agent) + console.c(" ▸ ", "dim"))
        session.broker.talk(agent, question, on_delta=console.write)
        console.write("\n")
        return True
    if first.lower() not in COMMANDS:
        console.write(console.c("overseer ▸ ", "dim"))
        session.broker.talk("tpm", line, on_delta=console.write)
        console.write("\n")
        return True
    return False


def _show(out: str) -> bool:
    """Act on what the session returned. False means the shell should stop."""
    if out == QUIT:
        return False
    if out == CLEAR:
        console.write("\033[2J\033[H")
    elif out:
        print(out)
    return True


def _repl(session: ChatSession, *, live: bool, objective: str | None = None) -> None:
    def run_line(line: str) -> bool:
        text = line.strip()
        if not text:
            return True
        if live and _stream_reply(session, text):
            return True
        # No blind spinner for builds — live per-agent progress prints instead.
        return _show(session.handle(text))

    if objective:
        run_line(f"build {objective}")
    while True:
        try:
            prompt = (console.c(f"\npixibot:/{session.cwd}", "bold", "magenta")
                      + console.c(" \u203a ", "dim"))   # typography, not an operator
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            break
        if not run_line(line):
            break


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    log_path = logbook.setup(args.log)
    _setup_history()

    bb = Blackboard(args.db, run_id="cli")
    session, provider = _build_session(args, bb)

    # Default to the split-pane TUI when possible; fall back to the plain shell.
    if args.tui or (not args.plain and sys.stdout.isatty()):
        try:
            from .tui import run_tui
        except ImportError:
            print("(install `textual` for the split-pane view: pip install textual"
                  " — using plain shell)")
        else:
            run_tui(session, provider.label, log_path)
            bb.close()
            return

    print(console.banner())
    print(console.c(f"provider: {provider.label}   ·   'help' for commands  ·  "
                    "↑/↓ history  ·  'exit' to leave", "dim"))
    workspace_root = session.workspace.root if session.workspace else "(none)"
    print(console.c(f"workspace: {workspace_root}", "dim"))
    print(console.c(f"run log: {log_path}", "dim"))
    _repl(session, live=provider.live, objective=args.objective)
    bb.close()


if __name__ == "__main__":  # pragma: no cover
    main()
