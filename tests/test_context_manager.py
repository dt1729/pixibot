"""Unit tests for the context-manager scheduler (stdlib only, mock runners)."""

import unittest

from pixibot.blackboard import KIND_ARTIFACT
from pixibot.lib.context_manager import STATE_BLOCKED, STATE_DONE, ContextManager

from .support import WithBlackboard


class ContextManagerTest(WithBlackboard, unittest.TestCase):
    run_id = "cm"

    def setUp(self):
        super().setUp()
        self.cm = ContextManager(self.bb, max_steps=50)

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


class BarrierTest(WithBlackboard, unittest.TestCase):
    run_id = "bar"

    def _agent(self, name, order):
        def runner(bb, agent_id, events):
            order.append(agent_id)
            return STATE_DONE
        return runner

    def test_a_barrier_pauses_the_pass_and_the_rest_keep_their_events(self):
        order = []
        cm = ContextManager(self.bb, max_steps=10)
        for name in ("a", "b", "c"):
            cm.register(name, self._agent(name, order))
            self.bb.send("tpm", "go", to=name)

        cm.run(barrier=lambda agent_id: False)      # pause after the first one
        self.assertEqual(len(order), 1)
        self.assertTrue(cm.paused)

        cm.run()                                    # resume: the other two still run
        self.assertEqual(len(order), 3)
        self.assertFalse(cm.paused)

    def test_no_barrier_runs_to_quiescence(self):
        order = []
        cm = ContextManager(self.bb, max_steps=10)
        for name in ("a", "b"):
            cm.register(name, self._agent(name, order))
            self.bb.send("tpm", "go", to=name)
        cm.run()
        self.assertEqual(sorted(order), ["a", "b"])
        self.assertFalse(cm.paused)

    def test_a_barrier_that_allows_everything_does_not_pause(self):
        order = []
        cm = ContextManager(self.bb, max_steps=10)
        cm.register("a", self._agent("a", order))
        self.bb.send("tpm", "go", to="a")
        cm.run(barrier=lambda agent_id: True)
        self.assertEqual(order, ["a"])
        self.assertFalse(cm.paused)


if __name__ == "__main__":
    unittest.main()
