"""Pixibot CLI — a Claude-Code-like chatbot over a persistent run (DESIGN.md §12).

Chat with the TPM by default; ``@<id>`` talks to any agent via a cheap read-only
spokesbot that never pauses it; ``/tell`` steers (non-blocking); ``/build`` plans
and runs; ``/revise`` re-plans; ``/form`` shows the intake form; ``/hard`` toggles
hard-development routing. Live (with ``ANTHROPIC_API_KEY``) it streams replies and
shows per-agent progress; offline it runs a canned multi-agent mock.
"""

from __future__ import annotations

import argparse
import os

from . import config, console, intake, mockrun, observer
from .blackboard import Blackboard
from .engine import Engine
from .interaction import Broker

HELP = """Commands:
  <text>                 talk to the current agent (default: tpm)
  @<agent> <text>        talk to a specific agent (no text: switch to it)
  /at <agent>            switch the default agent
  /build <objective>     plan + run a build
  /revise <feedback>     re-plan from demo feedback
  /tell <agent> <text>   non-blocking steering directive (agent resumes)
  /hard [on|off]         toggle hard-development routing (principal -> Fable 5)
  /form                  show the build-request intake form
  /agents                list agents on the blackboard
  /report                print the Observer run report
  /help                  show this help
  /quit                  exit"""


class ChatSession:
    """Command router (testable without the input loop)."""

    def __init__(self, bb: Blackboard, broker: Broker, engine_factory, *, target: str = "tpm"):
        self.bb = bb
        self.broker = broker
        self.engine_factory = engine_factory  # (hard: bool, objective: str) -> Engine
        self.target = target
        self.hard = False
        self.engine = None
        self.progress = None  # live-progress hook forwarded to the engine on /build

    def _agents_line(self) -> str:
        return ", ".join(a["agent_id"] for a in self.bb.list_agents()) or "(none)"

    def parse_talk(self, line: str):
        """Return (agent, message) if the line is a talk, else None (a command/switch)."""
        line = line.strip()
        if not line:
            return None
        if line.startswith("@"):
            agent, _, text = line[1:].partition(" ")
            return (agent, text) if text else None
        if line.startswith("/"):
            return None
        return (self.target, line)

    def handle(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        if line in ("/quit", "/exit"):
            return "__quit__"
        if line == "/help":
            return HELP
        if line == "/form":
            return intake.render_form()
        if line.startswith("/hard"):
            arg = line[5:].strip().lower()
            self.hard = (arg in ("on", "true", "yes")) if arg else (not self.hard)
            return f"(hard development: {'on' if self.hard else 'off'})"
        if line == "/agents":
            rows = self.bb.list_agents()
            if not rows:
                return "(no agents yet — run /build <objective>)"
            return "\n".join(
                f"{r['agent_id']}  role={r['role']} depth={r['depth']} state={r['state']}"
                for r in rows
            )
        if line == "/report":
            return observer.report(self.bb)
        if line.startswith("/at "):
            self.target = line[4:].strip()
            return f"(now talking to {self.target})"
        if line.startswith("/build "):
            objective = line[7:].strip()
            self.engine = self.engine_factory(self.hard, objective)
            self.engine.on_activation = self.progress
            res = self.engine.build_objective(objective, hard=self.hard)
            return (f"Built in {res['steps']} step(s). Agents: {self._agents_line()}. "
                    f"Try '/report' or '@<agent> <question>'.")
        if line.startswith("/revise "):
            if not self.engine:
                return "(nothing to revise — /build first)"
            steps = self.engine.revise(line[8:].strip())
            return f"Revised in {steps} step(s). Agents: {self._agents_line()}."
        if line.startswith("/tell "):
            agent, _, text = line[6:].strip().partition(" ")
            if not text:
                return "usage: /tell <agent> <directive>"
            if self.engine:
                steps = self.engine.tell(agent, text)
                return f"(directive sent to {agent}; resumed {steps} step(s))"
            eid = self.broker.tell(agent, text)
            return f"(directive #{eid} sent to {agent})"
        if line.startswith("@"):
            agent, _, text = line[1:].partition(" ")
            if not text:
                self.target = agent
                return f"(now talking to {agent})"
            return self.broker.talk(agent, text)
        return self.broker.talk(self.target, line)


# ── live wiring ──────────────────────────────────────────────────────────────
def _anthropic_spokesbot():
    from .model import AnthropicModel
    return AnthropicModel(config.SPOKESBOT_MODEL, effort=None, use_thinking=False, max_tokens=1024)


def _live_engine_factory(bb: Blackboard):
    from .run import anthropic_tpm_model, make_anthropic_model_factory

    def make(hard: bool, objective: str) -> Engine:
        return Engine(bb, anthropic_tpm_model(), make_anthropic_model_factory(hard))
    return make


def _offline_engine_factory(bb: Blackboard):
    def make(hard: bool, objective: str) -> Engine:
        return Engine(bb, mockrun.mock_tpm_model(objective), mockrun.mock_model_factory())
    return make


def _progress(agent_id: str, terminal: str, wrote: list) -> None:
    tag = console.label(agent_id)
    if wrote:
        console.write(f"  {console.c('◉', 'green')} {tag} → "
                      f"{console.c(', '.join(wrote), 'cyan')}\n")
    else:
        console.write(f"  {console.c('•', 'gray')} {tag} "
                      f"{console.c(terminal.lower(), 'dim')}\n")


def _run_line(session: ChatSession, broker: Broker, line: str, use_real: bool) -> bool:
    """Execute one input line with live styling. Returns False to quit."""
    talk = session.parse_talk(line)
    if use_real and talk:                       # stream a spokesbot reply live
        agent, msg = talk
        console.write(console.label(agent) + console.c(" ▸ ", "dim"))
        broker.talk(agent, msg, on_delta=console.write)
        console.write("\n")
        return True

    heavy = line.strip().startswith(("/build", "/revise", "/tell")) and use_real
    if heavy:
        with console.Spinner("agents working"):
            out = session.handle(line)
    else:
        out = session.handle(line)

    if out == "__quit__":
        return False
    if out:
        print(out)
    return True


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="pixibot", description="Pixibot chatbot CLI")
    ap.add_argument("--db", default="pixibot.db", help="blackboard db file")
    ap.add_argument("--objective", help="one-shot: build this, then drop into chat")
    args = ap.parse_args(argv)

    bb = Blackboard(args.db, run_id="cli")
    use_real = bool(os.environ.get("ANTHROPIC_API_KEY"))
    broker = Broker(bb, (lambda: _anthropic_spokesbot()) if use_real else (lambda: None))
    engine_factory = _live_engine_factory(bb) if use_real else _offline_engine_factory(bb)
    session = ChatSession(bb, broker, engine_factory)
    if use_real:
        session.progress = _progress

    print(console.banner())
    mode = "live (Claude)" if use_real else "offline (mock — set ANTHROPIC_API_KEY to go live)"
    print(console.c(f"mode: {mode}   ·   /help for commands, /quit to exit", "dim"))

    if args.objective:
        _run_line(session, broker, f"/build {args.objective}", use_real)

    while True:
        try:
            prompt = console.c(f"\n[{session.target}]", "bold", console.agent_color(session.target))
            line = input(prompt + console.c(" › ", "dim"))
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            continue
        if not _run_line(session, broker, line, use_real):
            break
    bb.close()


if __name__ == "__main__":  # pragma: no cover
    main()
