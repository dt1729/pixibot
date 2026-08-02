"""Mechanical checkpoint gate (DESIGN.md §10).

Prefer mechanical truth over prompt-adherence: at each checkpoint, run the
configured checks (linters / type-checks / tests / coverage) and require them all
green *before* the demo goes to the human. Checks run through the execution
runtime in production; here they shell out directly (the container path uses the
same command strings).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    passed: bool
    output: str


def run_check(name: str, command: str, cwd: str = ".") -> CheckResult:
    p = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True)
    return CheckResult(name, p.returncode == 0, p.stdout + p.stderr)


def checkpoint_gate(checks: list[tuple[str, str]], cwd: str = ".") -> tuple[bool, list[CheckResult]]:
    """Run all (name, command) checks. Returns (all_passed, results).

    ``all_passed`` must be True before a checkpoint's demo is shown to the user.
    """
    results = [run_check(name, command, cwd) for name, command in checks]
    return all(r.passed for r in results), results
