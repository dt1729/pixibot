"""Tests for the mechanical checkpoint gate.

Check *derivation* is pure, so it is tested directly on file lists. Check
*execution* is tested through a recording executor, so no compiler or cmake has
to exist here; one end-to-end case runs a real pytest to prove the wiring.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from pixibot.gates import GateReport, checks_for, language_of, run_gate
from pixibot.runtime import ExecResult, LocalExecutor


class RecordingExecutor:
    """An executor that answers from a script and remembers what it was asked."""

    workdir = "/work"

    def __init__(self, codes=None):
        self.commands = []
        self.codes = dict(codes or {})

    def run(self, command, *, timeout=120):
        self.commands.append(command)
        for needle, code in self.codes.items():
            if needle in command:
                return ExecResult(code, f"output of {command}")
        return ExecResult(0, f"output of {command}")


class ChecksForTest(unittest.TestCase):
    def test_python_project_with_tests_runs_pytest(self):
        checks = checks_for(["app.py", "tests/test_app.py"])
        self.assertEqual(checks, (("pytest", "python3 -m pytest -q"),))

    def test_python_without_tests_has_nothing_to_check(self):
        self.assertEqual(checks_for(["app.py", "README.md"]), ())

    def test_trailing_test_name_also_counts(self):
        self.assertEqual(len(checks_for(["app_test.py"])), 1)

    def test_cmake_project_configures_builds_and_runs_ctest(self):
        names = [n for n, _ in checks_for(["CMakeLists.txt", "src/main.cpp"])]
        self.assertEqual(names, ["cmake-configure", "cmake-build", "ctest"])

    def test_makefile_project_runs_make_and_its_test_target_when_declared(self):
        files = ["Makefile", "main.cpp"]
        self.assertEqual([n for n, _ in checks_for(files, lambda p: "all:\n\tg++ main.cpp")],
                         ["make"])
        self.assertEqual([n for n, _ in checks_for(files, lambda p: "test:\n\t./a.out")],
                         ["make", "make-test"])

    def test_bare_cpp_sources_fall_back_to_a_compile_check(self):
        checks = checks_for(["a.cpp", "b.cc", "h.hpp"])
        self.assertEqual(len(checks), 1)
        name, command = checks[0]
        self.assertEqual(name, "compile")
        self.assertIn("-fsyntax-only", command)
        self.assertIn("a.cpp", command)
        self.assertIn("b.cc", command)
        self.assertNotIn("h.hpp", command)  # headers are not translation units

    def test_a_mixed_project_checks_both_languages(self):
        checks = checks_for(["tests/test_api.py", "CMakeLists.txt", "core.cpp"])
        names = [n for n, _ in checks]
        self.assertEqual(names[0], "pytest")      # the cheap check runs first
        self.assertIn("ctest", names)
        self.assertEqual(language_of(checks), "python+c/c++")

    def test_language_of_names_the_single_language(self):
        self.assertEqual(language_of(checks_for(["tests/test_a.py"])), "python")
        self.assertEqual(language_of(checks_for(["a.cpp"])), "c/c++")
        self.assertEqual(language_of(()), "none")


class RunGateTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, rel, text=""):
        p = Path(self.root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_empty_project_is_not_applicable(self):
        report = run_gate(self.root, executor=RecordingExecutor())
        self.assertFalse(report.applicable)
        self.assertEqual(report.language, "none")

    def test_every_derived_check_runs_even_after_one_fails(self):
        self.write("CMakeLists.txt", "project(x)")
        self.write("main.cpp", "int main(){}")
        ex = RecordingExecutor(codes={"cmake --build": 2})
        report = run_gate(self.root, executor=ex)
        self.assertEqual(len(ex.commands), 3)          # configure, build, ctest
        self.assertFalse(report.passed)
        self.assertEqual([r.name for r in report.failures], ["cmake-build"])
        self.assertIn("cmake --build", report.summary())

    def test_summary_is_the_failure_text_only(self):
        self.write("tests/test_a.py", "def test_x(): assert True")
        ex = RecordingExecutor(codes={"pytest": 1})
        self.assertIn("pytest", run_gate(self.root, executor=ex).summary())
        self.assertEqual(run_gate(self.root, executor=RecordingExecutor()).summary(),
                         "all checks passed")

    def test_real_python_project_passes_through_a_local_executor(self):
        self.write("tests/test_math.py", "def test_add():\n    assert 1 + 1 == 2\n")
        report = run_gate(self.root, executor=LocalExecutor(self.root))
        self.assertEqual(report.language, "python")
        self.assertTrue(report.passed, report.summary())

    def test_real_failing_suite_is_reported_red(self):
        self.write("tests/test_math.py", "def test_add():\n    assert 1 + 1 == 3\n")
        report = run_gate(self.root, executor=LocalExecutor(self.root))
        self.assertFalse(report.passed)
        self.assertIn("test_add", report.summary())


class GateReportTest(unittest.TestCase):
    def test_a_report_with_no_checks_is_vacuously_green_but_not_applicable(self):
        report = GateReport(language="none")
        self.assertTrue(report.passed)
        self.assertFalse(report.applicable)


if __name__ == "__main__":
    unittest.main()
