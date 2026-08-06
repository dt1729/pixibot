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

import json
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
    thinking: str = ""             # the model's reasoning for this turn (if exposed)


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


from .logbook import get as _get_logger

_mlog = _get_logger("model")

# 1-hour ephemeral cache: agents sit idle for minutes between activations, so the
# default 5-minute TTL would expire between turns and we'd pay the write premium
# with no read. The 1h write costs 2x but pays off within ~3 reads — which
# continuous re-testing (A) and re-planning loops (E) hit easily.
_CACHE_CTL = {"type": "ephemeral", "ttl": "1h"}


def _cached_system(system):
    """Wrap the system prompt so tools+system are cached (render order is
    tools -> system -> messages, so a breakpoint on the last system block covers
    both). Returns a new value; never mutates the caller's."""
    if not system:
        return system
    if isinstance(system, str):
        return [{"type": "text", "text": system, "cache_control": _CACHE_CTL}]
    blocks = [dict(b) if isinstance(b, dict) else b for b in system]
    if blocks and isinstance(blocks[-1], dict):
        blocks[-1] = {**blocks[-1], "cache_control": _CACHE_CTL}
    return blocks


def _sanitize_messages(messages):
    """Drop empty text blocks (and messages left with no content). The API rejects
    empty text content blocks, and a persisted stateful transcript can contain one
    from a model turn that produced neither text nor a tool call."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            blocks = [b for b in content
                      if not (isinstance(b, dict) and b.get("type") == "text"
                              and not (b.get("text") or "").strip())]
            if not blocks:
                continue
            out.append({**m, "content": blocks})
        elif isinstance(content, str) and not content.strip():
            continue
        else:
            out.append(m)
    return out


def _cached_messages(messages):
    """Rolling breakpoint on the last block of the most recent turn, so the next
    request reuses the whole prior-conversation prefix. Copies before marking —
    the marker must NOT be persisted into the agent's stored transcript, or it
    would accumulate one breakpoint per turn and blow past the 4-breakpoint cap."""
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content, "cache_control": _CACHE_CTL}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        blocks = list(content)
        blocks[-1] = {**blocks[-1], "cache_control": _CACHE_CTL}
        last["content"] = blocks
    else:
        return messages  # nothing cacheable at the tail
    out[-1] = last
    return out


class AnthropicModel(Model):
    """Calls real Claude. The SDK import is deferred to first use."""

    def __init__(self, model_id: str, *, effort: "str | None" = "high",
                 max_tokens: int = 16000, use_thinking: bool = True, use_cache: bool = True):
        self.model_id = model_id
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_thinking = use_thinking  # Haiku rejects effort/adaptive thinking
        self.use_cache = use_cache
        self._client = None

    def _kwargs(self, system, messages, tools) -> dict:
        messages = _sanitize_messages(messages)  # never send empty text blocks (400)
        if self.use_cache:
            system = _cached_system(system)
            messages = _cached_messages(messages)
        kwargs = dict(model=self.model_id, max_tokens=self.max_tokens,
                      system=system, tools=tools or [], messages=messages)
        if self.use_thinking:
            # display:"summarized" — the default is "omitted", which returns empty
            # thinking text (so the 🧠 pane would show nothing).
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    @staticmethod
    def _log_cache(usage) -> None:
        if usage is None:
            return
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        _mlog.info("usage cache_read=%s cache_write=%s uncached_in=%s out=%s",
                   read, write, getattr(usage, "input_tokens", 0),
                   getattr(usage, "output_tokens", 0))

    def _client_lazy(self):
        if self._client is None:
            import anthropic  # deferred: keeps the rest of Pixibot import-free

            self._client = anthropic.Anthropic()
        return self._client

    def generate(self, *, system, messages, tools) -> ModelResponse:
        client = self._client_lazy()
        resp = client.messages.create(**self._kwargs(system, messages, tools))
        self._log_cache(getattr(resp, "usage", None))
        text = ""
        thinking = ""
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "thinking":
                thinking += getattr(block, "thinking", "")
            elif block.type == "redacted_thinking":
                thinking += "[redacted thinking]"
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(block.id, block.name, dict(block.input)))
        return ModelResponse(text=text, tool_calls=tool_calls,
                             stop_reason=resp.stop_reason, thinking=thinking)

    def stream_generate(self, *, system, messages, tools, on_delta=None) -> ModelResponse:
        client = self._client_lazy()
        with client.messages.stream(**self._kwargs(system, messages, tools)) as stream:
            for event in stream:
                if (event.type == "content_block_delta"
                        and getattr(event.delta, "type", "") == "text_delta"):
                    if on_delta:
                        on_delta(event.delta.text)
            msg = stream.get_final_message()
        self._log_cache(getattr(msg, "usage", None))
        text = ""
        tool_calls: list[ToolCall] = []
        for block in msg.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(block.id, block.name, dict(block.input)))
        return ModelResponse(text=text, tool_calls=tool_calls, stop_reason=msg.stop_reason)


class OpenAICompatModel(Model):
    """Any OpenAI-compatible endpoint: Gemini, OpenRouter, Groq, OpenAI, Ollama.

    Pixibot builds messages/tools in Anthropic shape; this class translates them
    to/from the OpenAI chat-completions shape. The ``openai`` SDK is imported
    lazily (only real calls need it, and only then must it be installed).
    """

    def __init__(self, model_id: str, *, base_url: str,
                 api_key_env: str = "OPENAI_API_KEY", max_tokens: int = 4096):
        self.model_id = model_id
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import os

            from openai import OpenAI  # deferred
            self._client = OpenAI(base_url=self.base_url, api_key=os.environ.get(self.api_key_env))
        return self._client

    # -- Anthropic-shape -> OpenAI-shape ------------------------------------
    @staticmethod
    def _to_messages(system, messages) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            role, content = m["role"], m["content"]
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if role == "assistant":
                text, calls = "", []
                for b in content:
                    if b.get("type") == "text":
                        text += b["text"]
                    elif b.get("type") == "tool_use":
                        calls.append({"id": b["id"], "type": "function",
                                      "function": {"name": b["name"],
                                                   "arguments": json.dumps(b["input"])}})
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:  # user turn: tool_result blocks -> role:tool; text -> user text
                texts = []
                for b in content:
                    if b.get("type") == "tool_result":
                        c = b["content"]
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                    "content": c if isinstance(c, str) else json.dumps(c)})
                    elif b.get("type") == "text":
                        texts.append(b["text"])
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
        return out

    @staticmethod
    def _to_tools(tools):
        if not tools:
            return None
        return [{"type": "function",
                 "function": {"name": t["name"], "description": t.get("description", ""),
                              "parameters": t["input_schema"]}} for t in tools]

    @staticmethod
    def _parse_message(msg) -> ModelResponse:
        text = msg.content or ""
        calls = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (ValueError, TypeError):
                args = {}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        return ModelResponse(text=text, tool_calls=calls,
                             stop_reason="tool_use" if calls else "end_turn")

    def generate(self, *, system, messages, tools) -> ModelResponse:
        client = self._client_lazy()
        resp = client.chat.completions.create(
            model=self.model_id, max_tokens=self.max_tokens,
            messages=self._to_messages(system, messages), tools=self._to_tools(tools),
        )
        return self._parse_message(resp.choices[0].message)

    def stream_generate(self, *, system, messages, tools, on_delta=None) -> ModelResponse:
        client = self._client_lazy()
        stream = client.chat.completions.create(
            model=self.model_id, max_tokens=self.max_tokens,
            messages=self._to_messages(system, messages), tools=self._to_tools(tools),
            stream=True,
        )
        text = ""
        frags: dict[int, dict] = {}
        for chunk in stream:
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                text += delta.content
                if on_delta:
                    on_delta(delta.content)
            for tc in (getattr(delta, "tool_calls", None) or []):
                f = frags.setdefault(tc.index, {"id": None, "name": None, "args": ""})
                if tc.id:
                    f["id"] = tc.id
                if tc.function and tc.function.name:
                    f["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    f["args"] += tc.function.arguments
        calls = []
        for i in sorted(frags):
            f = frags[i]
            try:
                args = json.loads(f["args"] or "{}")
            except (ValueError, TypeError):
                args = {}
            calls.append(ToolCall(f["id"] or f"call_{i}", f["name"], args))
        return ModelResponse(text=text, tool_calls=calls,
                             stop_reason="tool_use" if calls else "end_turn")
