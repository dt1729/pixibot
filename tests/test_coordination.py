"""Tests for deterministic dependency-chain activation (set_deps)."""

import unittest

from pixibot.blackboard import KIND_ARTIFACT
from pixibot.lib.context_manager import (
    STATE_BLOCKED,
    STATE_BUDGET_EXHAUSTED,
    STATE_DONE,
    ContextManager,
)

from .support import WithBlackboard


class CoordinationTest(WithBlackboard, unittest.TestCase):
    run_id = "coord"

    def setUp(self):
        super().setUp()
        self.cm = ContextManager(self.bb, max_steps=30)

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


class TerminalStateCascadeTest(WithBlackboard, unittest.TestCase):
    """Incident 008: a dependent must run after its dependency crashes or runs
    out of budget, not only after it succeeds."""

    run_id = "term"

    def _chain(self, upstream_runner):
        ran = []
        cm = ContextManager(self.bb, max_steps=10)
        cm.register("up", upstream_runner)
        cm.register("down", lambda bb, aid, events: ran.append(aid) or STATE_DONE)
        cm.set_deps("down", {"up"})
        self.bb.send("tpm", "go", to="up")
        cm.run()
        return cm, ran

    def test_a_dependent_runs_after_its_dependency_succeeds(self):
        cm, ran = self._chain(lambda bb, aid, events: STATE_DONE)
        self.assertEqual(ran, ["down"])
        self.assertIn("up", cm.completed())

    def test_a_dependent_runs_after_its_dependency_exhausts_its_budget(self):
        cm, ran = self._chain(lambda bb, aid, events: STATE_BUDGET_EXHAUSTED)
        self.assertEqual(ran, ["down"], "a spent agent must still cascade")
        self.assertIn("up", cm.completed())

    def test_a_dependent_runs_after_its_dependency_crashes(self):
        def boom(bb, aid, events):
            raise RuntimeError("crashed")

        cm, ran = self._chain(boom)
        self.assertEqual(ran, ["down"], "one crash must not stall the chain")
        self.assertIn("up", cm.completed())
        self.assertEqual(self.bb.get_agent("up")["state"], STATE_BLOCKED)

    def test_an_agent_that_never_ran_is_not_finished(self):
        cm = ContextManager(self.bb, max_steps=5)
        cm.register("a", lambda bb, aid, events: STATE_DONE)
        self.assertEqual(cm.completed(), frozenset())


if __name__ == "__main__":
    unittest.main()
