"""M6: one work agent driven end-to-end through the context-manager (mock model)."""

import os
import tempfile
import unittest

from pixibot.blackboard import Blackboard
from pixibot.context_manager import STATE_DONE, ContextManager
from pixibot.factory import make_agent
from pixibot.model import MockModel, ModelResponse, ToolCall


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bb = Blackboard(self.path, run_id="e2e")

    def tearDown(self):
        self.bb.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except FileNotFoundError:
                pass

    def test_programmer_agent_end_to_end(self):
        model = MockModel([
            ModelResponse(
                tool_calls=[ToolCall("t1", "write_artifact",
                                     {"section": "impl/f1", "content": "print('hi')"})],
                stop_reason="tool_use",
            ),
            ModelResponse(text="implemented f1", stop_reason="end_turn"),
        ])
        agent = make_agent("prog-f1", role="programmer", depth="senior",
                           model=model, scope="impl/f1")
        cm = ContextManager(self.bb, max_steps=20)
        cm.register(agent.agent_id, agent.runner,
                    role="programmer", depth="senior", scope="impl/f1")

        self.bb.send("tpm", "implement feature f1", to="prog-f1")  # kick
        steps = cm.run()

        self.assertGreaterEqual(steps, 1)
        self.assertEqual(self.bb.read_section("impl/f1").payload, "print('hi')")
        self.assertEqual(self.bb.get_agent("prog-f1")["state"], STATE_DONE)


if __name__ == "__main__":
    unittest.main()
