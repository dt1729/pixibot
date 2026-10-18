# Contract: pixibot.model

**Status:** accepted
**Depth:** principal

## Responsibility

Turn (system, messages, tools) into a response, whichever provider is behind it.

## Interface

```python
Message = dict[str, Any]          # Anthropic content-block shape
ToolDef = dict[str, Any]
SystemPrompt = str | list[dict[str, Any]]
DeltaSink = Callable[[str], None]

@dataclass
class ToolCall: id: str; name: str; input: dict[str, Any]
@dataclass
class ModelResponse:
    text: str = ""; tool_calls: list[ToolCall] = []
    stop_reason: str = "end_turn"       # "end_turn" | "tool_use" | "max_tokens"
    thinking: str = ""

class Model:
    def generate(self, *, system, messages, tools) -> ModelResponse
    def stream_generate(self, *, system, messages, tools, on_delta=None) -> ModelResponse

class MockModel(Model):      def __init__(self, script: list[ScriptItem])
class AnthropicModel(Model): def __init__(self, model_id, *, effort="high", max_tokens=32000,
                                          use_thinking=True, use_cache=True)
class OpenAICompatModel(Model): def __init__(self, model_id, *, base_url,
                                             api_key_env="OPENAI_API_KEY", max_tokens=4096)
```

- Pixibot speaks **Anthropic content-block shape internally**, everywhere. Translation to
  the OpenAI chat-completions shape happens inside `OpenAICompatModel` and nowhere else,
  so no other module branches on provider.
- `stop_reason` is part of the interface and callers act on all three values: `tool_use`
  continues the loop, `max_tokens` means the turn was **cut off** (and a tool input may be
  half-written), `end_turn` ends it.
- `generate` is non-streaming *to the caller* but streamed on the wire for Anthropic:
  agents write whole files, so `max_tokens` is large and a plain create would risk the
  SDK's ~10-minute timeout.
- Every request is **sanitised** before it leaves: empty text blocks are dropped and a
  `tool_use` with no matching `tool_result` is removed. Both are 400s from the API, and
  both can appear in a persisted transcript (decision 48). The sanitiser copies; it never
  mutates the caller's list.
- Cache markers (two breakpoints, 1-hour TTL) are applied to a **copy**: persisting them
  would accumulate one breakpoint per turn and exceed the four-breakpoint cap (decision 46).
- SDK imports are deferred to first use, so a missing provider extra costs a feature, not
  the process.
- `MockModel` returns its script in order and a benign `(no more script)` response once
  exhausted — a test asserts on behaviour, not on running out.

## Seam

`Model` is **the** external seam of the system: it is where Pixibot ends and a vendor
begins. Three implementations exist, which is what makes it real — one scripted (every
test and the offline shell), two live.

Deliberately thin: no retry policy, no cost accounting, no prompt assembly. Retries belong
to the SDK, prompts to `lib.factory`, and budget to the projection — a model that did any
of those would be the place every provider difference accumulates.

Rejected: a `Provider` object owning both model construction and calls. Construction is
`pixibot.providers`, which can then be a pure table; calling is here.

## Dependencies

- **May import:** `pixibot.lib.logbook`, stdlib, and lazily the `anthropic` / `openai` SDKs
- **Must not import:** `pixibot.lib.agent`, `pixibot.engine`, `pixibot.providers` — a model
  does not know who is calling it or why
- **Imported by:** `pixibot.providers`, `pixibot.lib.agent`, `pixibot.lib.tpm`,
  `pixibot.interaction`, `pixibot.lib.mockrun`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| whatever the SDK raises (auth, rate limit, network, 400) | the request fails | propagates to the agent loop, which logs it and lets the scheduler mark the agent BLOCKED |
| `ImportError` | the provider's extra is not installed | install `pixibot[compat]`, or choose another provider |

Nothing is retried or swallowed here: a failure that reaches the agent loop is visible in
the run log against a named agent, which is the only place it can be attributed.

## Observability

- **INFO** one line per response with cache accounting: cache-read, cache-write, uncached
  input and output tokens. This is the only place token usage is visible, so it is the
  line a cost question is answered from.
- **No ERROR** here: the exception is the report, and the agent loop logs it with the
  agent id.
- Never logged: prompts, messages, tool inputs, response text, or the API key variable's
  value.

## Tests

- a scripted model returns its responses in order and a benign response when exhausted
- a script item may be a callable, so a test can assert on the messages it received
- `stream_generate` without an override emits the whole text once through `on_delta`
- empty text blocks are dropped from an outgoing request
- a dangling `tool_use` is removed when no `tool_result` follows it
- cache markers appear on the outgoing copy and not on the caller's messages
- the OpenAI translation maps text, `tool_use` and `tool_result` blocks correctly
- a tool call with unparseable arguments becomes an empty input rather than raising
- streamed tool-call fragments are reassembled in index order

## Deletion test

Deleting this module makes every caller provider-specific: the agent loop would branch on
SDK shape, the TPM would parse two response formats, and the four message-shape defences
(empty blocks, dangling tool_use, cache copying, streamed assembly) would be rediscovered
once per call site — each of them was a live incident. It earns its keep.
