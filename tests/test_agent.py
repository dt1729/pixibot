"""Tests for the ReasoningAgent tool loop (MockModel — no API needed)."""

import os
import shutil
import tempfile
import unittest

from pixibot.agent import ReasoningAgent
from pixibot.blackboard import Blackboard
from pixibot.model import MockModel, ModelResponse, ToolCall
from pixibot.workspace import Workspace


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

    def test_producer_is_nudged_when_it_writes_nothing(self):
        """A programmer that tries to finish without creating a file gets pushed to
        write one before the turn is accepted."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        model = MockModel([
            ModelResponse(text="I looked around, nothing to do.", stop_reason="end_turn"),
            ModelResponse(
                tool_calls=[ToolCall("t1", "write_artifact",
                                     {"section": "app.py", "content": "print('hi')"})],
                stop_reason="tool_use"),
            ModelResponse(text="done", stop_reason="end_turn"),
        ])
        agent = ReasoningAgent("dev", model, role="programmer", workspace=Workspace(root))
        self.bb.register_agent("dev")
        self.bb.send("user", "build an app", to="dev")
        agent.runner(self.bb, "dev", self.bb.poll_inbox("dev"))
        self.assertEqual(agent.workspace.read_file("app.py"), "print('hi')")

    def test_nonproducer_not_nudged(self):
        """A reviewer with nothing to write is allowed to finish empty (no nudge)."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        model = MockModel([ModelResponse(text="looks fine", stop_reason="end_turn")])
        agent = ReasoningAgent("rev", model, role="reviewer", workspace=Workspace(root))
        self.bb.register_agent("rev")
        self.bb.send("user", "review", to="rev")
        agent.runner(self.bb, "rev", self.bb.poll_inbox("rev"))  # must not raise/hang
        self.assertEqual(agent.workspace.list_files(), [])

    def test_thinking_is_emitted_and_logged(self):
        from pixibot.blackboard import KIND_CONTROL
        seen = []
        model = MockModel([
            ModelResponse(text="ok", thinking="I will implement f first", stop_reason="end_turn"),
        ])
        agent = ReasoningAgent("a", model,
                               on_event=lambda aid, k, d: seen.append((k, d)))
        self.bb.register_agent("a")
        self.bb.send("user", "x", to="a")
        agent.runner(self.bb, "a", self.bb.poll_inbox("a"))
        self.assertTrue(any(k == "think" for k, _ in seen))  # streamed to the live pane
        logged = [e for e in self.bb.history(from_agent="a", kind=KIND_CONTROL)
                  if e.payload.startswith("[thinking]")]
        self.assertTrue(logged)  # persisted for cross-instance `think <agent>`
        self.assertIn("I will implement f first", logged[-1].payload)

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
