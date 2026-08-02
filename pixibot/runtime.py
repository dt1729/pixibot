"""Execution runtime (DESIGN.md §11): run commands in a Docker container per run,
with a local-subprocess fallback for environments without Docker (and for tests).

Work agents' bash/tests/builds run through an executor. In production that's a
per-run container; the fallback runs on the host. The command builder is a pure
function so it can be unit-tested without Docker installed.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class ExecResult:
    returncode: int
    output: str  # stdout + stderr combined


class LocalExecutor:
    """Runs commands directly on the host (fallback / tests)."""

    def __init__(self, workdir: str = "."):
        self.workdir = workdir

    def run(self, command: str, *, timeout: int = 120) -> ExecResult:
        p = subprocess.run(
            command, shell=True, cwd=self.workdir,
            capture_output=True, text=True, timeout=timeout,
        )
        return ExecResult(p.returncode, p.stdout + p.stderr)


def docker_command(image: str, workdir: str, command: str, *, name: str | None = None) -> list[str]:
    """Build the ``docker run`` argv (pure — no execution)."""
    argv = ["docker", "run", "--rm"]
    if name:
        argv += ["--name", name]
    argv += ["-v", f"{workdir}:/work", "-w", "/work", image, "bash", "-lc", command]
    return argv


class DockerExecutor:
    """Runs each command in an ephemeral container mounting the workdir."""

    def __init__(self, image: str, workdir: str = "."):
        self.image = image
        self.workdir = workdir

    def run(self, command: str, *, timeout: int = 600) -> ExecResult:
        argv = docker_command(self.image, self.workdir, command)
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return ExecResult(p.returncode, p.stdout + p.stderr)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def get_executor(workdir: str = ".", *, image: str = "pixibot-run:latest",
                 prefer_docker: bool = True):
    """Pick DockerExecutor when Docker is present and preferred, else LocalExecutor."""
    if prefer_docker and docker_available():
        return DockerExecutor(image, workdir)
    return LocalExecutor(workdir)
