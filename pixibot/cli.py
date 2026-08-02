"""Pixibot CLI — a chatbot over a persistent run (DESIGN.md §12).

Chat with the TPM by default; ``@<id>`` talks to any agent via a cheap read-only
spokesbot that never pauses it; ``/tell`` steers (non-blocking); ``/build`` plans
and runs; ``/revise`` re-plans from feedback; ``/form`` shows the intake form;
``/hard`` toggles hard-development routing (principal -> Fable 5 at xhigh).

Runs live against Claude when ``ANTHROPIC_API_KEY`` is set, otherwise offline
against a canned multi-agent mock (so all the wiring is demonstrable with no key).
"""

from __future__ import annotations

import argparse
import os

from . import config, intake, mockrun, observer
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

    def _agents_line(self) -> str:
        return ", ".join(a["agent_id"] for a in self.bb.list_agents()) or "(none)"

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

    print("Pixibot \U0001f916  (/help for commands, /quit to exit)")
    print(f"mode: {'live (Claude)' if use_real else 'offline (no API key — canned multi-agent mock)'}")
    if args.objective:
        print(session.handle(f"/build {args.objective}"))

    while True:
        try:
            line = input(f"[{session.target}] > ")
        except (EOFError, KeyboardInterrupt):
            break
        out = session.handle(line)
        if out == "__quit__":
            break
        if out:
            print(out)
    bb.close()


if __name__ == "__main__":  # pragma: no cover
    main()
