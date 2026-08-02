"""M7: full mock pipeline — input -> TPM -> MoA -> run -> Observer report."""

import json
import os
import tempfile
import unittest

from pixibot.blackboard import Blackboard
from pixibot.model import MockModel, ModelResponse, ToolCall
from pixibot.run import run_pipeline
from pixibot.schema import ValidationFailed


def sample_input():
    return {
        "objective": "Build a URL shortener",
        "target": {"language": "python", "framework": "flask", "platform": "linux"},
        "constraints": [], "non_goals": [], "acceptance_criteria": ["POST /shorten works"],
        "review_cadence": "per-feature",
        "budget_ceilings": {"compute": 100000, "max_depth": "senior", "scope": "one service"},
    }


def projection_json():
    return json.dumps({
        "plan_summary": "implement shorten",
        "breakdown": [{"id": "f1", "desc": "shorten", "depends_on": []}],
        "blackboard_schema": {"sections": ["impl/f1"]},
        "agents": [{
            "id": "prog-f1", "role": "programmer",
            "budget": {"compute": 40000, "depth": "senior", "scope": "impl/f1"},
            "blackboard": {"reads": [], "writes": ["impl/f1"]},
            "activates_on": "message",
        }],
        "checkpoints": [{"id": "cp1", "after": ["prog-f1"], "demo": "call it", "gate": "user_feedback"}],
    })


def mock_factory(spec):
    section = spec["blackboard"]["writes"][0]
    return MockModel([
        ModelResponse(tool_calls=[ToolCall("t1", "write_artifact",
                      {"section": section, "content": "CODE"})], stop_reason="tool_use"),
        ModelResponse(text="done", stop_reason="end_turn"),
    ])


class PipelineTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="pipe")

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_full_mock_pipeline(self):
        tpm_model = MockModel([ModelResponse(text=projection_json())])
        result = run_pipeline(sample_input(), tpm_model=tpm_model,
                              model_factory=mock_factory, bb=self.bb)
        self.assertEqual(self.bb.read_section("impl/f1").payload, "CODE")
        self.assertGreaterEqual(result["steps"], 1)
        self.assertIn("Message DAG", result["report"])

    def test_invalid_input_rejected(self):
        with self.assertRaises(ValidationFailed):
            run_pipeline({"objective": "x"}, tpm_model=MockModel([]),
                         model_factory=mock_factory, bb=self.bb)


if __name__ == "__main__":
    unittest.main()
