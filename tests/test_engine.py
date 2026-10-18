"""Integration tests for the Engine: build cascade, directive-resume, revision."""

import shutil
import tempfile
import unittest

from pixibot.engine import Engine, default_input
from pixibot.lib import mockrun
from pixibot.model import MockModel, ModelResponse, TokenUsage, ToolCall

from .support import WithBlackboard


def costed_factory():
    """Scripted agents whose turns report 200 tokens each, so a ceiling bites."""
    def factory(spec):
        section = spec["blackboard"]["writes"][0]
        usage = TokenUsage(input=100, output=100)
        return MockModel([
            ModelResponse(tool_calls=[ToolCall("t", "write_artifact",
                                               {"section": section, "content": "x"})],
                          stop_reason="tool_use", usage=usage),
            ModelResponse(text="done", stop_reason="end_turn", usage=usage),
        ])
    return factory


class EngineTest(WithBlackboard, unittest.TestCase):
    run_id = "eng"

    def setUp(self):
        super().setUp()
        self.engine = Engine(self.bb, mockrun.mock_tpm_model("thing"), mockrun.mock_model_factory())
        self.wsdir = tempfile.mkdtemp()
        self.engine.workspace_base = self.wsdir   # per-run dirs land here (don't touch $HOME)

    def tearDown(self):
        shutil.rmtree(self.wsdir, ignore_errors=True)

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


class RunBudgetTest(WithBlackboard, unittest.TestCase):
    run_id = "rbud"

    def setUp(self):
        super().setUp()
        self.wsdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.wsdir, True)

    def engine(self):
        eng = Engine(self.bb, mockrun.mock_tpm_model("thing"), costed_factory())
        eng.workspace_base = self.wsdir
        return eng

    def test_a_run_reports_what_it_spent(self):
        eng = self.engine()
        res = eng.build_objective("thing")
        self.assertGreater(res["tokens"], 0)
        self.assertEqual(res["tokens"], eng.spent())

    def test_a_run_with_no_ceiling_is_always_within_budget(self):
        eng = self.engine()
        inp = default_input("thing")
        inp["budget_ceilings"].pop("compute")
        eng.build(inp)
        self.assertTrue(eng.within_budget())
        self.assertIsNone(eng.build(inp)["budget"])

    def test_a_tiny_ceiling_stops_the_run(self):
        eng = self.engine()
        inp = default_input("thing")
        inp["budget_ceilings"]["compute"] = 150     # one agent turn is 200
        res = eng.build(inp)
        self.assertEqual(res["budget"], 150)
        self.assertFalse(eng.within_budget())
        # the cascade stopped early: not every section was written
        written = [s for s in ("topology", "impl/f1", "tests/f1")
                   if self.bb.read_section(s) is not None]
        self.assertLess(len(written), 3, f"the run spent its ceiling but wrote {written}")

    def test_tell_and_revise_spend_under_the_run_ceiling(self):
        """engine.md promises the ceiling is checked after every activation.
        resume() - which tell and revise go through - ran bare until M50."""
        eng = self.engine()
        inp = default_input("thing")
        inp["budget_ceilings"]["compute"] = 100000      # room for the build
        eng.build(inp)
        before = eng.spent()
        eng._ceiling = before                            # nothing left to spend
        steps = eng.tell("prog-f1", "use a dataclass")
        self.assertEqual(steps, 0, "a spent run must not activate on a directive")
        self.assertEqual(eng.spent(), before)
        self.assertFalse(eng.within_budget())

    def test_an_unreadable_ceiling_does_not_stop_the_run(self):
        eng = self.engine()
        inp = default_input("thing")
        inp["budget_ceilings"]["compute"] = "as much as it takes"
        res = eng.build(inp)
        self.assertIsNone(res["budget"])
        self.assertTrue(eng.within_budget())


if __name__ == "__main__":
    unittest.main()
