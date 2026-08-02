"""Orchestrator: materialize a projection into a live MoA (DESIGN.md §5).

Turns each projection agent-spec into a ReasoningAgent registered with the
context-manager, wires its blackboard *reads* as dependencies (so it activates
when upstream artifacts appear), and kicks the run by addressing the plan to the
root agents (those with no reads).
"""

from __future__ import annotations

from typing import Callable

from .blackboard import Blackboard
from .context_manager import ContextManager
from .factory import make_agent
from .model import Model

ModelFactory = Callable[[dict], Model]


def register_one(spec: dict, bb: Blackboard, cm: ContextManager, model_factory: ModelFactory):
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
    for section in reads:
        cm.add_dependency(section, spec["id"])
    return agent


def materialize(projection: dict, bb: Blackboard, cm: ContextManager,
                model_factory: ModelFactory) -> ContextManager:
    for spec in projection["agents"]:
        register_one(spec, bb, cm, model_factory)
    return cm


def root_ids(projection: dict) -> list[str]:
    roots = [a["id"] for a in projection["agents"]
             if not a.get("blackboard", {}).get("reads")]
    return roots or [projection["agents"][0]["id"]]


def kick(bb: Blackboard, projection: dict) -> None:
    """Address the plan summary to the root agents to start the cascade."""
    summary = projection.get("plan_summary", "begin")
    for aid in root_ids(projection):
        bb.send("tpm", summary, to=aid)
