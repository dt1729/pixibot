"""Tests for the Observer's DAG reconstruction and report."""

import os
import tempfile
import unittest

from pixibot.blackboard import Blackboard
from pixibot.observer import dag_edges, report


class ObserverTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="obs")

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_dag_captures_messages_and_replies(self):
        self.bb.register_agent("a")
        self.bb.register_agent("b")
        q = self.bb.send("a", "question", to="b")
        self.bb.send("b", "answer", to="a", in_reply_to=q)
        self.bb.send("b", "code", kind="artifact", section="impl/f1")

        pairs = [(s, d) for s, d, _ in dag_edges(self.bb.history())]
        self.assertIn(("a", "b"), pairs)   # the message
        self.assertIn(("b", "a"), pairs)   # the reply edge

    def test_report_lists_artifacts_and_dag(self):
        self.bb.register_agent("prog", scope="impl")
        self.bb.send("prog", "x", kind="artifact", section="impl/f1")
        rep = report(self.bb)
        self.assertIn("Message DAG", rep)
        self.assertIn("impl/f1", rep)
        self.assertIn("Activity by agent", rep)


if __name__ == "__main__":
    unittest.main()
