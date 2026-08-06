"""Tests for AnthropicModel prompt-cache assembly (pure — no API calls)."""

import unittest

from pixibot.model import (
    AnthropicModel,
    _cached_messages,
    _cached_system,
    _sanitize_messages,
)


class CacheAssemblyTest(unittest.TestCase):
    def test_cached_system_wraps_string_without_mutating(self):
        out = _cached_system("you are the architect")
        self.assertEqual(out[0]["text"], "you are the architect")
        self.assertEqual(out[0]["cache_control"], {"type": "ephemeral", "ttl": "1h"})

    def test_cached_messages_marks_last_block_only(self):
        msgs = [
            {"role": "user", "content": "build X"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ]
        out = _cached_messages(msgs)
        # last block of the last message carries the breakpoint
        self.assertIn("cache_control", out[-1]["content"][-1])
        # earlier turn is untouched
        self.assertNotIn("cache_control", str(out[0]))

    def test_cached_messages_does_not_mutate_input(self):
        """Critical: markers must never leak into the persisted transcript, or they
        accumulate one breakpoint per turn and blow past the 4-breakpoint cap."""
        msgs = [{"role": "user", "content": "hi"}]
        _cached_messages(msgs)
        self.assertEqual(msgs, [{"role": "user", "content": "hi"}])  # unchanged
        # and it never accumulates across repeated calls on its own output-source
        _cached_messages(msgs)
        self.assertEqual(msgs, [{"role": "user", "content": "hi"}])

    def test_kwargs_has_cache_and_summarized_thinking(self):
        m = AnthropicModel("claude-opus-4-8")
        kw = m._kwargs("system charter", [{"role": "user", "content": "go"}], tools=[])
        self.assertEqual(kw["system"][0]["cache_control"]["ttl"], "1h")
        self.assertIn("cache_control", kw["messages"][-1]["content"][-1])
        self.assertEqual(kw["thinking"], {"type": "adaptive", "display": "summarized"})

    def test_sanitize_strips_empty_text_blocks(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},   # empty -> dropped
            {"role": "user", "content": [
                {"type": "text", "text": "   "},                                # empty -> dropped
                {"type": "tool_result", "tool_use_id": "t", "content": "ok"},   # kept
            ]},
        ]
        out = _sanitize_messages(msgs)
        self.assertEqual(len(out), 2)  # the empty assistant message is gone
        self.assertEqual([b["type"] for b in out[-1]["content"]], ["tool_result"])

    def test_kwargs_never_emits_empty_text_block(self):
        m = AnthropicModel("claude-opus-4-8")
        kw = m._kwargs("sys", [{"role": "user", "content": "go"},
                               {"role": "assistant", "content": [{"type": "text", "text": ""}]}],
                       tools=[])
        for msg in kw["messages"]:
            if isinstance(msg["content"], list):
                for b in msg["content"]:
                    if b.get("type") == "text":
                        self.assertTrue(b["text"].strip(), "empty text block leaked into request")

    def test_kwargs_no_cache_when_disabled(self):
        m = AnthropicModel("claude-haiku-4-5", use_thinking=False, use_cache=False)
        kw = m._kwargs("system", [{"role": "user", "content": "go"}], tools=[])
        self.assertEqual(kw["system"], "system")          # left as a bare string
        self.assertEqual(kw["messages"][-1]["content"], "go")  # no breakpoint added
        self.assertNotIn("thinking", kw)


if __name__ == "__main__":
    unittest.main()
