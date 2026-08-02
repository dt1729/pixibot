"""Model abstraction — a thin, provider-agnostic interface the agents call.

Two implementations:

* ``MockModel`` — deterministic, scripted. Used by tests and the offline demo.
* ``AnthropicModel`` — lazily imports the Anthropic SDK; needed only to run
  against real Claude (requires ``ANTHROPIC_API_KEY``). Uses adaptive thinking
  and per-depth effort (DESIGN.md §8).

Messages use the Anthropic content-block shape (list of role/content dicts), so
``AnthropicModel`` is a thin pass-through and ``MockModel`` simply ignores the
details it doesn't need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Union


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use"


class Model:
    """Interface: turn (system, messages, tools) into a ModelResponse."""

    def generate(self, *, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        raise NotImplementedError

    def stream_generate(self, *, system, messages, tools, on_delta=None) -> ModelResponse:
        """Default: non-streaming, emit the whole text once. Overridden by AnthropicModel."""
        resp = self.generate(system=system, messages=messages, tools=tools)
        if on_delta and resp.text:
            on_delta(resp.text)
        return resp


# A script item is either a fixed response or a function of the messages so far.
ScriptItem = Union[ModelResponse, Callable[[list[dict]], ModelResponse]]


class MockModel(Model):
    """Returns scripted responses in order; a sane default once exhausted."""

    def __init__(self, script: list[ScriptItem]):
        self._script = list(script)
        self._i = 0

    def generate(self, *, system, messages, tools) -> ModelResponse:
        if self._i >= len(self._script):
            return ModelResponse(text="(no more script)", stop_reason="end_turn")
        item = self._script[self._i]
        self._i += 1
        return item(messages) if callable(item) else item


class AnthropicModel(Model):
    """Calls real Claude. The SDK import is deferred to first use."""

    def __init__(self, model_id: str, *, effort: "str | None" = "high",
                 max_tokens: int = 16000, use_thinking: bool = True):
        self.model_id = model_id
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_thinking = use_thinking  # Haiku rejects effort/adaptive thinking
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import anthropic  # deferred: keeps the rest of Pixibot import-free

            self._client = anthropic.Anthropic()
        return self._client

    def generate(self, *, system, messages, tools) -> ModelResponse:
        client = self._client_lazy()
        kwargs = dict(
            model=self.model_id,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools or [],
            messages=messages,
        )
        if self.use_thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        resp = client.messages.create(**kwargs)
        text = ""
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(block.id, block.name, dict(block.input)))
        return ModelResponse(text=text, tool_calls=tool_calls, stop_reason=resp.stop_reason)

    def stream_generate(self, *, system, messages, tools, on_delta=None) -> ModelResponse:
        client = self._client_lazy()
        kwargs = dict(model=self.model_id, max_tokens=self.max_tokens, system=system,
                      tools=tools or [], messages=messages)
        if self.use_thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if (event.type == "content_block_delta"
                        and getattr(event.delta, "type", "") == "text_delta"):
                    if on_delta:
                        on_delta(event.delta.text)
            msg = stream.get_final_message()
        text = ""
        tool_calls: list[ToolCall] = []
        for block in msg.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(block.id, block.name, dict(block.input)))
        return ModelResponse(text=text, tool_calls=tool_calls, stop_reason=msg.stop_reason)
