"""Shared test fixtures.

One temp-SQLite blackboard, created and removed the same way everywhere. The
same twelve-line setUp/tearDown pair had been copied into every test module that
needs a blackboard, each with its own spelling of the WAL cleanup — so a fix to
any of it reached one file out of twelve.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from pixibot.blackboard import Blackboard
from pixibot.workspace import Workspace

#: SQLite's write-ahead-log siblings, which a plain unlink of the db leaves behind.
DB_SUFFIXES = ("", "-wal", "-shm")


def new_blackboard(run_id: str = "test") -> tuple[str, Blackboard]:
    """A blackboard on a fresh temp file. Returns (path, blackboard).

    The caller is responsible for ``close()`` and :func:`remove_db` — usually via
    ``addCleanup``, or by inheriting :class:`WithBlackboard`.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path, Blackboard(path, run_id=run_id)


def remove_db(path: str) -> None:
    """Delete a blackboard file and its WAL siblings, if they are still there."""
    for suffix in DB_SUFFIXES:
        with contextlib.suppress(FileNotFoundError):
            Path(path + suffix).unlink()


class WithBlackboard:
    """Mixin giving a test case ``self.bb`` plus temp directories.

    Mixed in beside ``unittest.TestCase`` (not instead of it), so a test class
    keeps its own ``setUp`` when it needs one and calls ``super().setUp()``.
    """

    run_id = "test"

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        # `path` keeps the name the test modules already use for it
        self.path, self.bb = new_blackboard(self.run_id)
        self.addCleanup(remove_db, self.path)  # type: ignore[attr-defined]
        self.addCleanup(self.bb.close)  # type: ignore[attr-defined]

    def temp_dir(self) -> str:
        """A temp directory removed when the test finishes."""
        path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, path, True)  # type: ignore[attr-defined]
        return path

    def temp_workspace(self) -> Workspace:
        """A workspace in a temp directory, removed when the test finishes."""
        return Workspace(self.temp_dir())
