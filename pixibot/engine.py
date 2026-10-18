"""Engine: a persistent run you can build, steer, and revise (DESIGN.md §5/§12/§15).

Holds the blackboard, the scheduler and the agents for one run, so interactions
persist across turns (unlike the one-shot :func:`pixibot.run.run_pipeline`).
``tell`` posts a directive and resumes; ``revise`` re-invokes the TPM with the
prior projection plus feedback and splices in any new agents.

Each build is isolated: a fresh ``run_id`` and its own workspace directory, whose
commands run in this engine's sandbox (DESIGN.md §11).

Contract: docs/contracts/pixibot.engine.md
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import checkpoints, gates, observer
from .blackboard import KIND_CONTROL, KIND_DIRECTIVE, Blackboard
from .checkpoints import Checkpoint, Reviewer
from .gates import GateReport
from .lib import orchestrator, tpm
from .lib.agent import EventHook, RunContext
from .lib.context_manager import ActivationHook, ContextManager, StartHook
from .lib.logbook import get as _get_logger
from .model import Model
from .runtime import executor_for
from .workspace import Workspace

_log = _get_logger("engine")

ModelFactory = Callable[[dict[str, Any]], Model]
Projection = dict[str, Any]

# Greenfield-topology adoptions, each an on/off ablation arm (decision 47).
ALL_FEATURES = frozenset({"continuous_testing", "auto_gate"})
# auto_gate (adoption E's auto-replan) is OFF by default: a failing suite triggers extra
# Opus re-plan rounds, so it's opt-in on cost grounds. Add it to engine.features to enable.
DEFAULT_FEATURES = frozenset({"continuous_testing"})

DEFAULT_WORKSPACE_BASE = "~/pixibot-workspace"
#: How many times one checkpoint may send the run back for revision before the
#: engine stops asking. A person can always `revise` again by hand afterwards.
DEFAULT_REVIEW_ROUNDS = 3


def default_input(objective: str, *, hard: bool = False) -> dict[str, Any]:
    """The strict input for a one-line objective typed at the shell (§6)."""
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
    """One steerable run: build, resume, tell, revise, gate, report."""

    def __init__(self, bb: Blackboard, tpm_model: Model, model_factory: ModelFactory,
                 *, max_steps: int = 200):
        self.bb = bb
        self.tpm_model = tpm_model
        self.model_factory = model_factory
        self.max_steps = max_steps
        self.cm: ContextManager | None = None
        self.projection: Projection | None = None
        self.on_activation: ActivationHook | None = None   # agent finished
        self.on_agent_start: StartHook | None = None       # agent started
        self.on_event: EventHook | None = None             # per-agent activity feed
        self.workspace: Workspace | None = None            # real project dir, per build
        self.workspace_base: str | None = None             # parent dir for workspaces
        self.sandbox = "auto"      # container when docker answers, else confined host
        self.features = set(DEFAULT_FEATURES)  # toggleable adoption arms; auto_gate opt-in
        self.max_gate_revises = 2  # bound on auto-replan rounds (adoption E)
        self.max_review_rounds = DEFAULT_REVIEW_ROUNDS  # per checkpoint (§10)
        self._ceiling: int | None = None                # run token ceiling (§8)
        self.passed_checkpoints: list[str] = []         # ids a human has accepted
        self._settled: set[str] = set()                 # accepted, or out of rounds
        self.last_gate: GateReport | None = None   # the most recent gate run

    # -- workspace -----------------------------------------------------------
    def _new_workspace(self, run_id: str) -> Workspace:
        """A per-run workspace whose commands run in this engine's sandbox."""
        base = self.workspace_base or str(Path(DEFAULT_WORKSPACE_BASE).expanduser())
        root = str(Path(base) / run_id)
        return Workspace(root, executor_for(root, sandbox=self.sandbox))

    def _ensure_workspace(self) -> Workspace:
        if self.workspace is None:
            self.workspace = self._new_workspace(self.bb.run_id)
        return self.workspace

    # -- build ---------------------------------------------------------------
    def _ceiling_of(self, inp: dict[str, Any]) -> int | None:
        """The run's token ceiling from budget_ceilings.compute, or None.

        Unreadable is None rather than zero: a budget nobody can parse must not
        stop a run before it starts (decision 14).
        """
        raw = (inp.get("budget_ceilings") or {}).get("compute")
        if raw is None:
            return None
        try:
            ceiling = int(float(str(raw).strip().rstrip("kK") or 0))
        except (TypeError, ValueError):
            return None
        if str(raw).strip().lower().endswith("k"):
            ceiling *= 1000
        return ceiling if ceiling > 0 else None

    def spent(self) -> int:
        """Tokens this run has spent, as far as the models reported."""
        return self.cm.spent() if self.cm else 0

    def within_budget(self) -> bool:
        """False once the run has spent its ceiling. The scheduler asks this
        after every activation, so a run stops at the ceiling rather than at
        max_steps."""
        return self._ceiling is None or self.spent() < self._ceiling

    def build(self, inp: dict[str, Any], *,
              reviewer: Reviewer | None = None) -> dict[str, Any]:
        """Plan a team for `inp`, run it to quiescence, and report what happened.

        With a `reviewer`, each declared checkpoint is gated mechanically and then
        shown to that reviewer, whose verdict either ends the run or sends it back
        for revision (DESIGN.md §10, decision 12). Without one, the build returns
        as soon as the team quiesces — the unattended path.
        """
        # Isolate this build: a fresh run_id (no stale agents/events from prior
        # builds) and its own workspace dir, so an accidental rebuild can never
        # wipe an earlier run's work. Old runs stay on disk for recovery.
        run_id = f"b{time.strftime('%Y%m%d-%H%M%S')}"
        self.bb.reset_run(run_id)
        self.workspace = self._new_workspace(run_id)
        _log.info("BUILD start run=%s objective=%r ws=%s",
                  run_id, inp.get("objective"), self.workspace.root)

        self._ceiling = self._ceiling_of(inp)
        self.projection = tpm.plan(inp, self.tpm_model)
        _log.info("BUILD run=%s agents=%s", run_id,
                  [a["id"] for a in self.projection.get("agents", [])])
        self.cm = ContextManager(self.bb, max_steps=self.max_steps)
        self.cm.on_activation = self.on_activation
        self.cm.on_agent_start = self.on_agent_start
        objective = self.projection.get("plan_summary") or inp.get("objective", "")
        orchestrator.materialize(
            self.projection, self.cm, self.model_factory,
            RunContext(workspace=self.workspace, on_event=self.on_event, objective=objective),
            continuous=("continuous_testing" in self.features))
        orchestrator.kick(self.bb, self.projection)

        self.passed_checkpoints = []
        self._settled = set()
        gate: GateReport | None = None
        if reviewer is not None:
            steps, gate = self._drive_with_reviews(run_id, reviewer)
        else:
            steps = self.cm.run(barrier=self._budget_barrier)
            if "auto_gate" in self.features:
                steps, gate = self._gate_loop(run_id, steps)

        spent = self.spent()
        if self._ceiling is not None and spent >= self._ceiling:
            _log.warning("BUILD run=%s stopped on its compute ceiling (%d/%d)",
                         run_id, spent, self._ceiling)
        _log.info("BUILD done run=%s steps=%d files=%d tokens=%d/%s gate=%s",
                  run_id, steps, len(self.workspace.list_files()), spent,
                  self._ceiling or "unbounded", gate.passed if gate else "n/a")
        res: dict[str, Any] = {"projection": self.projection, "steps": steps,
                               "workspace": self.workspace.root, "run_id": run_id,
                               "tokens": spent, "budget": self._ceiling}
        if gate is not None:
            res["gate_passed"], res["gate"] = gate.passed, gate
        return res

    def build_objective(self, objective: str, *, hard: bool = False,
                        reviewer: Reviewer | None = None) -> dict[str, Any]:
        return self.build(default_input(objective, hard=hard), reviewer=reviewer)

    # -- the mechanical gate (adoption E) ------------------------------------
    def gate(self) -> GateReport:
        """Run the mechanical gate over this build's workspace, once.

        The checks are derived from the files the agents wrote, not configured
        here: see :func:`pixibot.gates.run_gate`.
        """
        ws = self._ensure_workspace()
        report = gates.run_gate(ws.root, executor=ws.executor)
        self.last_gate = report
        return report

    def _gate_loop(self, run_id: str, steps: int) -> tuple[int, GateReport | None]:
        """Gate, and on failure re-plan (bounded) and gate again.

        Returns (steps, report), or (steps, None) when there was nothing to check.
        """
        report = self.gate()
        if not report.applicable:
            return steps, None
        for attempt in range(self.max_gate_revises + 1):
            _log.info("GATE run=%s attempt=%d lang=%s passed=%s",
                      run_id, attempt, report.language, report.passed)
            if report.passed or attempt == self.max_gate_revises:
                return steps, report
            steps += self.revise("mechanical gate failed — fix these:\n" + report.summary())
            report = self.gate()
        return steps, report

    # -- the human gate (§10: mechanical first, demo second) -----------------
    def pending_checkpoints(self, *, only_triggered: bool = False) -> tuple[Checkpoint, ...]:
        """Declared checkpoints whose agents have finished and that are not settled.

        A settled checkpoint is one a person accepted, or one that ran out of
        review rounds. `only_triggered` excludes checkpoints with no `after`,
        which are due at quiescence and must not pause the run early.
        """
        if not self.projection or not self.cm:
            return ()
        return checkpoints.due(checkpoints.parse(self.projection), self.cm.completed(),
                               already_passed=self._settled,
                               only_triggered=only_triggered)

    def _due_now(self, agent_id: str) -> bool:  # noqa: ARG002 - the barrier signature
        """Scheduler barrier: pause when a checkpoint falls due, or the run is spent."""
        if not self.within_budget():
            return False
        return not any(cp.human for cp in self.pending_checkpoints(only_triggered=True))

    def _budget_barrier(self, agent_id: str) -> bool:  # noqa: ARG002 - barrier signature
        """Scheduler barrier for an unattended run: stop at the ceiling."""
        return self.within_budget()

    def _drive_with_reviews(self, run_id: str,
                            reviewer: Reviewer) -> tuple[int, GateReport | None]:
        """Activate the team, and stop at each checkpoint as it falls due (§10).

        The scheduler pauses on the activation that completes a checkpoint's
        agents, so a demo arrives while the rest of the team has not run yet.
        One pass per checkpoint, plus one for quiescence, bounds the loop: every
        pass either settles a checkpoint or finds none due.
        """
        total = len(checkpoints.parse(self.projection or {})) + 1
        steps = 0
        report: GateReport | None = None
        for _ in range(total):
            steps += self.cm.run(barrier=self._due_now) if self.cm else 0
            due = [cp for cp in self.pending_checkpoints() if cp.human]
            if not due:
                break
            for cp in due:
                spent, report = self._review_one(run_id, cp, reviewer)
                steps += spent
        return steps, report

    def _settle(self, cp: Checkpoint, *, accepted: bool) -> None:
        self._settled.add(cp.id)
        if accepted:
            self.passed_checkpoints.append(cp.id)

    def _review_one(self, run_id: str, cp: Checkpoint,
                    reviewer: Reviewer) -> tuple[int, GateReport]:
        """Gate, demo, verdict - for one checkpoint, bounded by max_review_rounds."""
        steps = 0
        report = self.gate()
        for round_no in range(self.max_review_rounds + 1):
            last = round_no == self.max_review_rounds
            _log.info("CHECKPOINT run=%s cp=%s round=%d gate=%s", run_id, cp.id, round_no,
                      report.passed if report.applicable else "n/a")
            if report.applicable and not report.passed:
                # A red gate never reaches a person (decision 33): the failures go
                # back to the team instead, while rounds remain.
                if last:
                    self._settle(cp, accepted=False)
                    return steps, report
                steps += self.revise("mechanical gate failed - fix these:" + chr(10)
                                     + report.summary())
                report = self.gate()
                continue
            review = reviewer(cp, report.summary() if report.applicable
                              else "no mechanical checks apply")
            self.bb.send("user", f"[checkpoint {cp.id}] "
                         + ("accepted" if review.accepted else f"revise: {review.feedback}"),
                         kind=KIND_CONTROL)
            if review.accepted:
                self._settle(cp, accepted=True)
                _log.info("CHECKPOINT run=%s cp=%s accepted", run_id, cp.id)
                return steps, report
            if last:
                _log.warning("CHECKPOINT run=%s cp=%s out of review rounds", run_id, cp.id)
                self._settle(cp, accepted=False)
                return steps, report
            steps += self.revise(review.feedback)
            report = self.gate()
        return steps, report

    # -- steer ---------------------------------------------------------------
    def resume(self) -> int:
        return self.cm.run() if self.cm else 0

    def tell(self, agent_id: str, directive: str) -> int:
        """Post a directive and resume — the agent picks it up (non-blocking chat)."""
        self.bb.send("user", directive, to=agent_id, kind=KIND_DIRECTIVE)
        return self.resume()

    def revise(self, feedback: str) -> int:
        """Re-plan from demo feedback; register and kick any newly-added agents."""
        if not self.projection or not self.cm:
            raise RuntimeError("nothing to revise — build first")
        self.projection = tpm.revise(self.projection, feedback, self.tpm_model)
        deps = orchestrator.infer_deps(self.projection)
        new = [s for s in self.projection["agents"] if not self.cm.is_registered(s["id"])]
        objective = self.projection.get("plan_summary", "")
        run = RunContext(workspace=self._ensure_workspace(), on_event=self.on_event,
                         objective=objective)
        for spec in new:
            orchestrator.register_one(spec, self.cm, self.model_factory, run)
            # only depend on agents that already completed, so new agents can start
            self.cm.set_deps(spec["id"], deps.get(spec["id"], set()) & self.cm.completed())
        for spec in new:
            self.bb.send("tpm", f"(revision) {feedback}", to=spec["id"])
        return self.resume()

    def report(self) -> str:
        return observer.report(self.bb)
