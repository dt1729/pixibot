# Contract: pixibot.benchmark

**Status:** accepted
**Depth:** senior

## Responsibility

Run a task list against a set of topology arms and report what each produced.

## Interface

```python
@dataclass(frozen=True)
class Task:  id: str; objective: str; language: str = "python"; hard: bool = False
@dataclass(frozen=True)
class Arm:   name: str; features: frozenset[str] = frozenset()
@dataclass(frozen=True)
class Result:
    task: str; arm: str; steps: int; agents: int; files: int
    gate_applicable: bool; gate_passed: bool; seconds: float; error: str = ""
    @property
    def ok(self) -> bool            # green mechanical gate, nothing else
@dataclass(frozen=True)
class Report:
    results: tuple[Result, ...]
    def by_arm(self) -> dict[str, tuple[int, int]]      # arm -> (passed, total)
    def table(self) -> str                              # markdown
    def to_json(self) -> str

STANDARD_ARMS: tuple[Arm, ...]      # bare, default, auto-gate, both (a 2x2 ablation)
DEFAULT_TASKS: tuple[Task, ...]
EngineSource = Callable[[Task, Arm], Engine]

def default_engines(provider_name: str, base: str = DEFAULT_BASE) -> EngineSource
def run_task(task: Task, arm: Arm, engines: EngineSource) -> Result
def run_suite(tasks: Sequence[Task], arms: Sequence[Arm], engines: EngineSource) -> Report
def load_tasks(path: str) -> tuple[Task, ...]
def select_arms(names: Sequence[str]) -> tuple[Arm, ...]
def main(argv: list[str] | None = None) -> None
```

- **A pass is a green mechanical gate and nothing else.** `gate_applicable=False`
  ("nothing to check") is explicitly *not* a pass: a build that produced no runnable
  tests is the failure this harness exists to measure.
- `run_task` **never raises**. A build that fails for any reason becomes a `Result` with
  `error` set and the elapsed time recorded, because losing a whole suite to one timeout
  would destroy the comparison the run was for.
- `run_suite` is **task-major** (every arm of task 1, then task 2): an interrupted run
  still compares arms fairly on the tasks it reached.
- Each (task, arm) gets its own blackboard and workspace under
  `<base>/<task>-<arm>/`, so runs are inspectable afterwards and no run's leftovers can
  anchor the next one (decision 41).
- `Arm.features` is applied to `Engine.features` wholesale, so an arm's name in a report
  always corresponds to exactly that feature set.
- `select_arms` raises on an unknown name rather than skipping it: silently benchmarking
  three arms when four were asked for would corrupt the conclusion.
- Timings are wall-clock seconds and include model latency; they compare arms within one
  run, not across machines or days.

## Seam

`EngineSource` is the seam, and what varies is where the engines come from:
`default_engines(provider)` for a real measurement, the offline provider for a smoke run,
and a fake in tests (which is how the suite logic is tested without a model at all).

Judging is deliberately **not** behind a seam: the verdict is `pixibot.gates`, the same
one the human checkpoint uses, so a benchmark score and a shipped build mean the same
thing. A pluggable scorer would let the benchmark grade itself more kindly than the gate.

Rejected: counting files or steps as success. Both are easy to inflate and neither says
the program works, so they are reported as context rather than as the verdict.

## Dependencies

- **May import:** `pixibot.providers`, `pixibot.engine`, `pixibot.blackboard`,
  `pixibot.logbook`, stdlib
- **Must not import:** `pixibot.cli`, `pixibot.tui` — a benchmark has no front end
- **Imported by:** nothing; it is an entry point (`python -m pixibot.benchmark`)

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `ValueError` | `select_arms` is given an unknown arm name | fix `--arms`; the message names the valid ones |
| `SystemExit` | `main` selects zero tasks | fix `--only` or `--tasks` |
| `OSError` / `JSONDecodeError` | `load_tasks` cannot read or parse the file | fix the file; failing early beats benchmarking nothing |

A failing build is not an error here: it is a `Result` with `error` set, which is the
measurement.

## Observability

- **INFO** `BENCH start` — task id, arm name and feature set, so a long suite can be
  followed in the run log.
- **INFO** `BENCH done` — task, arm, pass/fail, file count, elapsed seconds.
- **ERROR** (via `log.exception`) when a build raises, naming the task and arm; the same
  message also lands in the `Result`.
- `main` calls `logbook.setup()` so these reach the run-log file and never interleave with
  the results table on stdout.
- Never logged: the objective text beyond the task id, or any generated file content.

## Tests

- a green gate is a pass; a red gate, an error and "nothing to check" are not
- an arm's features reach the engine it runs on
- a task's language reaches the strict input
- a build that raises becomes a failed result carrying the exception name
- a failing task does not stop the rest of the suite
- every task runs against every arm, task-major, in order
- `by_arm` tallies passes per arm; `table` names every run and the tally
- the report round-trips through JSON, and an empty report still renders
- an unknown arm name raises; the standard arms are four distinct feature sets
- tasks load from a JSON file, defaulting language and hard
- offline end to end: a real engine, isolated directories, and a not-applicable verdict
  for the canned mock output

## Deletion test

Deleting this module leaves decision 47's arms unmeasured — the reason they were built as
toggles. Any comparison would go back to editing `Engine.features`, running the shell, and
eyeballing whether tests passed, with no record of which arm produced what. It earns its
keep.
