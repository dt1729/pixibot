"""Tests for input/projection validation and the repair loop."""

import unittest

from pixibot.schema import (
    ValidationFailed,
    obtain_valid,
    validate_input,
    validate_projection,
)


def good_input():
    return {
        "objective": "Build a URL shortener",
        "target": {"language": "python", "framework": "flask", "platform": "linux"},
        "constraints": ["no external db"],
        "non_goals": ["auth"],
        "acceptance_criteria": ["POST /shorten returns a code"],
        "review_cadence": "per-feature",
        "budget_ceilings": {"compute": 100000, "max_depth": "senior", "scope": "single service"},
    }


def good_projection():
    return {
        "plan_summary": "one feature",
        "breakdown": [{"id": "f1", "desc": "shorten", "depends_on": []}],
        "blackboard_schema": {"sections": ["impl/f1"]},
        "agents": [
            {
                "id": "prog-f1",
                "role": "programmer",
                "budget": {"compute": 40000, "depth": "senior", "scope": "impl/f1"},
                "blackboard": {"reads": ["topology"], "writes": ["impl/f1"]},
                "activates_on": "message",
            }
        ],
        "checkpoints": [{"id": "cp1", "after": ["prog-f1"], "demo": "run it", "gate": "user_feedback"}],
    }


class SchemaTest(unittest.TestCase):
    def test_good_input_valid(self):
        self.assertEqual(validate_input(good_input()), [])

    def test_missing_objective(self):
        inp = good_input()
        del inp["objective"]
        self.assertTrue(any("objective" in e for e in validate_input(inp)))

    def test_bad_max_depth(self):
        inp = good_input()
        inp["budget_ceilings"]["max_depth"] = "wizard"
        self.assertTrue(any("max_depth" in e for e in validate_input(inp)))

    def test_good_projection_valid(self):
        self.assertEqual(validate_projection(good_projection()), [])

    def test_empty_agents(self):
        proj = good_projection()
        proj["agents"] = []
        self.assertTrue(any("non-empty" in e for e in validate_projection(proj)))

    def test_duplicate_agent_id(self):
        proj = good_projection()
        proj["agents"].append(dict(proj["agents"][0]))
        self.assertTrue(any("duplicate" in e for e in validate_projection(proj)))

    def test_repair_loop_converges(self):
        attempts = {"n": 0}

        def generate(errors):
            attempts["n"] += 1
            proj = good_projection()
            if attempts["n"] == 1:
                proj["agents"] = []  # first attempt invalid
            return proj

        result = obtain_valid(generate, validate_projection, max_attempts=3)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(validate_projection(result), [])

    def test_repair_loop_gives_up(self):
        with self.assertRaises(ValidationFailed):
            obtain_valid(lambda errs: {"bad": True}, validate_projection, max_attempts=2)


if __name__ == "__main__":
    unittest.main()
