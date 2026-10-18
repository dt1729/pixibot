"""Tests for dependency-driven activation (handshakes) in the context-manager."""

import unittest

from pixibot.blackboard import KIND_ARTIFACT
from pixibot.lib.context_manager import ContextManager

from .support import WithBlackboard


class MultiAgentTest(WithBlackboard, unittest.TestCase):
    run_id = "ma"

    def setUp(self):
        super().setUp()
        self.cm = ContextManager(self.bb, max_steps=30)

    def test_write_to_section_activates_reader(self):
        def producer(bb, me, ev):
            bb.send(me, "topology-decided", kind=KIND_ARTIFACT, section="topology")

        def consumer(bb, me, ev):
            bb.send(me, "code-written", kind=KIND_ARTIFACT, section="impl/f1")

        self.cm.register("topo", producer, scope="topology")
        self.cm.register("prog", consumer, scope="impl/f1")
        self.cm.add_dependency("topology", "prog")   # prog reads topology

        self.bb.send("tpm", "go", to="topo")         # kick only the producer
        self.cm.run()

        self.assertEqual(self.bb.read_section("topology").payload, "topology-decided")
        # consumer was activated purely by the dependency handshake:
        self.assertEqual(self.bb.read_section("impl/f1").payload, "code-written")

    def test_three_stage_cascade(self):
        def writer(section, content):
            def run(bb, me, ev):
                bb.send(me, content, kind=KIND_ARTIFACT, section=section)
            return run

        self.cm.register("a", writer("topology", "T"), scope="topology")
        self.cm.register("b", writer("impl/f1", "I"), scope="impl/f1")
        self.cm.register("c", writer("tests/f1", "X"), scope="tests/f1")
        self.cm.add_dependency("topology", "b")
        self.cm.add_dependency("impl/f1", "c")

        self.bb.send("tpm", "go", to="a")
        self.cm.run()

        self.assertEqual(self.bb.read_section("tests/f1").payload, "X")  # reached the end


if __name__ == "__main__":
    unittest.main()
