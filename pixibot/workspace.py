"""Workspace — the real project directory agents write to (DESIGN.md §11).

Path-safe file I/O plus command execution, so agents produce a runnable project
on disk (not just blackboard text). All paths are confined to the workspace root.
"""

from __future__ import annotations

import os
import shutil
import subprocess

_IGNORE = ("__pycache__", ".pytest_cache", ".git")


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
        """Files created or modified since a snapshot (ignoring caches)."""
        out = []
        for p in self.list_files():
            if any(seg in p.split(os.sep) for seg in _IGNORE):
                continue
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
        for dirpath, _, filenames in os.walk(self.root):
            for name in filenames:
                out.append(os.path.relpath(os.path.join(dirpath, name), self.root))
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

    def run(self, command: str, timeout: int = 120):
        """Run a shell command in the workspace. Returns (returncode, output)."""
        p = subprocess.run(command, shell=True, cwd=self.root,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
