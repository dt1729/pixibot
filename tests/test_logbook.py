"""Tests for the file logger - exercised through its public interface only."""

import logging
import shutil
import tempfile
import unittest
from pathlib import Path

from pixibot.lib import logbook


class LogbookTest(unittest.TestCase):
    def setUp(self):
        # snapshot + restore the shared logger so this test cannot leak handlers
        self.logger = logging.getLogger("pixibot")
        self._saved = self.logger.handlers[:]
        self.addCleanup(self._restore)
        self.logger.handlers.clear()

    def _restore(self):
        for h in self.logger.handlers:
            h.close()
        self.logger.handlers[:] = self._saved

    def test_setup_writes_and_is_idempotent(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = str(Path(d) / "run.log")

        self.assertEqual(logbook.setup(path), path)
        n = len(self.logger.handlers)
        self.assertEqual(logbook.setup(path), path)  # second call adds no handler
        self.assertEqual(len(self.logger.handlers), n)

        logbook.get("test").info("MARKER-123")
        for h in self.logger.handlers:
            h.flush()
        self.assertIn("MARKER-123", Path(path).read_text(encoding="utf-8"))

    def test_get_returns_child_loggers_under_the_package(self):
        self.assertEqual(logbook.get("agent").name, "pixibot.agent")
        self.assertEqual(logbook.get().name, "pixibot")


if __name__ == "__main__":
    unittest.main()
