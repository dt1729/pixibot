"""Tests for the ChatSession command router (fakes; no input loop, no models)."""

import os
import tempfile
import unittest

from pixibot.blackboard import KIND_ARTIFACT, Blackboard
from pixibot.cli import ChatSession


class FakeBroker:
    def __init__(self):
        self.calls = []

    def talk(self, agent, message, on_delta=None):
        self.calls.append(("talk", agent, message))
        if on_delta:
            on_delta(f"[{agent}] {message}")
        return f"[{agent}] {message}"

    def tell(self, agent, text):
        self.calls.append(("tell", agent, text))
        return 7


class FakeEngine:
    def __init__(self):
        self.calls = []

    def build_objective(self, objective, *, hard=False):
        self.calls.append(("build", objective, hard))
        return {"projection": {"agents": [{"id": "a"}]}, "steps": 1}

    def tell(self, agent, text):
        self.calls.append(("tell", agent, text))
        return 2

    def revise(self, feedback):
        self.calls.append(("revise", feedback))
        return 3


class CliTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="cli")
        self.broker = FakeBroker()
        self.engine = FakeEngine()
        self.session = ChatSession(self.bb, self.broker, lambda hard, obj: self.engine)

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_plain_text_routes_to_default_tpm(self):
        self.session.handle("hello")
        self.assertEqual(self.broker.calls[-1], ("talk", "tpm", "hello"))

    def test_at_agent_talks(self):
        self.session.handle("@prog-f1 why a dict?")
        self.assertEqual(self.broker.calls[-1], ("talk", "prog-f1", "why a dict?"))

    def test_form(self):
        self.assertIn("Build Request", self.session.handle("/form"))

    def test_hard_toggle(self):
        self.assertIn("on", self.session.handle("/hard on"))
        self.assertTrue(self.session.hard)
        self.assertIn("off", self.session.handle("/hard off"))
        self.assertFalse(self.session.hard)

    def test_build_creates_engine_and_builds(self):
        out = self.session.handle("/build a url shortener")
        self.assertIs(self.session.engine, self.engine)
        self.assertEqual(self.engine.calls[-1], ("build", "a url shortener", False))
        self.assertIn("Built", out)

    def test_hard_flows_into_build(self):
        self.session.handle("/hard on")
        self.session.handle("/build hard thing")
        self.assertEqual(self.engine.calls[-1], ("build", "hard thing", True))

    def test_tell_uses_engine_after_build(self):
        self.session.handle("/build x")
        self.session.handle("/tell prog-f1 use a dataclass")
        self.assertEqual(self.engine.calls[-1], ("tell", "prog-f1", "use a dataclass"))

    def test_tell_falls_back_to_broker_without_engine(self):
        self.session.handle("/tell prog-f1 hi")
        self.assertEqual(self.broker.calls[-1], ("tell", "prog-f1", "hi"))

    def test_revise_requires_build(self):
        self.assertIn("build first", self.session.handle("/revise more"))
        self.session.handle("/build x")
        self.session.handle("/revise add docs")
        self.assertEqual(self.engine.calls[-1], ("revise", "add docs"))

    def test_agents_and_report(self):
        self.assertIn("no agents", self.session.handle("/agents"))
        self.bb.register_agent("prog-f1", role="programmer", depth="senior", state="DONE")
        self.assertIn("prog-f1", self.session.handle("/agents"))
        self.bb.send("prog-f1", "x", kind=KIND_ARTIFACT, section="impl/f1")
        self.assertIn("run report", self.session.handle("/report"))

    def test_quit(self):
        self.assertEqual(self.session.handle("/quit"), "__quit__")

    def test_parse_talk(self):
        self.assertEqual(self.session.parse_talk("hello"), ("tpm", "hello"))
        self.assertEqual(self.session.parse_talk("@prog why"), ("prog", "why"))
        self.assertIsNone(self.session.parse_talk("@prog"))        # a switch, not a talk
        self.assertIsNone(self.session.parse_talk("/report"))      # a command
        self.assertIsNone(self.session.parse_talk(""))


if __name__ == "__main__":
    unittest.main()
