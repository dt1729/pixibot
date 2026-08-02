"""Unit tests for the Pixibot blackboard (stdlib only)."""

import json
import os
import tempfile
import unittest

from pixibot.blackboard import (
    BROADCAST,
    KIND_ARTIFACT,
    Blackboard,
    ScopeError,
)


class BlackboardTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="t1")

    def tearDown(self):
        self.bb.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def test_addressed_delivery_and_cursor(self):
        self.bb.register_agent("prog")
        self.bb.send("tpm", "do work", to="prog")
        inbox = self.bb.poll_inbox("prog")
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0].payload, "do work")
        self.assertEqual(inbox[0].from_agent, "tpm")
        # cursor advanced -> a second poll returns nothing new
        self.assertEqual(self.bb.poll_inbox("prog"), [])

    def test_broadcast_delivered_to_others_but_not_self(self):
        self.bb.register_agent("tester")
        self.bb.register_agent("architect")
        self.bb.send("tester", "everyone: build is broken", to=BROADCAST)
        # a peer receives the broadcast
        self.assertEqual(len(self.bb.poll_inbox("architect")), 1)
        # the sender is NOT woken by its own broadcast (no self-wake loop)
        self.assertEqual(self.bb.poll_inbox("tester"), [])

    def test_reset_run_isolates_new_build(self):
        self.bb.register_agent("old_agent")
        self.bb.send("old_agent", "stale", to="tpm")
        self.bb.reset_run("b2")
        self.assertEqual(self.bb.run_id, "b2")
        self.assertEqual(self.bb.list_agents(), [])       # stale agents gone
        self.assertEqual(self.bb.history(), [])           # new run starts empty
        # a fresh agent in the new run works normally
        self.bb.register_agent("new_agent")
        self.bb.send("tpm", "go", to="new_agent")
        self.assertEqual(len(self.bb.poll_inbox("new_agent")), 1)

    def test_peek_does_not_advance(self):
        self.bb.register_agent("prog")
        self.bb.send("tpm", "hi", to="prog")
        self.assertEqual(len(self.bb.poll_inbox("prog", advance=False)), 1)
        self.assertEqual(len(self.bb.poll_inbox("prog", advance=False)), 1)  # still there
        self.assertEqual(len(self.bb.poll_inbox("prog")), 1)                 # now consumed
        self.assertEqual(len(self.bb.poll_inbox("prog")), 0)

    def test_broadcast_reaches_everyone(self):
        self.bb.register_agent("a")
        self.bb.register_agent("b")
        self.bb.send("tpm", "all hands", to=BROADCAST)
        self.assertEqual(len(self.bb.poll_inbox("a")), 1)
        self.assertEqual(len(self.bb.poll_inbox("b")), 1)

    def test_current_state_latest_wins(self):
        self.bb.register_agent("prog", scope="impl")
        self.bb.send("prog", "v1", kind=KIND_ARTIFACT, section="impl/f1")
        self.bb.send("prog", "v2", kind=KIND_ARTIFACT, section="impl/f1")
        state = self.bb.current_state()
        self.assertEqual(state["impl/f1"].payload, "v2")
        self.assertEqual(self.bb.read_section("impl/f1").payload, "v2")

    def test_scope_enforced(self):
        self.bb.register_agent("prog", scope="impl/f1")
        with self.assertRaises(ScopeError):
            self.bb.send("prog", "x", kind=KIND_ARTIFACT, section="topology")
        # a write inside scope succeeds
        eid = self.bb.send("prog", "ok", kind=KIND_ARTIFACT, section="impl/f1")
        self.assertIsInstance(eid, int)

    def test_scopeless_agent_may_write_anywhere(self):
        self.bb.register_agent("tpm")  # no scope
        self.bb.send("tpm", "topo", kind=KIND_ARTIFACT, section="topology")
        self.assertEqual(self.bb.read_section("topology").payload, "topo")

    def test_in_reply_to_builds_the_dag(self):
        self.bb.register_agent("a")
        self.bb.register_agent("b")
        q = self.bb.send("a", "question", to="b")
        self.bb.send("b", "answer", to="a", in_reply_to=q)
        hist = self.bb.history()
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[1].in_reply_to, q)

    def test_json_payload_roundtrip(self):
        self.bb.register_agent("a")
        self.bb.send("tpm", {"k": 1, "list": [1, 2]}, to="a")
        event = self.bb.poll_inbox("a")[0]
        self.assertEqual(json.loads(event.payload), {"k": 1, "list": [1, 2]})

    def test_run_isolation(self):
        self.bb.register_agent("a")
        self.bb.send("tpm", "run1", to="a")
        other = Blackboard(self.path, run_id="t2")
        try:
            other.register_agent("a")
            # 'a' in run t2 must not see t1's message
            self.assertEqual(other.poll_inbox("a"), [])
        finally:
            other.close()

    def test_history_filters(self):
        self.bb.register_agent("a")
        self.bb.register_agent("b")
        self.bb.send("a", "m1", to="b")
        self.bb.send("b", "m2", to="a")
        self.assertEqual(len(self.bb.history(from_agent="a")), 1)
        self.assertEqual(len(self.bb.history(to_agent="a")), 1)


if __name__ == "__main__":
    unittest.main()
