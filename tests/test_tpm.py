"""Tests for the TPM plan() flow, including the repair loop (mock model)."""

import json
import unittest

from pixibot.model import MockModel, ModelResponse
from pixibot.schema import ValidationFailed
from pixibot.tpm import plan


def valid_projection_json():
    return json.dumps({
        "plan_summary": "one feature",
        "breakdown": [{"id": "f1", "desc": "x", "depends_on": []}],
        "blackboard_schema": {"sections": ["impl/f1"]},
        "agents": [{
            "id": "prog-f1", "role": "programmer",
            "budget": {"compute": 40000, "depth": "senior", "scope": "impl/f1"},
            "blackboard": {"reads": [], "writes": ["impl/f1"]},
            "activates_on": "message",
        }],
        "checkpoints": [{"id": "cp1", "after": ["prog-f1"], "demo": "run", "gate": "user_feedback"}],
    })


class TpmTest(unittest.TestCase):
    def test_plan_returns_valid_projection(self):
        model = MockModel([ModelResponse(text=valid_projection_json())])
        proj = plan({"objective": "x"}, model)
        self.assertEqual(proj["agents"][0]["id"], "prog-f1")

    def test_plan_tolerates_prose_around_json(self):
        wrapped = "Sure! Here is the plan:\n" + valid_projection_json() + "\nHope that helps."
        model = MockModel([ModelResponse(text=wrapped)])
        proj = plan({"objective": "x"}, model)
        self.assertEqual(proj["agents"][0]["role"], "programmer")

    def test_repair_loop_fixes_invalid(self):
        bad = json.dumps({"plan_summary": "x", "agents": [], "checkpoints": []})
        model = MockModel([ModelResponse(text=bad), ModelResponse(text=valid_projection_json())])
        proj = plan({"objective": "x"}, model, max_repairs=3)
        self.assertTrue(proj["agents"])

    def test_repair_loop_gives_up(self):
        bad = ModelResponse(text=json.dumps({"nope": 1}))
        model = MockModel([bad, bad, bad])
        with self.assertRaises(ValidationFailed):
            plan({"objective": "x"}, model, max_repairs=3)


if __name__ == "__main__":
    unittest.main()
