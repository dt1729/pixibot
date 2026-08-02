"""Integration tests for the Engine: build cascade, directive-resume, revision."""

import os
import tempfile
import unittest

from pixibot import mockrun
from pixibot.blackboard import Blackboard
from pixibot.engine import Engine


class EngineTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="eng")
        self.engine = Engine(self.bb, mockrun.mock_tpm_model("thing"), mockrun.mock_model_factory())

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_build_runs_full_cascade(self):
        self.engine.build_objective("thing")
        for section in ("topology", "impl/f1", "tests/f1"):
            self.assertIsNotNone(self.bb.read_section(section), f"missing {section}")

    def test_tell_resumes_and_updates(self):
        self.engine.build_objective("thing")
        v1 = self.bb.read_section("impl/f1").payload
        self.engine.tell("prog-f1", "use a dataclass")
        v2 = self.bb.read_section("impl/f1").payload
        self.assertNotEqual(v1, v2)
        self.assertIn("v2", v2)
        # the tester re-ran off the new artifact (handshake), not just the programmer
        self.assertIn("2 tests", self.bb.read_section("tests/f1").payload)

    def test_revise_adds_agent(self):
        self.engine.build_objective("thing")
        self.assertIsNone(self.bb.read_section("docs/f1"))
        self.engine.revise("add API docs")
        self.assertIsNotNone(self.bb.read_section("docs/f1"))
        self.assertIn("docs-f1", [a["agent_id"] for a in self.bb.list_agents()])


if __name__ == "__main__":
    unittest.main()
