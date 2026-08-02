"""Tests for the ReasoningAgent tool loop (MockModel — no API needed)."""

import os
import tempfile
import unittest

from pixibot.agent import ReasoningAgent
from pixibot.blackboard import Blackboard
from pixibot.model import MockModel, ModelResponse, ToolCall


class AgentTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="ag")

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_agent_uses_tool_then_finishes(self):
        model = MockModel([
            ModelResponse(
                tool_calls=[ToolCall("t1", "write_artifact",
                                     {"section": "impl/f1", "content": "def f(): pass"})],
                stop_reason="tool_use",
            ),
            ModelResponse(text="done", stop_reason="end_turn"),
        ])
        agent = ReasoningAgent("prog", model, scope="impl", system_prompt="you code")
        self.bb.register_agent("prog", scope="impl")
        self.bb.send("user", "build f", to="prog")
        events = self.bb.poll_inbox("prog")

        agent.runner(self.bb, "prog", events)
        self.assertEqual(self.bb.read_section("impl/f1").payload, "def f(): pass")

    def test_context_rebuilt_from_reads(self):
        seen = {}

        def capture(messages):
            seen["content"] = messages[0]["content"]
            return ModelResponse(text="ok", stop_reason="end_turn")

        # seed a section the agent reads
        self.bb.register_agent("topo")
        self.bb.send("topo", "microservices", kind="artifact", section="topology")

        agent = ReasoningAgent("prog", MockModel([capture]), reads=("topology",))
        self.bb.register_agent("prog", scope="impl")
        self.bb.send("user", "go", to="prog")
        agent.runner(self.bb, "prog", self.bb.poll_inbox("prog"))

        self.assertIn("microservices", seen["content"])  # read was injected into context

    def test_unknown_tool_is_reported_not_fatal(self):
        model = MockModel([
            ModelResponse(tool_calls=[ToolCall("t1", "nope", {})], stop_reason="tool_use"),
            ModelResponse(text="recovered", stop_reason="end_turn"),
        ])
        agent = ReasoningAgent("a", model)
        self.bb.register_agent("a")
        self.bb.send("user", "x", to="a")
        # should not raise
        agent.runner(self.bb, "a", self.bb.poll_inbox("a"))


if __name__ == "__main__":
    unittest.main()
