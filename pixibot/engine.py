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
from .logbook import get as _get_logger
from .model import Model

_log = _get_logger("engine")

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
        self.on_activation = None  # live-progress hook (agent finished)
        self.on_agent_start = None  # live-progress hook (agent started)
        self.on_event = None       # per-agent activity feed on_event(agent_id, kind, detail)
        self.workspace = None      # real project dir; created per build
        self.workspace_base = None # parent dir for per-run workspaces (defaults to ~/pixibot-workspace)

    def _ensure_workspace(self):
        if self.workspace is None:
            import os

            from .workspace import Workspace
            root = os.path.join(os.path.expanduser("~/pixibot-workspace"), self.bb.run_id)
            self.workspace = Workspace(root)
        return self.workspace

    def build(self, inp: dict) -> dict:
        import os
        import time

        from .workspace import Workspace

        # Isolate this build: a fresh run_id (no stale agents/events from prior
        # builds) and its own workspace dir, so an accidental rebuild can never
        # wipe an earlier run's work. Old runs stay on disk for recovery.
        run_id = f"b{time.strftime('%Y%m%d-%H%M%S')}"
        self.bb.reset_run(run_id)
        base = self.workspace_base or os.path.expanduser("~/pixibot-workspace")
        self.workspace = Workspace(os.path.join(base, run_id))
        _log.info("BUILD start run=%s objective=%r ws=%s",
                  run_id, inp.get("objective"), self.workspace.root)

        self.projection = tpm.plan(inp, self.tpm_model)
        _log.info("BUILD run=%s agents=%s", run_id,
                  [a["id"] for a in self.projection.get("agents", [])])
        self.cm = ContextManager(self.bb, max_steps=self.max_steps)
        self.cm.on_activation = self.on_activation
        self.cm.on_agent_start = self.on_agent_start
        orchestrator.materialize(self.projection, self.bb, self.cm,
                                 self.model_factory, self.workspace, self.on_event)
        orchestrator.kick(self.bb, self.projection)
        steps = self.cm.run()
        _log.info("BUILD done run=%s steps=%d files=%d",
                  run_id, steps, len(self.workspace.list_files()))
        return {"projection": self.projection, "steps": steps,
                "workspace": self.workspace.root, "run_id": run_id}

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
        deps = orchestrator.infer_deps(self.projection)
        new = [s for s in self.projection["agents"] if not self.cm.is_registered(s["id"])]
        for spec in new:
            orchestrator.register_one(spec, self.bb, self.cm, self.model_factory,
                                      self._ensure_workspace(), self.on_event)
            # only depend on agents that already completed, so new agents can start
            self.cm.set_deps(spec["id"], deps.get(spec["id"], set()) & self.cm._done)
        for spec in new:
            self.bb.send("tpm", f"(revision) {feedback}", to=spec["id"])
        return self.resume()

    def report(self) -> str:
        return observer.report(self.bb)
