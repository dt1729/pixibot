"""Context-manager: the scheduler that drives message-driven activation.

An agent activates when a message addressed to it lands on the blackboard
(DESIGN.md §3). Beyond that, the context-manager performs **dependency-driven
handshakes**: when an agent writes an artifact to a section, every agent that
*reads* that section is notified — so a projection's data dependencies drive the
cascade (topology -> LLD -> programmer -> tester) without agents having to know
each other's names.

The loop stops when the run **quiesces** (a full pass activates nobody) or the
step budget is hit. `run()` is resumable — call it again after new events (a
directive, a revision) arrive.
"""

from __future__ import annotations

from typing import Callable, Optional

from .blackboard import KIND_ARTIFACT, Blackboard, Event

# runner(bb, agent_id, events) -> terminal lifecycle state (defaults to DONE)
AgentRunner = Callable[[Blackboard, str, list[Event]], Optional[str]]

STATE_SPAWNED = "SPAWNED"
STATE_ACTIVE = "ACTIVE"
STATE_DONE = "DONE"
STATE_BLOCKED = "BLOCKED"

ROUTER = "context-manager"  # from_agent used for dependency-ready notifications


class ContextManager:
    """Drives a set of agents over a shared blackboard until quiescence."""

    def __init__(self, bb: Blackboard, *, max_steps: int = 100):
        self.bb = bb
        self.max_steps = max_steps
        self._runners: dict[str, AgentRunner] = {}
        self._readers: list[tuple[str, str]] = []          # (section_pattern, reader_id)
        self._notified: set[tuple[int, str]] = set()       # (artifact_event_id, reader_id)

    # -- registration --------------------------------------------------------
    def register(self, agent_id: str, runner: AgentRunner, **agent_kwargs) -> None:
        self.bb.register_agent(agent_id, **agent_kwargs)
        self._runners[agent_id] = runner

    def is_registered(self, agent_id: str) -> bool:
        return agent_id in self._runners

    def add_dependency(self, section_pattern: str, reader_agent_id: str) -> None:
        """Reader activates when an artifact is written to a matching section."""
        self._readers.append((section_pattern, reader_agent_id))

    # -- dependency routing --------------------------------------------------
    @staticmethod
    def _matches(section: Optional[str], pattern: str) -> bool:
        if section is None:
            return False
        p = pattern.rstrip("/")
        return section == pattern or section == p or section.startswith(p + "/")

    def _route_new_artifacts(self, author_id: str, since_id: int) -> None:
        for e in self.bb.history(from_agent=author_id):
            if e.kind != KIND_ARTIFACT or e.id <= since_id:
                continue
            for pattern, reader in self._readers:
                if reader == author_id:
                    continue
                if self._matches(e.section, pattern) and (e.id, reader) not in self._notified:
                    self.bb.send(ROUTER, f"ready: {e.section}", to=reader)
                    self._notified.add((e.id, reader))

    # -- the loop ------------------------------------------------------------
    def step(self) -> int:
        activated = 0
        for agent_id, runner in list(self._runners.items()):
            events = self.bb.poll_inbox(agent_id)
            if not events:
                continue
            activated += 1
            since = self.bb.latest_event_id()
            self.bb.set_state(agent_id, STATE_ACTIVE)
            terminal = runner(self.bb, agent_id, events) or STATE_DONE
            self.bb.set_state(agent_id, terminal)
            self._route_new_artifacts(agent_id, since)
        return activated

    def run(self) -> int:
        """Drive activation until quiescence or the step budget (resumable)."""
        steps = 0
        while steps < self.max_steps:
            if self.step() == 0:
                break
            steps += 1
        return steps
