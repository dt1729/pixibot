"""Workspace — the real project directory agents write to (DESIGN.md §11).

Path-safe file I/O plus command execution, so agents produce a runnable project
on disk (not just blackboard text). Two rules hold whatever an agent asks for:

* every path resolves inside the workspace root, or the call raises;
* every command is screened for attempts to leave the tree before it runs.

Where a command runs — cwd, environment, isolation — is the executor's business
(:mod:`pixibot.runtime`), which is why that is the one injected dependency.

Contract: docs/contracts/pixibot.workspace.md
"""

from __future__ import annotations

import contextlib
import re
import shutil
from pathlib import Path

from .runtime import Executor, LocalExecutor, confined_env

# Directories that are never part of the project the agents are building.
_IGNORE = ("__pycache__", ".pytest_cache", ".git", ".venv", "venv", ".ffcheck",
           "site-packages", "node_modules", ".cache", ".mypy_cache", ".ruff_cache", ".tox",
           "build")
# path-segment prefixes to ignore too (pytest's tmp fixture dir is pytest-of-<user>)
_IGNORE_PREFIXES = ("pytest-of-",)

# Commands matching any of these are refused: they try to leave the workspace
# (parent traversal, absolute paths into the user's home/system) or read the key
# file. This keeps agents focused on their own project and off the rest of the box.
_ESCAPE_PATTERNS = (
    re.compile(r"(^|[\s;&|(\"'/])\.\.(/|$|[\s;&|)\"'])"),      # .. traversal
    re.compile(r"/home/|/root\b|/etc/|/mnt/|/var/|/proc/"),     # absolute system paths
    re.compile(r"\bcd\s+/"),                                    # absolute cd
    re.compile(r"ant_key|\.bash_history|\.ssh\b|id_rsa"),       # secret-ish targets
)

_ESCAPE_REFUSAL = ("refused: this command tries to leave your workspace. You are "
                   "confined to your project directory — work only with files here "
                   "(relative paths). Do not read other projects or system files.")
_ESCAPE_CODE = 126  # shell convention: command found but not executable


def _is_ignored(rel: str) -> bool:
    return any(seg in _IGNORE or seg.startswith(_IGNORE_PREFIXES)
               for seg in Path(rel).parts)


class Workspace:
    """The project directory agents own, plus the only way they run commands in it.

    `executor` is the seam: a confined host subprocess by default, a container
    when the engine was given one (DESIGN.md §11). Construction stays simple
    because the default is the safe one.
    """

    def __init__(self, root: str, executor: Executor | None = None):
        self.path = Path(root).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.path = self.path.resolve()   # the containment check compares against this
        self.executor = executor or LocalExecutor(str(self.path),
                                                  env=confined_env(str(self.path)))

    @property
    def root(self) -> str:
        """The workspace root as a string — what logs, prompts and the CLI print."""
        return str(self.path)

    def clear(self) -> None:
        """Empty the workspace — called at the start of each build so stale files
        from a previous run can't contaminate a new task."""
        for child in self.path.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    child.unlink()

    # -- inspection ----------------------------------------------------------
    def snapshot(self) -> dict[str, float]:
        """path -> mtime for every file (for detecting what an agent changed)."""
        return {rel: self._safe(rel).stat().st_mtime for rel in self.list_files()}

    def changed_since(self, before: dict[str, float] | None) -> list[str]:
        """Files created or modified since a snapshot (list_files already drops
        caches/venvs/pytest tmp dirs)."""
        out = []
        for rel in self.list_files():
            mtime = self._safe(rel).stat().st_mtime
            if before is None or before.get(rel) != mtime:
                out.append(rel)
        return out

    def list_files(self) -> list[str]:
        return sorted(str(p.relative_to(self.path)) for p in self.path.rglob("*")
                      if p.is_file() and not _is_ignored(str(p.relative_to(self.path))))

    def isdir(self, path: str = "") -> bool:
        return self._safe(path).is_dir()

    def listdir(self, path: str = "") -> list[str] | None:
        """Entries in a directory (dirs get a trailing '/'), or None if not a dir."""
        target = self._safe(path)
        if not target.is_dir():
            return None
        return [p.name + ("/" if p.is_dir() else "") for p in sorted(target.iterdir())]

    # -- file i/o ------------------------------------------------------------
    def _safe(self, path: str) -> Path:
        """Resolve `path` inside the workspace, or raise. The one gate every
        file operation passes through.

        Resolution is full (``..`` collapsed, symlinks followed), so neither
        ``../../etc/passwd`` nor a symlink planted in the tree can name a file
        outside it.
        """
        full = (self.path / path).resolve()
        if full != self.path and self.path not in full.parents:
            raise ValueError(f"path escapes workspace: {path!r}")
        return full

    def write_file(self, path: str, content: str) -> str:
        full = self._safe(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return path

    def read_file(self, path: str) -> str | None:
        """The file's text, or None when it is absent, binary or unreadable."""
        full = self._safe(path)
        if not full.is_file():
            return None
        try:
            return full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None  # binary or unreadable-as-text; callers treat as "skip"

    # -- execution -----------------------------------------------------------
    @staticmethod
    def _escape_reason(command: str) -> str | None:
        return _ESCAPE_REFUSAL if any(p.search(command) for p in _ESCAPE_PATTERNS) else None

    def run(self, command: str, timeout: int = 120) -> tuple[int, str]:
        """Run a shell command in the workspace. Returns (rc, output).

        Commands that try to leave the tree (parent traversal, absolute system
        paths, key files) are refused here, before the executor sees them: that
        is workspace policy and holds whichever executor is in use.
        """
        reason = self._escape_reason(command)
        if reason is not None:
            return _ESCAPE_CODE, reason
        res = self.executor.run(command, timeout=timeout)
        return res.returncode, res.output
