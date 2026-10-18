"""Mechanical checkpoint gate (DESIGN.md §10, decisions 22/33).

Prefer mechanical truth over prompt adherence: before a checkpoint's demo reaches
the human, the project the agents built must build and pass its own tests.

The gate *derives* its checks from what is in the workspace, because the caller
does not know what the agents chose to write. Pixibot targets Python and C/C++
(DESIGN.md §6), so:

* a Python project with tests        -> ``pytest``, then a **negative control**
* a CMake project                    -> configure, build, ``ctest``
* C/C++ sources with a Makefile      -> ``make``, and ``make test`` if that target exists
* C/C++ sources with neither         -> a compile-only pass over every source
* a mix                              -> both languages' checks, Python first

The agents write their own tests, so a green suite on its own only says that
the team agrees with itself. The **negative control** answers the other half:
hide the implementation, run the suite again, and demand that it fails. A suite
that passes with no code under it does not test the code.

The control proves that *at least one* test depends on the implementation. It
cannot prove that every test does.

Checks run through a :class:`~pixibot.runtime.Executor`, so the gate runs in the
same sandbox the agents do.

Contract: docs/contracts/pixibot.gates.md
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .runtime import DEFAULT_TIMEOUT, Executor, executor_for

Check = tuple[str, str]  # (name shown to humans, shell command)

GATE_TIMEOUT = 600  # a build plus a test suite, not a single command

_PY_TEST = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py)$")
_C_SRC = (".c", ".cc", ".cpp", ".cxx")
_CPP_STD = "c++20"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    output: str


@dataclass(frozen=True)
class GateReport:
    """What the gate found. ``applicable`` is False when there was nothing to check."""

    language: str                       # "python" | "c/c++" | "python+c/c++" | "none"
    results: tuple[CheckResult, ...] = ()

    @property
    def applicable(self) -> bool:
        return bool(self.results)

    @property
    def passed(self) -> bool:
        """True when every check passed. Vacuously true with nothing to check —
        read ``applicable`` first if the difference matters."""
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    def summary(self, *, clip: int = 1500) -> str:
        """Failure text for a human or for a re-plan prompt (tail-clipped)."""
        if not self.failures:
            return "all checks passed"
        return "\n\n".join(f"[{r.name}]\n{r.output[-clip:]}" for r in self.failures)


#: Name of the check that proves the suite depends on the implementation.
NEGATIVE_CONTROL = "negative-control"
_HIDDEN = ".pixibot-hidden"      # suffix an implementation file wears while hidden
_LIST = ".pixibot-impl.txt"      # the files we moved, so the restore is exact
_LOG = ".pixibot-control.log"

#: Files a test suite is meant to exercise: Python, not tests, not packaging,
#: not under a dot-directory. The shell command below selects the same set.
_FIND_IMPL = (
    'find . -name "*.py" ! -name "test_*.py" ! -name "*_test.py" '
    '! -name "conftest.py" ! -name "setup.py" ! -path "./.*" ! -path "*/.*"'
)

# One shell command, so the control runs through the same executor as every other
# check - inside the container when there is one. It hides each implementation
# file, runs the suite, restores every file it moved, then inverts the verdict:
# a suite that still passes with no implementation under it has proven nothing.
# The restore runs before the verdict, so a red control never leaves a
# half-hidden workspace for the next check.
_CONTROL_COMMAND = " ".join((
    "set -u;",
    f'{_FIND_IMPL} > {_LIST};',
    f'if [ ! -s {_LIST} ]; then echo "no implementation file to hide";',
    f'rm -f {_LIST}; exit 1; fi;',
    f'while IFS= read -r f; do mv "$f" "$f{_HIDDEN}"; done < {_LIST};',
    f"python3 -m pytest -q > {_LOG} 2>&1; rc=$?;",
    f'while IFS= read -r f; do mv "$f{_HIDDEN}" "$f"; done < {_LIST};',
    f"n=$(wc -l < {_LIST}); rm -f {_LIST};",
    'if [ "$rc" -eq 0 ]; then',
    'echo "THE SUITE PASSES WITH THE IMPLEMENTATION REMOVED.";',
    'echo "It does not test the code. Write tests that import and exercise it.";',
    f"tail -20 {_LOG}; rm -f {_LOG}; exit 1; fi;",
    'echo "ok: the suite fails without the implementation ($n file(s) hidden)";',
    f"rm -f {_LOG}; exit 0",
))


def implementation_files(files: Sequence[str]) -> tuple[str, ...]:
    """The Python files a test suite is supposed to exercise.

    Tests, packaging files and anything under a dot-directory are not
    implementation: hiding them would prove nothing about the code.
    """
    return tuple(sorted(
        f for f in files
        if f.endswith(".py")
        and not _PY_TEST.search(f)
        and not f.startswith(".")
        and "/." not in f
        and PurePosixPath(f).name not in ("setup.py", "conftest.py")))


def _python_checks(files: Sequence[str]) -> list[Check]:
    if not any(_PY_TEST.search(f) for f in files):
        return []
    checks = [("pytest", "python3 -m pytest -q")]
    if implementation_files(files):
        # The team wrote the suite, so a green suite is only half the answer.
        checks.append((NEGATIVE_CONTROL, _CONTROL_COMMAND))
    return checks


def _cpp_checks(files: Sequence[str], read: Callable[[str], str | None]) -> list[Check]:
    sources = sorted(f for f in files if f.endswith(_C_SRC))
    if not sources and "CMakeLists.txt" not in files:
        return []
    if "CMakeLists.txt" in files:
        return [("cmake-configure", "cmake -S . -B build"),
                ("cmake-build", "cmake --build build"),
                ("ctest", "ctest --test-dir build --output-on-failure")]
    makefile = next((f for f in ("Makefile", "makefile", "GNUmakefile") if f in files), None)
    if makefile:
        checks = [("make", "make")]
        body = read(makefile) or ""
        # Only ask for a target the Makefile actually declares; `make test` on a
        # Makefile without one fails for a reason that is not the code's fault.
        if re.search(r"^(test|check):", body, re.M):
            checks.append(("make-test", "make test"))
        return checks
    # No build system: prove at least that every translation unit compiles.
    listed = " ".join(sources)
    return [("compile", f"g++ -std={_CPP_STD} -fsyntax-only {listed}")]


def checks_for(files: Sequence[str],
               read: Callable[[str], str | None] = lambda _: None) -> tuple[Check, ...]:
    """The checks a project made of `files` must pass (pure; nothing is run).

    `read(path) -> text | None` is consulted only for build files whose contents
    change which checks apply (a Makefile's targets).
    """
    return tuple(_python_checks(files) + _cpp_checks(files, read))


#: Which checks belong to which language. A name not listed here is C/C++,
#: and adding a Python check without adding it here would mislabel the project.
_PYTHON_CHECK_NAMES = frozenset({"pytest", NEGATIVE_CONTROL})


def language_of(checks: Sequence[Check]) -> str:
    names = {name for name, _ in checks}
    python = bool(names & _PYTHON_CHECK_NAMES)
    cpp = bool(names - _PYTHON_CHECK_NAMES)
    if python and cpp:
        return "python+c/c++"
    if python:
        return "python"
    if cpp:
        return "c/c++"
    return "none"


def run_check(name: str, command: str, executor: Executor,
              *, timeout: int = DEFAULT_TIMEOUT) -> CheckResult:
    """Run one check through `executor`."""
    res = executor.run(command, timeout=timeout)
    return CheckResult(name, res.ok, res.output)


def run_gate(root: str | Path, *, executor: Executor | None = None,
             timeout: int = GATE_TIMEOUT) -> GateReport:
    """Derive the checks for the project at `root`, run them, and report.

    Later checks still run after an earlier one fails: a failing build *and* a
    failing test suite is more useful feedback for a re-plan than the first
    failure alone.
    """
    base = Path(root)
    files = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
    checks = checks_for(files, lambda rel: _read_text(base / rel))
    if not checks:
        return GateReport(language="none")
    ex = executor or executor_for(str(base), sandbox="local")
    results = tuple(run_check(name, command, ex, timeout=timeout) for name, command in checks)
    return GateReport(language=language_of(checks), results=results)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
