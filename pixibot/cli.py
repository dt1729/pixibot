"""Pixibot CLI — a friendly shell over your multi-agent build (DESIGN.md §12).

Navigate the project like bash (ls, cd, pwd, cat, tree). Run bare-word actions
(build, revise, tell, ask, status, provider, ...). Anything else you type is a
question to the project overseer. Live (with a provider key) it streams replies
and shows per-agent progress; offline it runs a canned multi-agent mock.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import config, console, intake, mockrun, observer
from .blackboard import Blackboard
from .engine import Engine
from .interaction import Broker
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
  provider [name]       switch model           hard [on|off]  hard-dev routing
  help                  this help              exit / quit    leave

Anything else you type is a question to the project overseer.
"""

# First words that are commands (everything else = a question to the overseer).
COMMANDS = {
    "ls", "cd", "pwd", "cat", "tree", "clear", "help", "?", "exit", "quit",
    "build", "build-from", "buildfrom", "revise", "tell", "ask",
    "status", "agents", "report", "form", "provider", "hard", "think", "updates",
}
HEAVY = {"build", "build-from", "buildfrom", "revise", "tell"}  # spin while these run


class ChatSession:
    """Command router / shell (testable without the input loop)."""

    def __init__(self, bb: Blackboard, broker: Broker, engine_factory, *,
                 workspace: Workspace = None, provider_builder=None, provider_name: str = "custom"):
        self.bb = bb
        self.broker = broker
        self.engine_factory = engine_factory
        self.workspace = workspace
        self.cwd = ""                # workspace-relative current dir
        self.hard = False
        self.engine = None
        self.progress = None        # agent finished
        self.progress_start = None  # agent started
        self.provider_builder = provider_builder
        self.provider_name = provider_name

    # ── bash-style navigation ───────────────────────────────────────────────
    def _resolve(self, arg: str):
        """Normalize an arg against cwd to a workspace-relative path (None if it escapes).

        No arg / '.' -> current dir; '~' or '/' -> workspace root.
        """
        if arg in ("~", "/"):
            return ""
        if not arg or arg == ".":
            return self.cwd
        joined = arg.lstrip("/") if arg.startswith("/") else (f"{self.cwd}/{arg}" if self.cwd else arg)
        norm = os.path.normpath(joined)
        if norm in (".", ""):
            return ""
        if norm.startswith(".."):
            return None
        return norm

    def _ls(self, arg):
        if not self.workspace:
            return "(no workspace yet — run a build first)"
        target = self._resolve(arg)
        if target is None:
            return "ls: path is outside the workspace"
        entries = self.workspace.listdir(target)
        if entries is None:
            return arg if self.workspace.read_file(target) is not None else f"ls: no such directory: {arg}"
        return "  ".join(entries) if entries else "(empty)"

    def _cd(self, arg):
        if not self.workspace:
            return "(no workspace yet — run a build first)"
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

    def _cat(self, arg):
        if not arg:
            return "usage: cat <file>"
        target = self._resolve(arg)
        if target is None or not self.workspace:
            return "cat: invalid path"
        content = self.workspace.read_file(target)
        return content if content is not None else f"cat: no such file: {arg}"

    def _tree(self):
        if not self.workspace:
            return "(no workspace yet)"
        files = self.workspace.list_files()
        return "\n".join(files) if files else "(empty)"

    # ── inspection ──────────────────────────────────────────────────────────
    def _agents(self):
        rows = self.bb.list_agents()
        if not rows:
            return "(no agents yet — run: build <objective>)"
        return "\n".join(f"{r['agent_id']:18} role={r.get('role')} depth={r.get('depth')} "
                         f"state={r.get('state')}" for r in rows)

    def _status(self):
        agents = self.bb.list_agents()
        files = self.workspace.list_files() if self.workspace else []
        head = f"provider: {self.provider_name}   agents: {len(agents)}   files: {len(files)}"
        if not agents and not files:
            return head + "\n(no build yet — run: build <objective>)"
        rows = [f"  {a['agent_id']:18} {str(a.get('role')):11} {a.get('state')}" for a in agents]
        return head + "\n" + "\n".join(rows)

    # ── build / steer ─────────────────────────────────────────────────────────
    def _start_engine(self, objective):
        self.engine = self.engine_factory(self.hard, objective)
        self.engine.on_activation = self.progress
        self.engine.on_agent_start = self.progress_start
        self.cwd = ""  # fresh build wipes the workspace; go back to root
        if self.workspace is not None:
            self.engine.workspace = self.workspace
        if self.progress_start:
            console.write(console.c("  … planning the team (TPM)\n", "dim"))

    def _built_msg(self, res):
        return (f"Built in {res['steps']} step(s). Agents: "
                + (", ".join(a['agent_id'] for a in self.bb.list_agents()) or "(none)")
                + ".  Try:  ls  ·  status  ·  report")

    def _build(self, objective):
        if not objective.strip():
            return "usage: build <what to build>"
        self._start_engine(objective)
        res = self.engine.build_objective(objective, hard=self.hard)
        self.workspace = self.engine.workspace   # adopt this build's isolated workspace
        return self._built_msg(res)

    def _build_from(self, path):
        if not path:
            return "usage: build-from <file.md>"
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                md = f.read()
        except OSError as e:
            return f"could not read {path}: {e}"
        from .schema import validate_input
        inp = intake.parse_form(md)
        errs = validate_input(inp)
        if errs:
            return "the form is missing/invalid:\n- " + "\n- ".join(errs)
        self.hard = self.hard or bool(inp.get("hard"))
        self._start_engine(inp.get("objective", "build"))
        res = self.engine.build(inp)
        self.workspace = self.engine.workspace   # adopt this build's isolated workspace
        return self._built_msg(res)

    def _revise(self, feedback):
        if not self.engine:
            return "nothing to revise yet — run a build first"
        if not feedback.strip():
            return "usage: revise <feedback>"
        steps = self.engine.revise(feedback)
        return f"Revised in {steps} step(s). Agents: " + (
            ", ".join(a['agent_id'] for a in self.bb.list_agents()) or "(none)")

    def _tell(self, rest):
        agent, _, msg = rest.partition(" ")
        if not agent or not msg.strip():
            return "usage: tell <agent> <message>"
        if self.engine:
            steps = self.engine.tell(agent, msg.strip())
            return f"directive sent to {agent}; resumed {steps} step(s)"
        return f"directive #{self.broker.tell(agent, msg.strip())} sent to {agent}"

    def _hard(self, arg):
        arg = arg.strip().lower()
        self.hard = (arg in ("on", "true", "yes")) if arg else (not self.hard)
        return f"hard development: {'on' if self.hard else 'off'}"

    def _provider(self, arg):
        arg = arg.strip().lower()
        if not arg:
            return f"provider: {self.provider_name}.  switch with: provider <{' | '.join(PROVIDERS)}>"
        if arg not in PROVIDERS:
            return f"unknown provider '{arg}'. options: {', '.join(PROVIDERS)}"
        if self.provider_builder is None:
            return "(provider switching not available here)"
        label, use_real, broker, factory = self.provider_builder(arg)
        self.broker, self.engine_factory = broker, factory
        self.provider_name, self.engine = arg, None
        self.progress = _progress if use_real else None
        return f"switched to {label} — run a fresh build"

    # ── dispatch ──────────────────────────────────────────────────────────────
    def handle(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if cmd in ("exit", "quit"):
            return "__quit__"
        if cmd in ("help", "?"):
            return HELP
        if cmd == "clear":
            return "__clear__"
        if cmd == "ls":
            return self._ls(rest.strip())
        if cmd == "cd":
            return self._cd(rest.strip())
        if cmd == "pwd":
            return "/" + self.cwd
        if cmd == "cat":
            return self._cat(rest.strip())
        if cmd == "tree":
            return self._tree()
        if cmd == "agents":
            return self._agents()
        if cmd == "status":
            return self._status()
        if cmd == "report":
            return observer.report(self.bb)
        if cmd == "form":
            return intake.render_form()
        if cmd == "hard":
            return self._hard(rest)
        if cmd == "provider":
            return self._provider(rest)
        if cmd == "build":
            return self._build(rest)
        if cmd in ("build-from", "buildfrom"):
            return self._build_from(rest.strip())
        if cmd == "revise":
            return self._revise(rest)
        if cmd == "tell":
            return self._tell(rest)
        if cmd == "updates":
            return self._updates()
        if cmd == "think":
            return self._think(rest.strip())
        if cmd == "ask":
            agent, _, q = rest.partition(" ")
            if not agent or not q.strip():
                return "usage: ask <agent> <question>"
            return self.broker.talk(agent, q.strip())
        # anything else -> talk to the project overseer
        return self.broker.talk("tpm", line)

    def _updates(self) -> str:
        """Raw latest report from each agent + recent messages — no LLM in the loop."""
        from .interaction import latest_reports
        reports = latest_reports(self.bb)
        agents = self.bb.list_agents()
        if not agents:
            return "no build has run yet — try: build <objective>"
        out = ["=== agent updates ==="]
        for a in agents:
            aid, st = a["agent_id"], a.get("state")
            rep = reports.get(aid)
            out.append(f"\n[{aid}] ({st})")
            out.append(f"  {rep.strip()}" if rep else "  (no report yet — still working or never ran)")
        return "\n".join(out)

    def _think(self, agent: str) -> str:
        """Show an agent's most recent recorded reasoning (works cross-instance,
        reading straight from the shared blackboard)."""
        if not agent:
            return "usage: think <agent>   (e.g. think architect)"
        from .blackboard import KIND_CONTROL
        thoughts = [e for e in self.bb.history(from_agent=agent, kind=KIND_CONTROL)
                    if e.payload.startswith("[thinking]")]
        if not thoughts:
            return (f"no recorded thinking for '{agent}' yet — it may not have run, or is "
                    "running on a model without exposed thinking.")
        return f"🧠 {agent} — latest reasoning:\n{thoughts[-1].payload[len('[thinking]'):].strip()}"


# ── provider wiring ──────────────────────────────────────────────────────────
def _anthropic_spokesbot():
    from .model import AnthropicModel
    return AnthropicModel(config.SPOKESBOT_MODEL, effort=None, use_thinking=False, max_tokens=1024)


def _gemini_spokesbot():
    from .model import OpenAICompatModel
    return OpenAICompatModel(config.GEMINI_SPOKESBOT_MODEL, base_url=config.GEMINI_BASE_URL,
                             api_key_env="GEMINI_API_KEY", max_tokens=1024)


def _openrouter_spokesbot():
    from .model import OpenAICompatModel
    return OpenAICompatModel(config.OPENROUTER_SPOKESBOT_MODEL, base_url=config.OPENROUTER_BASE_URL,
                             api_key_env="OPENROUTER_API_KEY", max_tokens=1024)


def _live_engine_factory(bb):
    from .run import anthropic_tpm_model, make_anthropic_model_factory

    def make(hard, objective):
        return Engine(bb, anthropic_tpm_model(), make_anthropic_model_factory(hard))
    return make


def _gemini_engine_factory(bb):
    from .run import gemini_tpm_model, make_gemini_model_factory

    def make(hard, objective):
        return Engine(bb, gemini_tpm_model(), make_gemini_model_factory(hard))
    return make


def _openrouter_engine_factory(bb):
    from .run import make_openrouter_model_factory, openrouter_tpm_model

    def make(hard, objective):
        return Engine(bb, openrouter_tpm_model(), make_openrouter_model_factory(hard))
    return make


def _offline_engine_factory(bb):
    def make(hard, objective):
        return Engine(bb, mockrun.mock_tpm_model(objective), mockrun.mock_model_factory())
    return make


PROVIDERS = ("anthropic", "gemini", "openrouter", "offline")


def _make_provider(bb, name):
    if name == "gemini":
        return ("gemini (Google AI Studio)", True, Broker(bb, lambda: _gemini_spokesbot()),
                _gemini_engine_factory(bb))
    if name == "openrouter":
        return ("openrouter", True, Broker(bb, lambda: _openrouter_spokesbot()),
                _openrouter_engine_factory(bb))
    if name == "anthropic":
        return ("anthropic (Claude)", True, Broker(bb, lambda: _anthropic_spokesbot()),
                _live_engine_factory(bb))
    return ("offline (mock)", False, Broker(bb, lambda: None), _offline_engine_factory(bb))


def _auto_provider_name():
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "offline"


def _progress_start(agent_id):
    console.write(f"  {console.c('▸', 'yellow')} {console.label(agent_id)} "
                  f"{console.c('working…', 'dim')}\n")


def _progress(agent_id, terminal, wrote):
    tag = console.label(agent_id)
    if wrote:
        shown = ", ".join(wrote[:6]) + (f" (+{len(wrote) - 6} more)" if len(wrote) > 6 else "")
        console.write(f"  {console.c('◉', 'green')} {tag} → {console.c(shown, 'cyan')}\n")
    else:
        console.write(f"  {console.c('•', 'gray')} {tag} {console.c(terminal.lower(), 'dim')}\n")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="pixibot", description="Pixibot shell")
    ap.add_argument("--db", default="pixibot.db")
    ap.add_argument("--provider", choices=("auto",) + PROVIDERS, default="auto")
    ap.add_argument("--objective", help="one-shot: build this, then drop into the shell")
    ap.add_argument("--tui", action="store_true", help="force the split-pane TUI")
    ap.add_argument("--plain", action="store_true", help="force the plain shell (no TUI)")
    args = ap.parse_args(argv)

    # Arrow-key history + line editing (up/down recalls commands, left/right edits).
    try:
        import atexit
        import readline
        histfile = os.path.expanduser("~/.pixibot_history")
        try:
            readline.read_history_file(histfile)
        except OSError:
            pass
        readline.set_history_length(1000)
        atexit.register(readline.write_history_file, histfile)
    except ImportError:
        pass

    bb = Blackboard(args.db, run_id="cli")
    workspace = Workspace(os.path.join(os.path.expanduser("~/pixibot-workspace"), "cli"))
    name = args.provider if args.provider != "auto" else _auto_provider_name()
    label, use_real, broker, engine_factory = _make_provider(bb, name)
    session = ChatSession(bb, broker, engine_factory, workspace=workspace,
                          provider_builder=lambda n: _make_provider(bb, n), provider_name=name)

    # Default to the split-pane TUI when possible; fall back to the plain shell.
    if args.tui or (not args.plain and sys.stdout.isatty()):
        try:
            from .tui import run_tui
        except ImportError:
            run_tui = None
        if run_tui is not None:
            run_tui(session, label)
            bb.close()
            return
        print("(install `textual` for the split-pane view: pip install textual — using plain shell)")

    if use_real:
        session.progress = _progress
        session.progress_start = _progress_start

    print(console.banner())
    print(console.c(f"provider: {label}   ·   'help' for commands  ·  ↑/↓ history  ·  'exit' to leave", "dim"))
    print(console.c(f"workspace: {workspace.root}", "dim"))

    def run_line(line: str) -> bool:
        s = line.strip()
        if not s:
            return True
        first = s.split(maxsplit=1)[0].lower()
        # stream chat/ask replies live in real mode
        if use_real and first == "ask":
            body = s[3:].strip().split(maxsplit=1)
            if len(body) == 2:
                agent, q = body
                console.write(console.label(agent) + console.c(" ▸ ", "dim"))
                session.broker.talk(agent, q, on_delta=console.write)
                console.write("\n")
                return True
        if use_real and first not in COMMANDS:
            console.write(console.c("overseer ▸ ", "dim"))
            session.broker.talk("tpm", s, on_delta=console.write)
            console.write("\n")
            return True
        # No blind spinner for builds — live per-agent progress prints instead.
        out = session.handle(s)
        if out == "__quit__":
            return False
        if out == "__clear__":
            console.write("\033[2J\033[H")
            return True
        if out:
            print(out)
        return True

    if args.objective:
        run_line(f"build {args.objective}")

    while True:
        try:
            prompt = console.c(f"\npixibot:/{session.cwd}", "bold", "magenta") + console.c(" › ", "dim")
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            break
        if not run_line(line):
            break
    bb.close()


if __name__ == "__main__":  # pragma: no cover
    main()
