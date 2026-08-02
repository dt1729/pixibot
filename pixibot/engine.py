"""Engine: a persistent run you can build, steer, and revise (DESIGN.md §5/§12/§15).

Holds the blackboard + context-manager + agents for one run so interactions
persist across turns (unlike a one-shot pipeline). `tell` posts a directive and
resumes; `revise` re-invokes the TPM with the prior projection + feedback and
splices in any new agents.
"""

from __future__ import annotations

from typing import Callable, Optional

from . import observer, orchestrator, tpm
from .blackboard import KIND_DIRECTIVE, Blackboard
from .context_manager import ContextManager
from .model import Model

ModelFactory = Callable[[dict], Model]


def default_input(objective: str, *, hard: bool = False) -> dict:
    return {
        "objective": objective,
        "target": {"language": "python", "framework": "none", "platform": "linux"},
        "constraints": [], "non_goals": [], "acceptance_criteria": [f"satisfies: {objective}"],
        "review_cadence": "per-feature",
        "budget_ceilings": {"compute": 200000,
                            "max_depth": "principal" if hard else "senior",
                            "scope": "as needed"},
        "hard": hard,
    }


class Engine:
    def __init__(self, bb: Blackboard, tpm_model: Model, model_factory: ModelFactory,
                 *, max_steps: int = 200):
        self.bb = bb
        self.tpm_model = tpm_model
        self.model_factory = model_factory
        self.max_steps = max_steps
        self.cm: Optional[ContextManager] = None
        self.projection: Optional[dict] = None

    def build(self, inp: dict) -> dict:
        self.projection = tpm.plan(inp, self.tpm_model)
        self.cm = ContextManager(self.bb, max_steps=self.max_steps)
        orchestrator.materialize(self.projection, self.bb, self.cm, self.model_factory)
        orchestrator.kick(self.bb, self.projection)
        steps = self.cm.run()
        return {"projection": self.projection, "steps": steps}

    def build_objective(self, objective: str, *, hard: bool = False) -> dict:
        return self.build(default_input(objective, hard=hard))

    def resume(self) -> int:
        return self.cm.run() if self.cm else 0

    def tell(self, agent_id: str, directive: str) -> int:
        """Post a directive and resume — the agent picks it up (non-blocking chat)."""
        self.bb.send("user", directive, to=agent_id, kind=KIND_DIRECTIVE)
        return self.resume()

    def revise(self, feedback: str) -> int:
        """Re-plan from demo feedback; register + kick any newly-added agents."""
        if not self.projection or not self.cm:
            raise RuntimeError("nothing to revise — build first")
        self.projection = tpm.revise(self.projection, feedback, self.tpm_model)
        new = [s for s in self.projection["agents"] if not self.cm.is_registered(s["id"])]
        for spec in new:
            orchestrator.register_one(spec, self.bb, self.cm, self.model_factory)
        for spec in new:
            self.bb.send("tpm", f"(revision) {feedback}", to=spec["id"])
        return self.resume()

    def report(self) -> str:
        return observer.report(self.bb)
