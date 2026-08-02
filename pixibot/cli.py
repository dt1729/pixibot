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
  /provider [name]       switch model provider (anthropic|gemini|openrouter|offline)
  /form                  show the build-request intake form
  /agents                list agents on the blackboard
  /report                print the Observer run report
  /help                  show this help
  /quit                  exit"""


class ChatSession:
    """Command router (testable without the input loop)."""

    def __init__(self, bb: Blackboard, broker: Broker, engine_factory, *, target: str = "tpm",
                 provider_builder=None, provider_name: str = "custom"):
        self.bb = bb
        self.broker = broker
        self.engine_factory = engine_factory  # (hard: bool, objective: str) -> Engine
        self.target = target
        self.hard = False
        self.engine = None
        self.progress = None  # live-progress hook forwarded to the engine on /build
        self.provider_builder = provider_builder  # (name) -> (label, use_real, broker, factory)
        self.provider_name = provider_name

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
        if line.startswith("/provider"):
            arg = line[len("/provider"):].strip().lower()
            if not arg:
                return (f"current provider: {self.provider_name}. "
                        f"switch with: /provider <{' | '.join(PROVIDERS)}>")
            if arg not in PROVIDERS:
                return f"unknown provider '{arg}'. options: {', '.join(PROVIDERS)}"
            if self.provider_builder is None:
                return "(provider switching not available here)"
            label, use_real, broker, factory = self.provider_builder(arg)
            self.broker, self.engine_factory = broker, factory
            self.provider_name, self.engine = arg, None
            self.progress = _progress if use_real else None
            return f"(switched to {label} — run /build to start a fresh run)"
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


def _gemini_spokesbot():
    from .model import OpenAICompatModel
    return OpenAICompatModel(config.GEMINI_SPOKESBOT_MODEL, base_url=config.GEMINI_BASE_URL,
                             api_key_env="GEMINI_API_KEY", max_tokens=1024)


def _gemini_engine_factory(bb: Blackboard):
    from .run import gemini_tpm_model, make_gemini_model_factory

    def make(hard: bool, objective: str) -> Engine:
        return Engine(bb, gemini_tpm_model(), make_gemini_model_factory(hard))
    return make


def _openrouter_spokesbot():
    from .model import OpenAICompatModel
    return OpenAICompatModel(config.OPENROUTER_SPOKESBOT_MODEL, base_url=config.OPENROUTER_BASE_URL,
                             api_key_env="OPENROUTER_API_KEY", max_tokens=1024)


def _openrouter_engine_factory(bb: Blackboard):
    from .run import make_openrouter_model_factory, openrouter_tpm_model

    def make(hard: bool, objective: str) -> Engine:
        return Engine(bb, openrouter_tpm_model(), make_openrouter_model_factory(hard))
    return make


PROVIDERS = ("anthropic", "gemini", "openrouter", "offline")


def _make_provider(bb: Blackboard, name: str):
    """Return (label, use_real, broker, engine_factory) for a provider name."""
    if name == "gemini":
        return ("gemini (Google AI Studio, free tier)", True,
                Broker(bb, lambda: _gemini_spokesbot()), _gemini_engine_factory(bb))
    if name == "openrouter":
        return ("openrouter", True,
                Broker(bb, lambda: _openrouter_spokesbot()), _openrouter_engine_factory(bb))
    if name == "anthropic":
        return ("anthropic (Claude)", True,
                Broker(bb, lambda: _anthropic_spokesbot()), _live_engine_factory(bb))
    return ("offline (mock)", False, Broker(bb, lambda: None), _offline_engine_factory(bb))


def _auto_provider_name() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "offline"


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
    ap.add_argument("--provider", choices=("auto",) + PROVIDERS, default="auto",
                    help="model provider (default: auto-detect from env keys)")
    ap.add_argument("--objective", help="one-shot: build this, then drop into chat")
    args = ap.parse_args(argv)

    bb = Blackboard(args.db, run_id="cli")
    name = args.provider if args.provider != "auto" else _auto_provider_name()
    label, use_real, broker, engine_factory = _make_provider(bb, name)

    session = ChatSession(bb, broker, engine_factory,
                          provider_builder=lambda n: _make_provider(bb, n), provider_name=name)
    if use_real:
        session.progress = _progress

    print(console.banner())
    print(console.c(f"mode: {label}   ·   /provider to switch  ·  /help  ·  /quit", "dim"))

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
