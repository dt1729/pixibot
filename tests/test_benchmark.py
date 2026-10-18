"""Tests for the benchmark harness.

The suite runner is exercised with a fake engine source, so no model, no key and
no build is involved; one end-to-end case runs the real offline engine to prove
the wiring and the mechanical judging.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pixibot.benchmark import (
    DEFAULT_TASKS,
    STANDARD_ARMS,
    Arm,
    Report,
    Result,
    Task,
    default_engines,
    load_tasks,
    run_suite,
    run_task,
    select_arms,
)
from pixibot.blackboard import Blackboard
from pixibot.engine import Engine
from pixibot.gates import CheckResult, GateReport


class FakeEngine:
    """An engine-shaped double: records the features it was given, fakes a gate."""

    def __init__(self, *, gate, files=("app.py",), steps=3, raises=None):
        self.features = set()
        self.workspace_base = None
        self._gate = gate
        self._files = files
        self._steps = steps
        self._raises = raises
        self.bb = self
        self.workspace = self

    # blackboard + workspace stand-ins
    def list_agents(self):
        return [{"agent_id": "a"}, {"agent_id": "b"}]

    def list_files(self):
        return list(self._files)

    def build(self, inp):
        if self._raises:
            raise self._raises
        self.built = inp
        return {"steps": self._steps}

    def gate(self):
        return self._gate


GREEN = GateReport("python", (CheckResult("pytest", True, ""),))
RED = GateReport("python", (CheckResult("pytest", False, "1 failed"),))
NOTHING = GateReport("none")


def source(engine):
    seen = []

    def make(task, arm):
        seen.append((task.id, arm.name))
        engine.features = set(arm.features)
        return engine

    make.seen = seen
    return make


class ResultTest(unittest.TestCase):
    def test_a_green_gate_is_the_only_pass(self):
        self.assertTrue(Result("t", "a", gate_applicable=True, gate_passed=True).ok)

    def test_a_red_gate_is_not_a_pass(self):
        self.assertFalse(Result("t", "a", gate_applicable=True, gate_passed=False).ok)

    def test_nothing_to_check_is_not_a_pass(self):
        # a build that produced nothing checkable is the failure we are hunting
        self.assertFalse(Result("t", "a", gate_applicable=False, gate_passed=True).ok)

    def test_an_error_is_not_a_pass(self):
        self.assertFalse(Result("t", "a", gate_applicable=True, gate_passed=True,
                                error="boom").ok)


class RunTaskTest(unittest.TestCase):
    def test_a_green_run_is_recorded_with_its_counts(self):
        r = run_task(Task("t1", "do a thing"), Arm("default", frozenset({"continuous_testing"})),
                     source(FakeEngine(gate=GREEN, files=("a.py", "b.py"), steps=5)))
        self.assertTrue(r.ok)
        self.assertEqual((r.task, r.arm, r.steps, r.files, r.agents), ("t1", "default", 5, 2, 2))
        self.assertGreaterEqual(r.seconds, 0.0)
        self.assertEqual(r.error, "")

    def test_the_arm_features_reach_the_engine(self):
        engine = FakeEngine(gate=GREEN)
        run_task(Task("t1", "x"), Arm("all", frozenset({"continuous_testing", "auto_gate"})),
                 source(engine))
        self.assertEqual(engine.features, {"continuous_testing", "auto_gate"})

    def test_the_task_language_reaches_the_input(self):
        engine = FakeEngine(gate=GREEN)
        run_task(Task("t1", "x", language="cpp"), Arm("bare"), source(engine))
        self.assertEqual(engine.built["target"]["language"], "cpp")

    def test_a_raising_build_becomes_a_failed_result(self):
        r = run_task(Task("t1", "x"), Arm("bare"),
                     source(FakeEngine(gate=GREEN, raises=RuntimeError("no key"))))
        self.assertFalse(r.ok)
        self.assertIn("RuntimeError", r.error)
        self.assertIn("no key", r.error)

    def test_a_red_gate_records_the_failure_without_an_error(self):
        r = run_task(Task("t1", "x"), Arm("bare"), source(FakeEngine(gate=RED)))
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "")
        self.assertTrue(r.gate_applicable)


class RunSuiteTest(unittest.TestCase):
    def test_every_task_runs_against_every_arm(self):
        src = source(FakeEngine(gate=GREEN))
        report = run_suite([Task("t1", "a"), Task("t2", "b")],
                           [Arm("bare"), Arm("default")], src)
        self.assertEqual(len(report.results), 4)
        self.assertEqual(src.seen, [("t1", "bare"), ("t1", "default"),
                                    ("t2", "bare"), ("t2", "default")])

    def test_a_failing_task_does_not_end_the_suite(self):
        engines = [FakeEngine(gate=GREEN, raises=ValueError("x")), FakeEngine(gate=GREEN)]

        def make(task, arm):
            return engines.pop(0)

        report = run_suite([Task("t1", "a"), Task("t2", "b")], [Arm("bare")], make)
        self.assertEqual(len(report.results), 2)
        self.assertFalse(report.results[0].ok)
        self.assertTrue(report.results[1].ok)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.report = Report((
            Result("t1", "bare", gate_applicable=True, gate_passed=False),
            Result("t1", "default", gate_applicable=True, gate_passed=True),
            Result("t2", "bare", error="boom"),
            Result("t2", "default", gate_applicable=True, gate_passed=True),
        ))

    def test_tally_counts_passes_per_arm(self):
        self.assertEqual(self.report.by_arm(), {"bare": (0, 2), "default": (2, 2)})

    def test_table_names_every_run_and_the_tally(self):
        table = self.report.table()
        self.assertIn("| t1 | bare | FAIL |", table)
        self.assertIn("| t2 | bare | error |", table)
        self.assertIn("| default | 2/2 |", table)

    def test_json_round_trips(self):
        data = json.loads(self.report.to_json())
        self.assertEqual(len(data["results"]), 4)
        self.assertEqual(data["results"][1]["arm"], "default")

    def test_an_empty_report_still_renders(self):
        self.assertIn("| task | arm |", Report().table())


class SelectionTest(unittest.TestCase):
    def test_arms_come_back_in_the_order_asked(self):
        self.assertEqual([a.name for a in select_arms(["both", "bare"])], ["both", "bare"])

    def test_an_unknown_arm_raises(self):
        with self.assertRaises(ValueError):
            select_arms(["bare", "wizard"])

    def test_the_standard_arms_are_distinct_feature_sets(self):
        sets = [frozenset(a.features) for a in STANDARD_ARMS]
        self.assertEqual(len(sets), len(set(sets)))

    def test_built_in_tasks_have_unique_ids_and_mention_tests(self):
        ids = [t.id for t in DEFAULT_TASKS]
        self.assertEqual(len(ids), len(set(ids)))
        for t in DEFAULT_TASKS:
            self.assertIn("test", t.objective)

    def test_tasks_load_from_json(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = Path(d) / "tasks.json"
        path.write_text(json.dumps([{"id": "x", "objective": "do x", "hard": True}]),
                        encoding="utf-8")
        tasks = load_tasks(str(path))
        self.assertEqual(tasks[0].id, "x")
        self.assertTrue(tasks[0].hard)
        self.assertEqual(tasks[0].language, "python")   # defaulted


class OfflineEndToEndTest(unittest.TestCase):
    """The real engine, the offline provider: proves the harness is wired."""

    def test_offline_run_produces_a_result_and_isolated_directories(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        engines = default_engines("offline", base)
        task, arm = Task("mock", "a url shortener"), Arm("default",
                                                         frozenset({"continuous_testing"}))
        engine = engines(task, arm)
        self.assertIsInstance(engine, Engine)
        self.assertIsInstance(engine.bb, Blackboard)
        self.assertEqual(engine.features, {"continuous_testing"})
        self.assertTrue((Path(base) / "mock-default").is_dir())
        engine.bb.close()

    def test_a_full_offline_task_is_judged_not_applicable(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        result = run_task(Task("mock", "a url shortener"), Arm("bare"),
                          default_engines("offline", base))
        # the canned mock writes prose, not a pytest suite: nothing to check, so
        # it must not count as a pass
        self.assertEqual(result.error, "")
        self.assertGreater(result.files, 0)
        self.assertFalse(result.gate_applicable)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
