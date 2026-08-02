"""Tests for deterministic dependency-chain activation (set_deps)."""

import os
import tempfile
import unittest

from pixibot.blackboard import KIND_ARTIFACT, Blackboard
from pixibot.context_manager import ContextManager


class CoordinationTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="coord")
        self.cm = ContextManager(self.bb, max_steps=30)

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def _recorder(self, order):
        def make(name):
            def run(bb, me, events):
                order.append(me)
                bb.send(me, f"{me} done", kind=KIND_ARTIFACT, section=f"{me}.txt")
            return run
        return make

    def test_chain_runs_all_in_order(self):
        order = []
        make = self._recorder(order)
        for n in ("a", "b", "c"):
            self.cm.register(n, make(n))
        self.cm.set_deps("a", set())
        self.cm.set_deps("b", {"a"})
        self.cm.set_deps("c", {"b"})
        self.bb.send("tpm", "go", to="a")   # kick the root only
        self.cm.run()
        self.assertEqual(order, ["a", "b", "c"])   # b and c woke via dependencies

    def test_agent_waits_for_all_deps(self):
        order = []
        make = self._recorder(order)
        for n in ("a", "b", "c"):
            self.cm.register(n, make(n))
        self.cm.set_deps("a", set())
        self.cm.set_deps("b", set())
        self.cm.set_deps("c", {"a", "b"})   # c needs BOTH
        self.bb.send("tpm", "go", to="a")
        self.bb.send("tpm", "go", to="b")
        self.cm.run()
        self.assertEqual(sorted(order), ["a", "b", "c"])
        self.assertEqual(order[-1], "c")    # c ran only after a and b finished


if __name__ == "__main__":
    unittest.main()
