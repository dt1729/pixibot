"""Context-manager: the scheduler that drives message-driven activation.

There is no central router. An agent activates when a message addressed to it
lands on the blackboard (DESIGN.md §3). The context-manager polls each agent's
inbox, runs the ones with new mail, and stops when the run **quiesces** (a full
pass activates nobody) or a step budget is hit. Human-gated checkpoint
termination is layered on later (M10).

An *agent runner* is any callable that consumes a batch of inbox events and may
write to the blackboard as a side effect. Real reasoning agents (M5/M6) plug in
here; tests use lightweight mock runners.
"""

from __future__ import annotations

from typing import Callable, Optional

from .blackboard import Blackboard, Event

# runner(bb, agent_id, events) -> terminal lifecycle state (defaults to DONE)
AgentRunner = Callable[[Blackboard, str, list[Event]], Optional[str]]

STATE_SPAWNED = "SPAWNED"
STATE_ACTIVE = "ACTIVE"
STATE_DONE = "DONE"
STATE_BLOCKED = "BLOCKED"


class ContextManager:
    """Drives a set of agents over a shared blackboard until quiescence."""

    def __init__(self, bb: Blackboard, *, max_steps: int = 100):
        self.bb = bb
        self.max_steps = max_steps
        self._runners: dict[str, AgentRunner] = {}

    def register(self, agent_id: str, runner: AgentRunner, **agent_kwargs) -> None:
        """Register an agent's runner and record it in the blackboard registry."""
        self.bb.register_agent(agent_id, **agent_kwargs)
        self._runners[agent_id] = runner

    def step(self) -> int:
        """Run one activation pass; return how many agents activated.

        Agents are polled in registration order. A message written by an
        earlier agent this pass is visible to a later agent in the *same* pass
        (its inbox is polled afterward), which speeds convergence.
        """
        activated = 0
        for agent_id, runner in list(self._runners.items()):
            events = self.bb.poll_inbox(agent_id)
            if not events:
                continue
            activated += 1
            self.bb.set_state(agent_id, STATE_ACTIVE)
            terminal = runner(self.bb, agent_id, events) or STATE_DONE
            self.bb.set_state(agent_id, terminal)
        return activated

    def run(self) -> int:
        """Drive activation until quiescence or the step budget.

        Returns the number of steps taken. A return value equal to
        ``max_steps`` means the budget was exhausted (likely a non-terminating
        interaction) rather than natural quiescence.
        """
        steps = 0
        while steps < self.max_steps:
            if self.step() == 0:
                break  # quiescent: nobody had pending mail
            steps += 1
        return steps
