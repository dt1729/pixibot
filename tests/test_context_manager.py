"""Unit tests for the context-manager scheduler (stdlib only, mock runners)."""

import json
import os
import tempfile
import unittest

from pixibot.blackboard import KIND_ARTIFACT, Blackboard
from pixibot.context_manager import STATE_BLOCKED, STATE_DONE, ContextManager


class ContextManagerTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="cm")
        self.cm = ContextManager(self.bb, max_steps=50)

    def tearDown(self):
        self.bb.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_single_agent_activates_and_writes(self):
        def runner(bb, me, events):
            bb.send(me, "hello world", kind=KIND_ARTIFACT, section="impl/f1")

        self.cm.register("prog", runner, scope="impl")
        self.bb.send("user", "build it", to="prog")  # the kick
        steps = self.cm.run()

        self.assertGreaterEqual(steps, 1)
        self.assertEqual(self.bb.read_section("impl/f1").payload, "hello world")
        self.assertEqual(self.bb.get_agent("prog")["state"], STATE_DONE)

    def test_bounded_ping_pong_converges(self):
        # A and B bat a counter back and forth, stopping at 4 hops.
        def make(peer):
            def runner(bb, me, events):
                for e in events:
                    n = int(e.payload)
                    if n < 4:
                        bb.send(me, str(n + 1), to=peer)
            return runner

        self.cm.register("a", make("b"))
        self.cm.register("b", make("a"))
        self.bb.send("user", "0", to="a")  # kick

        steps = self.cm.run()
        self.assertLess(steps, self.cm.max_steps)  # converged, not budget-capped
        # 1 kick + hops 1,2,3,4 = 5 messages total
        self.assertEqual(len(self.bb.history()), 5)

    def test_infinite_interaction_hits_step_budget(self):
        # Two agents bat a message back and forth forever (an agent can no longer
        # wake itself, so the runaway loop is modelled with a mutual ping-pong).
        def make(peer):
            def runner(bb, me, events):
                bb.send(me, "again", to=peer)  # never stops
            return runner

        self.cm.register("ping", make("pong"))
        self.cm.register("pong", make("ping"))
        self.bb.send("user", "go", to="ping")
        steps = self.cm.run()
        self.assertEqual(steps, self.cm.max_steps)  # budget stopped the loop, no hang

    def test_runner_can_report_blocked(self):
        def runner(bb, me, events):
            return STATE_BLOCKED

        self.cm.register("waiter", runner)
        self.bb.send("user", "ping", to="waiter")
        self.cm.run()
        self.assertEqual(self.bb.get_agent("waiter")["state"], STATE_BLOCKED)

    def test_quiescent_with_no_mail(self):
        self.cm.register("idle", lambda bb, me, ev: None)
        self.assertEqual(self.cm.run(), 0)  # nobody kicked -> zero steps


if __name__ == "__main__":
    unittest.main()
