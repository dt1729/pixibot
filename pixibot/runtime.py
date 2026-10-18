"""Execution runtime (DESIGN.md §11, decision 24): where a command actually runs.

Every command Pixibot runs on behalf of an agent — the agent's own ``run_bash``
and the mechanical gate's checks — goes through an :class:`Executor`. Two exist:
a container per command (the designed sandbox) and a confined host subprocess
(the fallback, and what tests use).

Confinement is built here rather than at the call sites, so both paths get it:
the child sees ``HOME``/``TMPDIR``/cwd inside the workspace and no
secret-bearing variable from the parent environment. The container adds the
kernel-level half the host path cannot give.

Contract: docs/contracts/pixibot.runtime.md
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .lib.logbook import get as _get_logger

_log = _get_logger("runtime")

DEFAULT_IMAGE = "pixibot-run:latest"
DEFAULT_TIMEOUT = 120
# Any variable whose name contains one of these is withheld from the child, so a
# wandering agent can never read an API key or token out of the environment.
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
                "ANTHROPIC", "OPENAI", "GEMINI", "OPENROUTER", "GROQ", "AWS")


class SandboxUnavailableError(RuntimeError):
    """Raised when container execution is required but Docker is not usable."""


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    output: str  # stdout + stderr combined, in the order the child wrote them

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@runtime_checkable
class Executor(Protocol):
    """Runs a shell command somewhere, and says what came back."""

    workdir: str

    def run(self, command: str, *, timeout: int = DEFAULT_TIMEOUT) -> ExecResult: ...


def confined_env(root: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The parent environment minus secrets, with HOME/TMPDIR/PWD pinned to `root`.

    So ``~``, ``$HOME`` and anything writing to a temp dir all resolve inside the
    project the agent is allowed to touch.
    """
    source = os.environ if environ is None else environ
    env = {k: v for k, v in source.items()
           if not any(hint in k.upper() for hint in SECRET_HINTS)}
    env["HOME"] = root
    env["TMPDIR"] = root
    env["PWD"] = root
    return env


class LocalExecutor:
    """Runs commands in a confined host subprocess (fallback, and tests).

    Confinement here is cwd + environment only: a determined command can still
    reach the filesystem. Use :class:`DockerExecutor` when that matters.
    """

    def __init__(self, workdir: str = ".", *, env: Mapping[str, str] | None = None):
        self.workdir = str(Path(workdir).expanduser())
        self._env = dict(env) if env is not None else None

    def run(self, command: str, *, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        try:
            p = subprocess.run(
                command, shell=True, cwd=self.workdir, env=self._env,
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            _log.warning("local timeout after %ss: %s", timeout, command[:120])
            return ExecResult(124, f"timed out after {timeout}s")
        return ExecResult(p.returncode, p.stdout + p.stderr)


def docker_command(image: str, workdir: str, command: str, *,
                   name: str | None = None) -> list[str]:
    """Build the ``docker run`` argv (pure — nothing is executed).

    The workspace is the only thing mounted, no host environment is forwarded,
    and the container is removed on exit.
    """
    argv = ["docker", "run", "--rm", "--network", "none"]
    if name:
        argv += ["--name", name]
    argv += ["-v", f"{workdir}:/work", "-w", "/work",
             "-e", "HOME=/work", "-e", "TMPDIR=/work",
             image, "bash", "-lc", command]
    return argv


class DockerExecutor:
    """Runs each command in an ephemeral container mounting only the workspace."""

    def __init__(self, image: str = DEFAULT_IMAGE, workdir: str = ".",
                 *, network: bool = False):
        self.image = image
        self.workdir = str(Path(workdir).expanduser())
        self.network = network

    def run(self, command: str, *, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        argv = docker_command(self.image, self.workdir, command)
        if self.network:
            argv.remove("--network")
            argv.remove("none")
        try:
            p = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            _log.warning("container timeout after %ss: %s", timeout, command[:120])
            return ExecResult(124, f"timed out after {timeout}s")
        return ExecResult(p.returncode, p.stdout + p.stderr)


def docker_available() -> bool:
    """True when a docker CLI is on PATH and its daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        p = subprocess.run(["docker", "info"], capture_output=True,
                           text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


def executor_for(workdir: str = ".", *, sandbox: str = "auto",
                 image: str = DEFAULT_IMAGE) -> Executor:
    """Pick an executor for `workdir`.

    `sandbox` is ``"docker"`` (container, or raise), ``"local"`` (confined host
    subprocess) or ``"auto"`` (container when Docker answers, else local with a
    warning). "auto" is the default because a missing daemon should cost
    isolation, not the run.
    """
    if sandbox not in ("auto", "docker", "local"):
        raise ValueError(f"unknown sandbox mode {sandbox!r}; use auto, docker or local")
    if sandbox == "local":
        return LocalExecutor(workdir, env=confined_env(str(Path(workdir).expanduser())))
    if docker_available():
        return DockerExecutor(image, workdir)
    if sandbox == "docker":
        raise SandboxUnavailableError(
            "docker execution was requested but no working docker daemon was found; "
            "build the image with: docker build -t pixibot-run:latest docker/")
    _log.warning("no docker daemon: falling back to a confined host subprocess")
    return LocalExecutor(workdir, env=confined_env(str(Path(workdir).expanduser())))
