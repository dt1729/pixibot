"""Tests for the Broker / spokesbot interaction layer."""

import os
import tempfile
import unittest

from pixibot.blackboard import KIND_ARTIFACT, KIND_DIRECTIVE, Blackboard
from pixibot.interaction import Broker, snapshot
from pixibot.model import MockModel, ModelResponse


class InteractionTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="ix")
        self.bb.register_agent("prog-f1", role="programmer", depth="senior",
                               scope="impl/f1", state="ACTIVE")
        self.bb.send("tpm", "implement f1", to="prog-f1")
        self.bb.send("prog-f1", "def f(): pass", kind=KIND_ARTIFACT, section="impl/f1")

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_snapshot_includes_identity_and_activity(self):
        snap = snapshot(self.bb, "prog-f1")
        self.assertIn("prog-f1", snap)
        self.assertIn("programmer", snap)
        self.assertIn("implement f1", snap)   # a received message
        self.assertIn("impl/f1", snap)         # produced artifact

    def test_talk_offline_shows_context(self):
        broker = Broker(self.bb)  # no factory -> offline
        out = broker.talk("prog-f1", "what are you doing?")
        self.assertIn("offline", out)
        self.assertIn("prog-f1", out)

    def test_talk_with_model_uses_spokesbot_and_keeps_history(self):
        model = MockModel([
            ModelResponse(text="I'm implementing feature f1.", stop_reason="end_turn"),
            ModelResponse(text="Because the plan asked for it.", stop_reason="end_turn"),
        ])
        broker = Broker(self.bb, lambda: model)
        self.assertEqual(broker.talk("prog-f1", "what are you doing?"),
                         "I'm implementing feature f1.")
        broker.talk("prog-f1", "why?")
        # history should carry both user turns + both assistant turns
        self.assertEqual(len(broker._history["prog-f1"]), 4)

    def test_tell_posts_nonblocking_directive(self):
        broker = Broker(self.bb)
        eid = broker.tell("prog-f1", "use a dataclass")
        self.assertIsInstance(eid, int)
        inbox = self.bb.poll_inbox("prog-f1")
        directives = [e for e in inbox if e.kind == KIND_DIRECTIVE]
        self.assertEqual(len(directives), 1)
        self.assertEqual(directives[0].payload, "use a dataclass")


if __name__ == "__main__":
    unittest.main()
