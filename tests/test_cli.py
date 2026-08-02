"""Tests for the ChatSession command router (fake broker, no input loop)."""

import os
import tempfile
import unittest

from pixibot.blackboard import KIND_ARTIFACT, Blackboard
from pixibot.cli import ChatSession


class FakeBroker:
    def __init__(self):
        self.calls = []

    def talk(self, agent, message):
        self.calls.append(("talk", agent, message))
        return f"[{agent}] {message}"

    def tell(self, agent, text):
        self.calls.append(("tell", agent, text))
        return 42


class CliTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="cli")
        self.broker = FakeBroker()
        self.session = ChatSession(self.bb, self.broker, builder=lambda o: f"built {o}")

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_plain_text_routes_to_default_tpm(self):
        out = self.session.handle("hello")
        self.assertEqual(self.broker.calls[-1], ("talk", "tpm", "hello"))
        self.assertIn("tpm", out)

    def test_at_agent_talks(self):
        self.session.handle("@prog-f1 why a dict?")
        self.assertEqual(self.broker.calls[-1], ("talk", "prog-f1", "why a dict?"))

    def test_at_agent_no_text_switches_target(self):
        self.session.handle("@prog-f1")
        self.assertEqual(self.session.target, "prog-f1")
        self.session.handle("status?")
        self.assertEqual(self.broker.calls[-1], ("talk", "prog-f1", "status?"))

    def test_tell_directive(self):
        out = self.session.handle("/tell prog-f1 use a dataclass")
        self.assertEqual(self.broker.calls[-1], ("tell", "prog-f1", "use a dataclass"))
        self.assertIn("42", out)

    def test_agents_listing(self):
        self.assertIn("no agents", self.session.handle("/agents"))
        self.bb.register_agent("prog-f1", role="programmer", depth="senior", state="DONE")
        self.assertIn("prog-f1", self.session.handle("/agents"))

    def test_report(self):
        self.bb.register_agent("prog-f1", scope="impl")
        self.bb.send("prog-f1", "x", kind=KIND_ARTIFACT, section="impl/f1")
        self.assertIn("run report", self.session.handle("/report"))

    def test_build_invokes_builder(self):
        self.assertEqual(self.session.handle("/build a thing"), "built a thing")

    def test_quit(self):
        self.assertEqual(self.session.handle("/quit"), "__quit__")


if __name__ == "__main__":
    unittest.main()
