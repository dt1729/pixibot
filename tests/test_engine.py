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

    def _workspace(self):
        from pixibot.workspace import Workspace
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.engine.workspace = Workspace(d)
        return self.engine.workspace

    def test_gate_is_not_applicable_without_a_checkable_project(self):
        ws = self._workspace()
        self.assertFalse(self.engine.gate().applicable)      # nothing to run
        ws.write_file("tests/f1", "2 tests passing")         # not a .py file
        self.assertFalse(self.engine.gate().applicable)      # still nothing

    def test_gate_passes_on_green_suite(self):
        self._workspace().write_file("tests/test_ok.py", "def test_ok():\n    assert True\n")
        steps, gate = self.engine._gate_loop("r", 0)
        self.assertIsNotNone(gate)
        self.assertTrue(gate.passed, gate.summary())   # green: no auto-revise triggered
        self.assertEqual(gate.language, "python")
        self.assertEqual(steps, 0)

    def test_a_red_suite_is_reported_with_its_failure_text(self):
        self._workspace().write_file("tests/test_bad.py", "def test_bad():\n    assert False\n")
        report = self.engine.gate()
        self.assertTrue(report.applicable)
        self.assertFalse(report.passed)
        self.assertIn("test_bad", report.summary())
        self.assertIs(report, self.engine.last_gate)

    def test_a_cpp_project_is_gated_on_its_own_toolchain(self):
        ws = self._workspace()
        ws.write_file("CMakeLists.txt", "cmake_minimum_required(VERSION 3.20)\n")
        ws.write_file("main.cpp", "int main() { return 0; }\n")
        import pixibot.gates as gates_mod
        seen = []
        real = gates_mod.run_check
        try:
            gates_mod.run_check = lambda name, cmd, ex, **kw: (
                seen.append(cmd) or gates_mod.CheckResult(name, True, ""))
            report = self.engine.gate()
        finally:
            gates_mod.run_check = real
        self.assertEqual(report.language, "c/c++")
        self.assertTrue(any("cmake" in c for c in seen))
        self.assertTrue(any("ctest" in c for c in seen))

    def test_revise_adds_agent(self):
        self.engine.build_objective("thing")
        self.assertIsNone(self.bb.read_section("docs/f1"))
        self.engine.revise("add API docs")
        self.assertIsNotNone(self.bb.read_section("docs/f1"))
        self.assertIn("docs-f1", [a["agent_id"] for a in self.bb.list_agents()])


if __name__ == "__main__":
    unittest.main()
