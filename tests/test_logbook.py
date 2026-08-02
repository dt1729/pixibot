"""Tests for the file logger."""

import logging
import os
import shutil
import tempfile
import unittest

from pixibot import logbook


class LogbookTest(unittest.TestCase):
    def setUp(self):
        # snapshot + restore global logger state so this test can't leak handlers
        self.logger = logging.getLogger("pixibot")
        self._saved = self.logger.handlers[:]
        self._flag = logbook._CONFIGURED
        self.addCleanup(self._restore)
        self.logger.handlers.clear()
        logbook._CONFIGURED = False

    def _restore(self):
        self.logger.handlers[:] = self._saved
        logbook._CONFIGURED = self._flag

    def test_setup_writes_and_is_idempotent(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "run.log")

        self.assertEqual(logbook.setup(path), path)
        n = len(self.logger.handlers)
        logbook.setup(path)  # second call must not add another handler
        self.assertEqual(len(self.logger.handlers), n)

        logbook.get("test").info("MARKER-123")
        for h in self.logger.handlers:
            h.flush()
        with open(path, encoding="utf-8") as f:
            self.assertIn("MARKER-123", f.read())


if __name__ == "__main__":
    unittest.main()
