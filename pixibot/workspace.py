"""Workspace — the real project directory agents write to (DESIGN.md §11).

Path-safe file I/O plus command execution, so agents produce a runnable project
on disk (not just blackboard text). All paths are confined to the workspace root.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

_IGNORE = ("__pycache__", ".pytest_cache", ".git", ".venv", "venv", ".ffcheck",
           "site-packages", "node_modules", ".cache", ".mypy_cache", ".ruff_cache", ".tox")
# path-segment prefixes to ignore too (pytest's tmp fixture dir is pytest-of-<user>)
_IGNORE_PREFIXES = ("pytest-of-",)


def _is_ignored(path: str) -> bool:
    for seg in path.split(os.sep):
        if seg in _IGNORE or seg.startswith(_IGNORE_PREFIXES):
            return True
    return False

# Any env var whose name contains one of these is stripped from the child shell,
# so a wandering agent can never read an API key/token out of the environment.
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
                 "ANTHROPIC", "OPENAI", "GEMINI", "OPENROUTER", "GROQ", "AWS")

# Commands matching any of these are refused: they try to leave the workspace
# (parent traversal, absolute paths into the user's home/system) or read the key
# file. This keeps agents focused on their own project and off the rest of the box.
_ESCAPE_PATTERNS = (
    re.compile(r"(^|[\s;&|(\"'/])\.\.(/|$|[\s;&|)\"'])"),  # .. traversal
    re.compile(r"/home/|/root\b|/etc/|/mnt/|/var/|/proc/"),  # absolute system paths
    re.compile(r"\bcd\s+/"),                                  # absolute cd
    re.compile(r"ant_key|\.bash_history|\.ssh\b|id_rsa"),     # secret-ish targets
)


class Workspace:
    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))
        os.makedirs(self.root, exist_ok=True)

    def clear(self) -> None:
        """Empty the workspace — called at the start of each build so stale files
        from a previous run can't contaminate a new task."""
        for name in os.listdir(self.root):
            p = os.path.join(self.root, name)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def snapshot(self) -> dict:
        """path -> mtime for every file (for detecting what an agent changed)."""
        return {p: os.path.getmtime(self._safe(p)) for p in self.list_files()}

    def changed_since(self, before: dict) -> list[str]:
        """Files created or modified since a snapshot (list_files already drops
        caches/venvs/pytest tmp dirs)."""
        out = []
        for p in self.list_files():
            mt = os.path.getmtime(self._safe(p))
            if before is None or before.get(p) != mt:
                out.append(p)
        return out

    def _safe(self, path: str) -> str:
        full = os.path.abspath(os.path.join(self.root, path))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ValueError(f"path escapes workspace: {path!r}")
        return full

    def write_file(self, path: str, content: str) -> str:
        full = self._safe(path)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def read_file(self, path: str):
        full = self._safe(path)
        if not os.path.isfile(full):
            return None
        with open(full, encoding="utf-8") as f:
            return f.read()

    def list_files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            # prune ignored dirs so we don't descend into venvs/caches at all
            dirnames[:] = [d for d in dirnames
                           if d not in _IGNORE and not d.startswith(_IGNORE_PREFIXES)]
            for name in filenames:
                rel = os.path.relpath(os.path.join(dirpath, name), self.root)
                if not _is_ignored(rel):
                    out.append(rel)
        return sorted(out)

    def isdir(self, path: str = "") -> bool:
        return os.path.isdir(self._safe(path))

    def listdir(self, path: str = ""):
        """Entries in a directory (dirs get a trailing '/'), or None if not a dir."""
        full = self._safe(path)
        if not os.path.isdir(full):
            return None
        entries = []
        for name in sorted(os.listdir(full)):
            entries.append(name + ("/" if os.path.isdir(os.path.join(full, name)) else ""))
        return entries

    def _clean_env(self) -> dict:
        """Child environment with secrets removed and HOME/TMP pinned to the
        workspace, so `~`/`$HOME`/temp all resolve inside the project."""
        env = {k: v for k, v in os.environ.items()
               if not any(h in k.upper() for h in _SECRET_HINTS)}
        env["HOME"] = self.root
        env["TMPDIR"] = self.root
        env["PWD"] = self.root
        return env

    @staticmethod
    def _escape_reason(command: str):
        for pat in _ESCAPE_PATTERNS:
            if pat.search(command):
                return ("refused: this command tries to leave your workspace. You are "
                        "confined to your project directory — work only with files here "
                        "(relative paths). Do not read other projects or system files.")
        return None

    def run(self, command: str, timeout: int = 120):
        """Run a shell command confined to the workspace. Returns (rc, output).

        The command runs with cwd + HOME + TMPDIR pinned to the workspace root and
        with all secret-bearing env vars stripped; commands that try to escape the
        tree (parent traversal, absolute system paths, key files) are refused."""
        reason = self._escape_reason(command)
        if reason is not None:
            return 126, reason
        p = subprocess.run(command, shell=True, cwd=self.root, env=self._clean_env(),
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
