"""Tests for the mechanical checkpoint gate."""

import unittest

from pixibot.gates import checkpoint_gate, run_check


class GatesTest(unittest.TestCase):
    def test_gate_passes_when_all_green(self):
        ok, results = checkpoint_gate([("tests", "true"), ("lint", "true")])
        self.assertTrue(ok)
        self.assertTrue(all(r.passed for r in results))

    def test_gate_fails_if_any_red(self):
        ok, results = checkpoint_gate([("tests", "true"), ("lint", "false")])
        self.assertFalse(ok)
        self.assertFalse(results[1].passed)

    def test_run_check_captures_name_and_output(self):
        r = run_check("echo", "echo hello")
        self.assertEqual(r.name, "echo")
        self.assertTrue(r.passed)
        self.assertIn("hello", r.output)


if __name__ == "__main__":
    unittest.main()
