# Contract: pixibot.gates

**Status:** accepted
**Depth:** senior

## Responsibility

Decide, mechanically, whether the project the agents built is green.

## Interface

```python
Check = tuple[str, str]        # (name shown to humans, shell command)
GATE_TIMEOUT = 600

@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    output: str

@dataclass(frozen=True)
class GateReport:
    language: str                       # "python" | "c/c++" | "python+c/c++" | "none"
    results: tuple[CheckResult, ...] = ()
    @property
    def applicable(self) -> bool        # there was something to check
    @property
    def passed(self) -> bool            # every check passed (vacuously true if none)
    @property
    def failures(self) -> tuple[CheckResult, ...]
    def summary(self, *, clip: int = 1500) -> str

def checks_for(files: Sequence[str], read: Callable[[str], str | None] = ...) -> tuple[Check, ...]
def language_of(checks: Sequence[Check]) -> str
def run_check(name: str, command: str, executor: Executor, *, timeout: int = ...) -> CheckResult
def run_gate(root: str | Path, *, executor: Executor | None = None, timeout: int = GATE_TIMEOUT) -> GateReport
```

- `checks_for` is **pure**: paths in, commands out, nothing executed. `read` is consulted
  only where a build file's contents change which checks apply (a Makefile's targets).
- The order of checks is deliberate: Python before C/C++, since `pytest` is the cheap
  check and its output is the most actionable.
- `run_gate` runs **every** derived check even after one fails. A failing build *and* a
  failing suite is better re-plan feedback than the first failure alone.
- `applicable` and `passed` are separate on purpose: a project with nothing to check is
  vacuously green, and a caller that cannot tell the difference would report success for
  a build that produced no tests.
- `summary()` returns only failure text, tail-clipped, because the head of a pytest run is
  boilerplate and the tail holds the assertion.
- Commands assume the workspace is the working directory; `run_gate` passes the executor
  that already guarantees that.

## Seam

The `Executor` parameter is the seam (see `pixibot.runtime`): the gate must run wherever
the agents ran, which is a container when one is available. It is injectable rather than
constructed so the engine can hand over the workspace's own executor, and so tests can
assert on the *commands* with a recording double instead of installing cmake.

Derivation is deliberately **not** behind a seam: one function reads the file list and
decides. Language support is a table to extend (adding Rust is one branch), not a plugin
point, because nothing outside this module should get to redefine what "green" means.

Rejected: letting the caller pass checks (the previous design). The caller — the engine —
does not know what the agents chose to write, so it had to guess with a filename
heuristic, and C/C++ projects were silently ungated.

## Dependencies

- **May import:** `pixibot.runtime`, stdlib (`pathlib`, `re`)
- **Must not import:** `pixibot.engine`, `pixibot.lib.agent`, `pixibot.cli` — a gate must not
  know who asked
- **Imported by:** `pixibot.engine`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `SandboxUnavailableError` | propagated from `executor_for` when a container was demanded | build the image or relax the sandbox mode |

A red check is not an error: it is a `CheckResult(passed=False)`. Reading an unreadable
build file is not an error either — `read` returns `None` and the check set falls back.

## Observability

- **No logging** in this module. The verdict is the return value, and its one consumer
  (`pixibot.engine`) logs it at **INFO** with the run id, language and pass/fail — the
  context an operator needs, which this module does not have.
- A failing check's output reaches the operator through `summary()`, in the shell and in
  the re-plan prompt, rather than through the log.
- Never logged here: the check output, which can contain the whole source file.

## Tests

- a Python project with `tests/test_x.py` derives exactly `pytest`
- a Python project with no test file derives nothing and is not applicable
- a CMake project derives configure, build and ctest, in that order
- a Makefile project derives `make`, and `make test` only when that target is declared
- bare C++ sources with no build system derive a compile-only check over the sources,
  excluding headers
- a mixed project derives both languages' checks, Python first
- every derived check runs even when an earlier one fails
- a real failing pytest suite reports red and names the failing test in `summary()`
- a report with no checks is vacuously `passed` but not `applicable`

## Deletion test

Deleting this module returns the decision to the engine, which had `_derive_checks()`
matching `"test" in basename` and a Python-only command — so a C/C++ build (half of
Pixibot's stated targets) passed the human checkpoint with nothing ever compiled, and the
gate could not run in the same sandbox as the agents. It earns its keep.
