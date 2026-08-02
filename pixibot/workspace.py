"""Workspace — the real project directory agents write to (DESIGN.md §11).

Path-safe file I/O plus command execution, so agents produce a runnable project
on disk (not just blackboard text). All paths are confined to the workspace root.
"""

from __future__ import annotations

import os
import subprocess


class Workspace:
    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))
        os.makedirs(self.root, exist_ok=True)

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
