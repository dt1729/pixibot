"""Benchmark harness over the toggleable topology arms (DESIGN.md §16, decision 47).

The adoptions landed as on/off arms (``Engine.features``) precisely so they could
be measured rather than believed. This module runs a task list against a set of
arms and reports what each produced, judged by the **mechanical gate** — the same
green/red the human checkpoint uses, so a benchmark score and a shipped build mean
the same thing.

The task set matters as much as the harness. ``DEFAULT_TASKS`` are built to need
a team: more than one module, an interface two modules must agree on, a
constraint that fights the obvious approach, and behaviour only a deterministic
test pins down. ``SMOKE_TASKS`` are the easy exercises a single model one-shots;
every arm scores the same on those, which measures nothing about a topology.

Run it:

    python -m pixibot.benchmark --provider offline            # smoke, no key
    python -m pixibot.benchmark --provider anthropic --out bench.json

One bad task does not end a suite: a build that raises becomes a failed
:class:`Result` carrying the error, because the comparison between arms is the
product and losing it to one timeout would be the worst outcome.

Contract: docs/contracts/pixibot.benchmark.md
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import providers
from .blackboard import Blackboard
from .engine import ALL_FEATURES, DEFAULT_FEATURES, Engine, default_input
from .lib import logbook
from .lib.logbook import get as _get_logger

_log = _get_logger("benchmark")

_DEFAULT_BASE = "~/pixibot-bench"


@dataclass(frozen=True)
class Task:
    """One benchmark prompt. `objective` is what a user would type at the shell."""

    id: str
    objective: str
    language: str = "python"
    hard: bool = False


@dataclass(frozen=True)
class Arm:
    """One configuration under test: a name and the feature set it turns on."""

    name: str
    features: frozenset[str] = field(default_factory=frozenset)


#: A 2x2 ablation over the two arms that exist: each feature alone, neither, both.
#: "default" is the shipped configuration, so a report says what shipping costs.
STANDARD_ARMS: tuple[Arm, ...] = (
    Arm("bare", frozenset()),
    Arm("default", frozenset(DEFAULT_FEATURES)),          # continuous testing only
    Arm("auto-gate", frozenset({"auto_gate"})),           # failure-driven re-plan only
    Arm("both", frozenset(ALL_FEATURES)),
)

#: Small, self-contained programs with an obvious test surface. Every arm scores
#: the same on these, so they measure that the harness runs - not whether a
#: topology helps. Use them as a smoke check: --set smoke.
SMOKE_TASKS: tuple[Task, ...] = (
    Task("lru-cache", "an LRU cache class with a capacity bound, plus pytest tests"),
    Task("csv2json", "a CLI that converts a CSV file to JSON, plus pytest tests"),
    Task("url-shortener", "a URL shortener library with encode/decode and pytest tests"),
    Task("md-table", "a Markdown table formatter that aligns columns, plus pytest tests"),
    Task("interval-merge", "a function that merges overlapping integer intervals, "
                           "plus pytest tests"),
)

#: Tasks built to need a team. Each one has more than one module, an interface
#: two modules must agree on, a constraint that fights the obvious approach, and
#: a behaviour only a deterministic test pins down. A single agent that writes
#: the obvious thing fails the stated acceptance criteria.
DEFAULT_TASKS: tuple[Task, ...] = (
    Task("ledger", (
        "A double-entry ledger in three modules: ledger.py (the core), importer.py "
        "(reads CSV rows into transactions) and cli.py (a command-line entry point), "
        "plus pytest tests. Acceptance: every transaction must balance to zero across "
        "its entries, and the importer must REJECT an unbalanced row with a named error "
        "rather than skip it or round it. The core must not import the importer or the "
        "CLI. Tests must cover a balanced import, an unbalanced row, and a row with an "
        "unknown account.")),
    Task("rate-limiter", (
        "A token-bucket rate limiter in two modules: limiter.py and clock.py, plus "
        "pytest tests. Acceptance: per-key buckets with a documented burst size and "
        "refill rate; the limiter takes its clock as a dependency so tests never sleep; "
        "refill is proportional to elapsed time, not a whole token per call. Tests must "
        "prove refill over simulated time, a burst being exhausted, and two keys not "
        "sharing a bucket - with no call to time.sleep anywhere.")),
    Task("config-layers", (
        "A layered configuration loader in two modules: layers.py and coerce.py, plus "
        "pytest tests. Acceptance: precedence is defaults < file < environment < "
        "command line, decided PER KEY rather than per layer; values coerce to the "
        "declared type (int, bool, list) and an uncoercible value raises a named error "
        "that says which key and which layer it came from; the loader reports, for every "
        "key, which layer won. Tests must prove the per-key precedence and one coercion "
        "failure.")),
    Task("retry-policy", (
        "A retry policy in two modules: policy.py and backoff.py, plus pytest tests. "
        "Acceptance: exponential backoff with jitter, where both the clock and the "
        "random source are injected so every test is deterministic; a total elapsed-time "
        "budget that stops retrying even when attempts remain; and a NonRetryable error "
        "class that is never retried. Tests must prove the backoff sequence exactly, the "
        "elapsed budget cutting a retry short, and NonRetryable failing on the first "
        "attempt - with no sleeping and no randomness.")),
    Task("plugin-registry", (
        "A plugin registry in three modules: protocol.py (the Plugin interface), "
        "registry.py (registration and lookup) and discover.py (finds plugins from a "
        "mapping of name to callable), plus pytest tests. Acceptance: registry and "
        "discover must both depend on protocol.py and not on each other; a duplicate "
        "plugin name is refused with a named error; plugins run in declared priority "
        "order with a stable tie-break by name; and a dry-run lists what would run "
        "without running it. Tests must cover the duplicate refusal, the ordering "
        "including a tie, and that a dry run has no side effect.")),
)

#: (task, arm) -> a fresh Engine configured for that run.
EngineSource = Callable[[Task, Arm], Engine]


@dataclass(frozen=True)
class Result:
    """What one (task, arm) run produced."""

    task: str
    arm: str
    steps: int = 0
    agents: int = 0
    files: int = 0
    gate_applicable: bool = False
    gate_passed: bool = False
    seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        """A run counts as a pass only on a green mechanical gate.

        No tests to run is **not** a pass: a build that produced nothing checkable
        is the failure mode this benchmark exists to catch.
        """
        return not self.error and self.gate_applicable and self.gate_passed


@dataclass(frozen=True)
class Report:
    results: tuple[Result, ...] = ()

    def by_arm(self) -> dict[str, tuple[int, int]]:
        """arm -> (passed, total), in the order the arms first appear."""
        out: dict[str, tuple[int, int]] = {}
        for r in self.results:
            passed, total = out.get(r.arm, (0, 0))
            out[r.arm] = (passed + (1 if r.ok else 0), total + 1)
        return out

    def table(self) -> str:
        """A markdown table of every run, then the per-arm tally."""
        head = ("| task | arm | gate | steps | files | secs | note |\n"
                "| --- | --- | --- | --- | --- | --- | --- |")
        rows = []
        for r in self.results:
            if r.error:
                gate = "error"
            elif not r.gate_applicable:
                gate = "n/a"
            else:
                gate = "pass" if r.gate_passed else "FAIL"
            rows.append(f"| {r.task} | {r.arm} | {gate} | {r.steps} | {r.files} | "
                        f"{r.seconds:.1f} | {r.error[:60]} |")
        tally = ["", "| arm | passed |", "| --- | --- |"]
        tally += [f"| {arm} | {passed}/{total} |"
                  for arm, (passed, total) in self.by_arm().items()]
        return "\n".join([head, *rows, *tally])

    def to_json(self) -> str:
        return json.dumps({"results": [asdict(r) for r in self.results]}, indent=2)


def default_engines(provider_name: str, base: str = _DEFAULT_BASE) -> EngineSource:
    """Build engines from a provider name, one blackboard and workspace per run.

    Runs are kept apart on disk (``<base>/<task>-<arm>/``) so a benchmark can be
    inspected afterwards, and so one run's files cannot anchor the next one.
    """
    provider = providers.get(provider_name)
    root = Path(base).expanduser()

    def make(task: Task, arm: Arm) -> Engine:
        run_dir = root / f"{task.id}-{arm.name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        bb = Blackboard(str(run_dir / "blackboard.db"), run_id=f"{task.id}-{arm.name}")
        engine = Engine(bb, provider.tpm_model(task.objective),
                        provider.model_factory(hard=task.hard))
        engine.workspace_base = str(run_dir)
        engine.features = set(arm.features)
        return engine

    return make


def run_task(task: Task, arm: Arm, engines: EngineSource) -> Result:
    """Run one task under one arm and judge it mechanically."""
    engine = engines(task, arm)
    started = time.monotonic()
    _log.info("BENCH start task=%s arm=%s features=%s", task.id, arm.name,
              sorted(arm.features))
    try:
        inp = default_input(task.objective, hard=task.hard)
        inp["target"] = {**inp["target"], "language": task.language}
        res = engine.build(inp)
        report = engine.gate()
    except Exception as exc:  # noqa: BLE001 - one task must not end the suite
        _log.exception("BENCH task=%s arm=%s failed", task.id, arm.name)
        return Result(task=task.id, arm=arm.name, error=f"{type(exc).__name__}: {exc}",
                      seconds=time.monotonic() - started)
    workspace = engine.workspace
    result = Result(
        task=task.id, arm=arm.name, steps=int(res.get("steps", 0)),
        agents=len(engine.bb.list_agents()),
        files=len(workspace.list_files()) if workspace else 0,
        gate_applicable=report.applicable, gate_passed=report.passed,
        seconds=time.monotonic() - started,
    )
    _log.info("BENCH done task=%s arm=%s ok=%s files=%d secs=%.1f",
              task.id, arm.name, result.ok, result.files, result.seconds)
    return result


def run_suite(tasks: Sequence[Task], arms: Sequence[Arm], engines: EngineSource) -> Report:
    """Every task against every arm, task-major so an interrupted run still
    compares arms on the tasks it reached."""
    results = [run_task(task, arm, engines) for task in tasks for arm in arms]
    return Report(tuple(results))


def load_tasks(path: str) -> tuple[Task, ...]:
    """Read tasks from a JSON list of {id, objective, language?, hard?}."""
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return tuple(Task(id=str(t["id"]), objective=str(t["objective"]),
                      language=str(t.get("language", "python")),
                      hard=bool(t.get("hard", False)))
                 for t in raw)


def select_arms(names: Sequence[str]) -> tuple[Arm, ...]:
    """The named arms, in the order given. Unknown names raise, rather than
    silently benchmarking something other than what was asked for."""
    known = {a.name: a for a in STANDARD_ARMS}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"unknown arm(s): {', '.join(unknown)}; "
                         f"choose from {', '.join(known)}")
    return tuple(known[n] for n in names)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pixibot.benchmark",
                                 description="Compare Pixibot's topology arms")
    ap.add_argument("--provider", choices=providers.NAMES, default="offline")
    ap.add_argument("--arms", default="default",
                    help="comma-separated: " + ", ".join(a.name for a in STANDARD_ARMS))
    ap.add_argument("--tasks", help="JSON file of tasks (overrides --set)")
    ap.add_argument("--set", choices=("hard", "smoke"), default="hard",
                    dest="task_set", help="built-in task set (default: hard)")
    ap.add_argument("--only", help="comma-separated task ids to run")
    ap.add_argument("--base", default=_DEFAULT_BASE, help="where runs are written")
    ap.add_argument("--out", help="write the JSON results here as well as the table")
    args = ap.parse_args(argv)
    # Send the run log to a file, so a warning (e.g. no docker daemon) lands there
    # instead of interleaving with the results table on stdout.
    logbook.setup()

    builtin = DEFAULT_TASKS if args.task_set == "hard" else SMOKE_TASKS
    tasks = load_tasks(args.tasks) if args.tasks else builtin
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        tasks = tuple(t for t in tasks if t.id in wanted)
    if not tasks:
        raise SystemExit("no tasks selected")
    arms = select_arms([a.strip() for a in args.arms.split(",")])

    report = run_suite(tasks, arms, default_engines(args.provider, args.base))
    print(report.table())
    if args.out:
        Path(args.out).expanduser().write_text(report.to_json(), encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":  # pragma: no cover
    main()
