"""Orchestrator: materialize a projection into a live MoA (DESIGN.md §5).

Each projection agent-spec becomes a ReasoningAgent (with the run's workspace).
Coordination is **deterministic**: an agent runs only after the agents it depends
on have completed. Dependencies come from the projection's explicit
``depends_on`` if present, otherwise from a role-ordered chain — so every agent
reliably participates even when the model's plan is loose.
"""

from __future__ import annotations

from typing import Callable

from .blackboard import Blackboard
from .context_manager import ContextManager
from .factory import make_agent
from .model import Model

ModelFactory = Callable[[dict], Model]

# Lower rank runs first. Unknown roles default to the implementation tier.
ROLE_RANK = {
    "topology": 0, "architect": 0,
    "lld": 1, "design": 1,
    "programmer": 2, "coder": 2, "implementer": 2, "specialist": 2,
    "tester": 3, "qa": 3,
    "reviewer": 4,
    "docs": 5, "documentation": 5,
}


def infer_deps(projection: dict) -> dict:
    """agent_id -> set of agent_ids it depends on."""
    agents = projection["agents"]
    ids = {a["id"] for a in agents}
    if any(a.get("depends_on") for a in agents):
        return {a["id"]: {d for d in a.get("depends_on", []) if d in ids} for a in agents}
    # Fallback: a strict chain ordered by role rank (reliable, if serial).
    ordered = sorted(agents, key=lambda a: (ROLE_RANK.get(a.get("role", "specialist"), 2), a["id"]))
    deps = {}
    for i, a in enumerate(ordered):
        deps[a["id"]] = {ordered[i - 1]["id"]} if i > 0 else set()
    return deps


def register_one(spec: dict, bb: Blackboard, cm: ContextManager,
                 model_factory: ModelFactory, workspace=None, on_event=None, objective=""):
    budget = spec.get("budget", {})
    depth = budget.get("depth", "senior")
    scope = budget.get("scope")
    reads = tuple(spec.get("blackboard", {}).get("reads", ()))
    model = model_factory(spec)
    agent = make_agent(spec["id"], role=spec.get("role", "specialist"),
                       depth=depth, model=model, scope=scope, reads=reads,
                       workspace=workspace, on_event=on_event, objective=objective)
    cm.register(agent.agent_id, agent.runner, role=agent.role, depth=depth,
                model=getattr(model, "model_id", None), scope=scope, budget=budget)
    return agent


def materialize(projection: dict, bb: Blackboard, cm: ContextManager,
                model_factory: ModelFactory, workspace=None, on_event=None,
                objective="") -> ContextManager:
    deps = infer_deps(projection)
    objective = objective or projection.get("plan_summary", "")
    for spec in projection["agents"]:
        register_one(spec, bb, cm, model_factory, workspace, on_event, objective)
        cm.set_deps(spec["id"], deps.get(spec["id"], set()))
    return cm


def root_ids(projection: dict) -> list[str]:
    deps = infer_deps(projection)
    roots = [aid for aid, d in deps.items() if not d]
    return roots or [projection["agents"][0]["id"]]


def kick(bb: Blackboard, projection: dict) -> None:
    """Address the plan summary to the root agents to start the cascade."""
    summary = projection.get("plan_summary", "begin")
    for aid in root_ids(projection):
        bb.send("tpm", summary, to=aid)
