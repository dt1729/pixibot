"""Orchestrator: materialize a projection into a live MoA (DESIGN.md §5).

Turns each projection agent-spec into a ReasoningAgent registered with the
context-manager, then kicks off the run by addressing the plan to the first
agent. ``model_factory(spec) -> Model`` decides which model each agent gets
(mixed-by-depth in production; a MockModel in tests).
"""

from __future__ import annotations

from typing import Callable

from .blackboard import Blackboard
from .context_manager import ContextManager
from .factory import make_agent
from .model import Model

ModelFactory = Callable[[dict], Model]


def materialize(projection: dict, bb: Blackboard, cm: ContextManager,
                model_factory: ModelFactory) -> ContextManager:
    for spec in projection["agents"]:
        budget = spec.get("budget", {})
        depth = budget.get("depth", "senior")
        scope = budget.get("scope")
        reads = tuple(spec.get("blackboard", {}).get("reads", ()))
        model = model_factory(spec)
        agent = make_agent(
            spec["id"], role=spec.get("role", "specialist"),
            depth=depth, model=model, scope=scope, reads=reads,
        )
        cm.register(
            agent.agent_id, agent.runner,
            role=agent.role, depth=depth,
            model=getattr(model, "model_id", None), scope=scope, budget=budget,
        )
    return cm


def kick(bb: Blackboard, projection: dict) -> None:
    """Address the plan summary to the first agent to start the cascade."""
    first = projection["agents"][0]["id"]
    bb.send("tpm", projection.get("plan_summary", "begin"), to=first)
