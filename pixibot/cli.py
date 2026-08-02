"""Pixibot CLI — a chatbot over a run's blackboard (DESIGN.md §12).

Default: chat with the TPM. Talk to any agent with ``@<id> ...``. The Broker uses
a cheap read-only spokesbot, so talking never pauses a working agent; steer with
``/tell``. Kick a build with ``/build <objective>``. Runs live against Claude when
``ANTHROPIC_API_KEY`` is set, otherwise offline (spokesbots show context; /build
uses a canned single-agent plan).
"""

from __future__ import annotations

import argparse
import json
import os

from . import config, observer
from .blackboard import Blackboard
from .interaction import Broker

HELP = """Commands:
  <text>                 talk to the current agent (default: tpm)
  @<agent> <text>        talk to a specific agent (no text: switch to it)
  /at <agent>            switch the default agent
  /tell <agent> <text>   send a non-blocking steering directive
  /agents                list agents on the blackboard
  /report                print the Observer run report
  /build <objective>     plan + run a build for <objective>
  /help                  show this help
  /quit                  exit"""


def _default_input(objective: str) -> dict:
    return {
        "objective": objective,
        "target": {"language": "python", "framework": "none", "platform": "linux"},
        "constraints": [], "non_goals": [], "acceptance_criteria": [f"satisfies: {objective}"],
        "review_cadence": "per-feature",
        "budget_ceilings": {"compute": 100000, "max_depth": "senior", "scope": "one module"},
    }


def _offline_build(bb: Blackboard, objective: str) -> dict:
    from .model import MockModel, ModelResponse, ToolCall
    from .run import run_pipeline

    proj = {
        "plan_summary": f"(offline mock) {objective}",
        "breakdown": [{"id": "f1", "desc": objective, "depends_on": []}],
        "blackboard_schema": {"sections": ["impl/f1"]},
        "agents": [{
            "id": "prog-f1", "role": "programmer",
            "budget": {"compute": 40000, "depth": "senior", "scope": "impl/f1"},
            "blackboard": {"reads": [], "writes": ["impl/f1"]},
            "activates_on": "message",
        }],
        "checkpoints": [{"id": "cp1", "after": ["prog-f1"], "demo": "run", "gate": "user_feedback"}],
    }
    tpm_model = MockModel([ModelResponse(text=json.dumps(proj))])

    def factory(spec):
        section = spec["blackboard"]["writes"][0]
        return MockModel([
            ModelResponse(tool_calls=[ToolCall("t1", "write_artifact",
                          {"section": section, "content": f"# {objective}\n"})], stop_reason="tool_use"),
            ModelResponse(text="done", stop_reason="end_turn"),
        ])

    return run_pipeline(_default_input(objective), tpm_model=tpm_model, model_factory=factory, bb=bb)


def make_builder(bb: Blackboard, use_real: bool):
    def build(objective: str) -> str:
        if use_real:
            from .run import anthropic_model_factory, anthropic_tpm_model, run_pipeline
            result = run_pipeline(_default_input(objective), tpm_model=anthropic_tpm_model(),
                                  model_factory=anthropic_model_factory, bb=bb)
        else:
            result = _offline_build(bb, objective)
        agents = ", ".join(a["agent_id"] for a in bb.list_agents()) or "(none)"
        return (f"Built in {result['steps']} step(s). Agents: {agents}. "
                f"Try '/report' or '@<agent> <question>'.")
    return build


class ChatSession:
    """Command router (testable without the input loop)."""

    def __init__(self, bb: Blackboard, broker: Broker, *, builder=None, target: str = "tpm"):
        self.bb = bb
        self.broker = broker
        self.builder = builder
        self.target = target

    def handle(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        if line in ("/quit", "/exit"):
            return "__quit__"
        if line == "/help":
            return HELP
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
        if line.startswith("/tell "):
            agent, _, text = line[6:].strip().partition(" ")
            if not text:
                return "usage: /tell <agent> <directive>"
            eid = self.broker.tell(agent, text)
            return f"(directive #{eid} sent to {agent} — it will pick it up at its next step)"
        if line.startswith("/build "):
            objective = line[7:].strip()
            if not self.builder:
                return "(build not available)"
            return self.builder(objective)
        if line.startswith("@"):
            agent, _, text = line[1:].partition(" ")
            if not text:
                self.target = agent
                return f"(now talking to {agent})"
            return self.broker.talk(agent, text)
        return self.broker.talk(self.target, line)


def _anthropic_spokesbot():
    from .model import AnthropicModel
    # Haiku: no effort, no adaptive thinking (it rejects them).
    return AnthropicModel(config.SPOKESBOT_MODEL, effort=None, use_thinking=False, max_tokens=1024)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="pixibot", description="Pixibot chatbot CLI")
    ap.add_argument("--db", default="pixibot.db", help="blackboard db file")
    ap.add_argument("--objective", help="one-shot: build this, then drop into chat")
    args = ap.parse_args(argv)

    bb = Blackboard(args.db, run_id="cli")
    use_real = bool(os.environ.get("ANTHROPIC_API_KEY"))
    broker = Broker(bb, (lambda: _anthropic_spokesbot()) if use_real else (lambda: None))
    session = ChatSession(bb, broker, builder=make_builder(bb, use_real))

    print("Pixibot \U0001f916  (/help for commands, /quit to exit)")
    mode = "live (Claude)" if use_real else "offline (no API key — spokesbots show context only)"
    print(f"mode: {mode}")
    if args.objective:
        print(session.builder(args.objective))

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
