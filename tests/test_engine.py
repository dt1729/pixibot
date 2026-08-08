"""Integration tests for the Engine: build cascade, directive-resume, revision."""

import os
import shutil
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
        self.wsdir = tempfile.mkdtemp()
        self.engine.workspace_base = self.wsdir   # per-run dirs land here (don't touch $HOME)

    def tearDown(self):
        self.bb.close()
        shutil.rmtree(self.wsdir, ignore_errors=True)
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

    def test_auto_gate_off_by_default(self):
        # auto-replan is opt-in (cost); continuous testing is on
        self.assertNotIn("auto_gate", self.engine.features)
        self.assertIn("continuous_testing", self.engine.features)

    def test_derive_checks_requires_real_py_tests(self):
        from pixibot.workspace import Workspace
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.engine.workspace = Workspace(d)
        self.assertEqual(self.engine._derive_checks(), [])          # nothing to run
        self.engine.workspace.write_file("tests/f1", "2 tests passing")  # not a .py file
        self.assertEqual(self.engine._derive_checks(), [])          # still nothing
        self.engine.workspace.write_file("tests/test_x.py", "def test_x():\n    assert True\n")
        self.assertEqual(len(self.engine._derive_checks()), 1)      # now pytest runs

    def test_gate_passes_on_green_suite(self):
        from pixibot.workspace import Workspace
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.engine.workspace = Workspace(d)
        self.engine.workspace.write_file("tests/test_ok.py", "def test_ok():\n    assert True\n")
        steps, gate = self.engine._gate_loop("r", 0)
        self.assertIsNotNone(gate)
        self.assertTrue(gate[0])  # mechanical gate is green — no auto-revise triggered

    def test_revise_adds_agent(self):
        self.engine.build_objective("thing")
        self.assertIsNone(self.bb.read_section("docs/f1"))
        self.engine.revise("add API docs")
        self.assertIsNotNone(self.bb.read_section("docs/f1"))
        self.assertIn("docs-f1", [a["agent_id"] for a in self.bb.list_agents()])


if __name__ == "__main__":
    unittest.main()
