"""Tests for the OpenAI-compatible provider's translation layer (no network).

Covers Gemini/OpenRouter/etc. since they share OpenAICompatModel. The openai SDK
is imported lazily, so these tests need only the class + fakes.
"""

import unittest

from pixibot.model import OpenAICompatModel


class _FakeFunc:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments, index=0):
        self.id = id
        self.type = "function"
        self.function = _FakeFunc(name, arguments)
        self.index = index


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class OpenAICompatTest(unittest.TestCase):
    def setUp(self):
        self.m = OpenAICompatModel("model-x", base_url="http://x", api_key_env="X_KEY")

    def test_tools_translation(self):
        tools = [{"name": "write_artifact", "description": "d",
                  "input_schema": {"type": "object", "properties": {}}}]
        out = self.m._to_tools(tools)
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(out[0]["function"]["name"], "write_artifact")
        self.assertIsNone(self.m._to_tools([]))

    def test_message_translation_roundtrips_tool_use_and_result(self):
        messages = [
            {"role": "user", "content": "build it"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "t1", "name": "write_artifact",
                 "input": {"section": "impl", "content": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "wrote"}]},
        ]
        out = self.m._to_messages("SYS", messages)
        self.assertEqual(out[0], {"role": "system", "content": "SYS"})
        self.assertEqual(out[1], {"role": "user", "content": "build it"})
        self.assertEqual(out[2]["role"], "assistant")
        self.assertEqual(out[2]["tool_calls"][0]["function"]["name"], "write_artifact")
        self.assertEqual(out[3], {"role": "tool", "tool_call_id": "t1", "content": "wrote"})

    def test_parse_plain_text(self):
        r = self.m._parse_message(_FakeMessage(content="hello"))
        self.assertEqual(r.text, "hello")
        self.assertEqual(r.stop_reason, "end_turn")

    def test_parse_tool_calls(self):
        r = self.m._parse_message(_FakeMessage(
            content=None,
            tool_calls=[_FakeToolCall("t1", "write_artifact", '{"section":"impl","content":"x"}')],
        ))
        self.assertEqual(r.stop_reason, "tool_use")
        self.assertEqual(r.tool_calls[0].name, "write_artifact")
        self.assertEqual(r.tool_calls[0].input, {"section": "impl", "content": "x"})

    def test_parse_tolerates_bad_json_args(self):
        r = self.m._parse_message(_FakeMessage(
            tool_calls=[_FakeToolCall("t1", "f", "not-json")]))
        self.assertEqual(r.tool_calls[0].input, {})


if __name__ == "__main__":
    unittest.main()
