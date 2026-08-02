"""Interaction layer — talk to agents without pausing them (DESIGN.md §12).

When a user wants to talk to an agent, we do NOT interrupt the working agent.
The ``Broker`` snapshots that agent's context from the blackboard up to now and
spins a cheap, read-only *spokesbot* (Haiku — cheapest per token) briefed with
that snapshot to converse with the user. The real agent keeps working.

Steering is separate and also non-blocking: ``tell()`` posts a ``directive``
event to the agent's inbox, which the agent consumes at its next step
(§12 obey/escalate).
"""

from __future__ import annotations

from typing import Callable, Optional

from .blackboard import KIND_DIRECTIVE, Blackboard
from .model import Model

_CLIP = 200  # truncate long payloads in the snapshot


def snapshot(bb: Blackboard, agent_id: str) -> str:
    """A text briefing of an agent's context up to now: identity + activity."""
    agent = bb.get_agent(agent_id)
    lines: list[str] = []
    if agent:
        lines.append(f"Agent: {agent_id}")
        lines.append(
            f"role={agent.get('role')} depth={agent.get('depth')} "
            f"scope={agent.get('scope')} state={agent.get('state')}"
        )
    else:
        lines.append(f"Agent: {agent_id} (not registered)")

    inbox = bb.history(to_agent=agent_id)
    if inbox:
        lines.append("\nMessages received:")
        for e in inbox[-10:]:
            lines.append(f"  <- {e.from_agent}: {e.payload[:_CLIP]}")

    produced = bb.history(from_agent=agent_id)
    if produced:
        lines.append("\nWork produced:")
        for e in produced[-10:]:
            where = e.section or e.to_agent or "?"
            lines.append(f"  -> [{e.kind}:{where}] {e.payload[:_CLIP]}")
    return "\n".join(lines)


def _spokesbot_system(agent_id: str, snap: str) -> str:
    return (
        f"You are a read-only spokesperson for the '{agent_id}' agent in the "
        "Pixibot system. Using ONLY the context below, answer the user's questions "
        "about what this agent is doing and why. You cannot change its work. If the "
        "user wants to change the agent's behavior, tell them to issue a directive "
        f"with the CLI: /tell {agent_id} <instruction>.\n\n"
        f"--- {agent_id} context ---\n{snap}"
    )


PROJECT_TARGETS = {"tpm", "project", "overseer", "orchestrator"}


def project_snapshot(bb: Blackboard) -> str:
    """A whole-project overview: agents, files/artifacts, and recent activity."""
    agents = bb.list_agents()
    state = bb.current_state()
    events = bb.history()
    lines = ["PROJECT OVERVIEW",
             f"agents: {len(agents)}   files: {len(state)}   events: {len(events)}"]
    if agents:
        lines.append("\nAgents:")
        for a in agents:
            lines.append(f"  {a['agent_id']}: role={a.get('role')} depth={a.get('depth')} "
                         f"state={a.get('state')}")
    if state:
        lines.append("\nFiles / artifacts:")
        for section in sorted(state):
            lines.append(f"  {section} (by {state[section].from_agent})")
    if events:
        lines.append("\nRecent activity:")
        for e in events[-8:]:
            dest = e.to_agent or e.section or ""
            lines.append(f"  {e.from_agent} -> {dest} [{e.kind}]")
    if not agents and not state:
        lines.append("\n(no build has run yet — run: build <objective>  or  build-from <file.md>)")
    return "\n".join(lines)


def _project_system(snap: str) -> str:
    return (
        "You are the overseer for a Pixibot build — the friendly assistant the user talks to "
        "by default. Using ONLY the project overview below, answer their questions about overall "
        "status, what each agent did, and the files produced. You are read-only: to start a build "
        "tell them to run 'build <objective>' or 'build-from <file.md>'; to steer an agent, "
        "'tell <agent> <instruction>'; to inspect files, 'ls' / 'cat <file>'.\n\n"
        f"--- project overview ---\n{snap}"
    )


# spokesbot_factory() -> Model | None  (None => offline mode: show context only)
SpokesbotFactory = Callable[[], Optional[Model]]


class Broker:
    """Read-only conversational access to agents, plus non-blocking steering."""

    def __init__(self, bb: Blackboard, spokesbot_factory: Optional[SpokesbotFactory] = None):
        self.bb = bb
        self._factory = spokesbot_factory or (lambda: None)
        self._history: dict[str, list[dict]] = {}  # per-agent chat history

    def talk(self, agent_id: str, message: str, on_delta=None) -> str:
        """Ask the agent's spokesbot a question. Read-only; never pauses the agent.

        Pass ``on_delta`` to stream the reply token-by-token (live mode).
        """
        if agent_id in PROJECT_TARGETS:
            snap = project_snapshot(self.bb)
            system = _project_system(snap)
            hist_key = "__project__"
        else:
            snap = snapshot(self.bb, agent_id)
            system = _spokesbot_system(agent_id, snap)
            hist_key = agent_id
        model = self._factory()
        if model is None:  # offline: present the context itself
            return f"(offline — set an API key for live chat)\n\n{snap}"
        hist = self._history.setdefault(hist_key, [])
        hist.append({"role": "user", "content": message})
        resp = model.stream_generate(system=system, messages=hist, tools=[], on_delta=on_delta)
        hist.append({"role": "assistant", "content": [{"type": "text", "text": resp.text}]})
        return resp.text

    def tell(self, agent_id: str, directive: str) -> int:
        """Post a steering directive to the agent's inbox (non-blocking)."""
        return self.bb.send("user", directive, to=agent_id, kind=KIND_DIRECTIVE)
