"""M6: one work agent driven end-to-end through the context-manager (mock model)."""

import unittest

from pixibot.lib.context_manager import STATE_DONE, ContextManager
from pixibot.lib.factory import make_agent
from pixibot.model import MockModel, ModelResponse, ToolCall

from .support import WithBlackboard


class EndToEndTest(WithBlackboard, unittest.TestCase):
    run_id = "e2e"

    def test_programmer_agent_end_to_end(self):
        model = MockModel([
            ModelResponse(
                tool_calls=[ToolCall("t1", "write_artifact",
                                     {"section": "impl/f1", "content": "print('hi')"})],
                stop_reason="tool_use",
            ),
            ModelResponse(text="implemented f1", stop_reason="end_turn"),
        ])
        agent = make_agent({"id": "prog-f1", "role": "programmer",
                            "budget": {"depth": "senior", "scope": "impl/f1"}}, model)
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
