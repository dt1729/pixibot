"""Orchestrator: materialize a projection into a live MoA (DESIGN.md §5).

Each projection agent-spec becomes a ReasoningAgent sharing one
:class:`~pixibot.agent.RunContext` (the workspace, the objective, the activity
feed). Coordination is **deterministic**: an agent runs only after the agents it
depends on have completed. Dependencies come from the projection's explicit
``depends_on`` if present, otherwise from a role-ordered chain — so every agent
reliably participates even when the model's plan is loose.

Contract: docs/contracts/pixibot.orchestrator.md
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from .agent import ReasoningAgent, RunContext
from .blackboard import Blackboard
from .context_manager import ContextManager
from .factory import make_agent
from .model import Model

ModelFactory = Callable[[dict[str, Any]], Model]
Projection = dict[str, Any]

# Lower rank runs first. Unknown roles default to the implementation tier.
ROLE_RANK = {
    "topology": 0, "architect": 0,
    "lld": 1, "design": 1,
    "test-designer": 1.5,  # designs tests from the spec; after design, before/with programmers
    "programmer": 2, "coder": 2, "implementer": 2, "specialist": 2,
    "tester": 3, "qa": 3,
    "reviewer": 4,
    "docs": 5, "documentation": 5,
}
_DEFAULT_RANK = 2


def infer_deps(projection: Projection) -> dict[str, set[str]]:
    """agent_id -> set of agent_ids it depends on."""
    agents = projection["agents"]
    ids = {a["id"] for a in agents}
    if any(a.get("depends_on") for a in agents):
        return {a["id"]: {d for d in a.get("depends_on", []) if d in ids} for a in agents}
    # Fallback: a strict chain ordered by role rank (reliable, if serial).
    ordered = sorted(agents,
                     key=lambda a: (ROLE_RANK.get(a.get("role", "specialist"), _DEFAULT_RANK),
                                    a["id"]))
    deps: dict[str, set[str]] = {}
    for i, a in enumerate(ordered):
        deps[a["id"]] = {ordered[i - 1]["id"]} if i > 0 else set()
    return deps


def blind_sections(projection: Projection) -> tuple[str, ...]:
    """Sections a test-designer may not read (adoption D, mechanical blindness):
    every section a programmer writes. Tests must encode what the software SHOULD
    do, so the implementation is withheld at the tool layer, not just in prose."""
    return tuple(sorted({
        w for s in projection["agents"] if s.get("role") == "programmer"
        for w in s.get("blackboard", {}).get("writes", ())
    }))


def register_one(spec: dict[str, Any], cm: ContextManager, model_factory: ModelFactory,
                 run: RunContext, *, read_denylist: tuple[str, ...] = ()) -> ReasoningAgent:
    """Build one agent from its spec and register it with the scheduler."""
    budget = spec.get("budget", {})
    model = model_factory(spec)
    agent = make_agent(spec, model, run, read_denylist=read_denylist)
    cm.register(agent.agent_id, agent.runner, role=agent.role,
                depth=budget.get("depth", "senior"),
                model=getattr(model, "model_id", None),
                scope=budget.get("scope"), budget=budget)
    return agent


def materialize(projection: Projection, cm: ContextManager, model_factory: ModelFactory,
                run: RunContext | None = None, *, continuous: bool = True) -> ContextManager:
    """Register every agent in the projection, wired for dependency activation."""
    run = run or RunContext()
    if not run.objective:
        run = dataclasses.replace(run, objective=projection.get("plan_summary", ""))
    deps = infer_deps(projection)
    blind = blind_sections(projection)
    for spec in projection["agents"]:
        deny = blind if spec.get("role") == "test-designer" else ()
        register_one(spec, cm, model_factory, run, read_denylist=deny)
        cm.set_deps(spec["id"], deps.get(spec["id"], set()))
        # Adoption A — continuous testing: a reader re-activates whenever an
        # artifact lands in a section it reads (the r=0.72 self-testing loop),
        # not just once after its dependencies complete.
        if continuous:
            for pattern in spec.get("blackboard", {}).get("reads", ()):
                cm.add_dependency(pattern, spec["id"])
    return cm


def root_ids(projection: Projection) -> list[str]:
    """The agents with nothing to wait for — where the cascade starts."""
    deps = infer_deps(projection)
    roots = [aid for aid, d in deps.items() if not d]
    return roots or [projection["agents"][0]["id"]]


def kick(bb: Blackboard, projection: Projection) -> None:
    """Address the plan summary to the root agents to start the cascade."""
    summary = projection.get("plan_summary", "begin")
    for aid in root_ids(projection):
        bb.send("tpm", summary, to=aid)
