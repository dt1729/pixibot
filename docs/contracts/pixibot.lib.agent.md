# Contract: pixibot.lib.agent

**Status:** accepted
**Depth:** principal

## Responsibility

Run one agent's activation: rebuild its context, loop model and tools, write results back.

## Interface

```python
EventHook = Callable[[str, str, str], None]        # (agent_id, kind, detail)
ToolImpl = Callable[[ReasoningAgent, Blackboard, dict], str]

@dataclass(frozen=True)
class AgentSpec:                 # who one agent is
    agent_id: str
    model: Model
    system_prompt: str = ""
    role: str | None = None
    depth: str | None = None
    scope: str | None = None             # sections it may write
    reads: tuple[str, ...] = ()          # sections pulled into context
    read_denylist: tuple[str, ...] = ()  # sections it may not read (adoption D)
    max_iters: int = 14

@dataclass(frozen=True)
class RunContext:                # what every agent in one run shares
    workspace: Workspace | None = None
    on_event: EventHook | None = None
    objective: str = ""

@dataclass(frozen=True)
class Toolset:                   # how an agent affects the world
    defs: list[dict]             # schemas the model sees
    impls: Mapping[str, ToolImpl]

class ReasoningAgent:
    def __init__(self, spec: AgentSpec, run: RunContext | None = None, tools: Toolset | None = None)
    agent_id: str; role: str | None; workspace: Workspace | None; read_denylist: tuple[str, ...]
    def runner(self, bb: Blackboard, agent_id: str, events: list[Event]) -> str
```

- `runner` matches `ContextManager.AgentRunner` exactly — that is the contract the
  scheduler calls, and it returns the terminal lifecycle state (`DONE`).
- An activation is **stateful across activations but stateless in memory**: the transcript
  is loaded from the blackboard at the start and saved at the end (DESIGN.md §9,
  decision 45). Nothing is retained on the instance between calls.
- The run objective is re-stated in **every** activation's first message, so a bare status
  ping cannot make an agent invent a new task.
- A producing role (topology, lld, test-designer, programmer, tester) that finishes
  without writing a file and has never delivered gets exactly **one** forced delivery pass
  (decision 43). A role that already delivered is never nudged.
- A turn cut off at `max_tokens` is retried with an explicit truncation notice, at most
  `_MAX_TRUNCATIONS` (2) times — not accepted as finished, which is the M33 defect.
- Tools run whenever the response carries tool calls, even when `stop_reason` is not
  `tool_use`, so no `tool_use` block is ever persisted without its `tool_result` (the API
  rejects that pairing).
- `max_iters` bounds model turns per activation; the nudge pass gets `max(4, max_iters//2)`.
- A tool that raises returns its error text to the model instead of failing the turn: the
  model is expected to read and recover.

## Seam

Three records in, one loop. `AgentSpec` varies per agent, `RunContext` per run, `Toolset`
per policy — and `Model` behind the spec is the real external seam (`MockModel` in tests,
two live providers in production).

`Toolset` is injectable because the test-designer's tool surface is genuinely different
(adoption D denies it implementation reads), and because tests substitute recording impls.
`RunContext` exists so that a run-wide value is passed once rather than threaded through
engine, materialize, register_one and the constructor — the shape that made adding one
value a five-signature change.

Rejected: keeping the 13-keyword constructor. DESIGN.md §1 defines an agent as one config
over one loop; the code should say that, and the records make `dataclasses.replace` the
way to vary an agent instead of re-listing every argument.

## Dependencies

- **May import:** `pixibot.blackboard`, `pixibot.lib.context_manager`, `pixibot.model`,
  `pixibot.lib.tools`, `pixibot.lib.logbook`, `pixibot.workspace` (types only), stdlib
- **Must not import:** `pixibot.engine`, `pixibot.lib.orchestrator`, `pixibot.cli`,
  `pixibot.providers` — an agent does not know who scheduled it or which provider built
  its model
- **Imported by:** `pixibot.lib.factory`, `pixibot.lib.orchestrator`, `pixibot.lib.tools` (types)

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| whatever `Model.generate` raises | the provider call fails (auth, rate limit, network) | propagates to `ContextManager.step`, which marks the agent BLOCKED and keeps the run alive |

Tool failures, memory-write failures, report failures and hook failures are all caught and
logged: none of them is worth losing an activation's work over. A tool failure is returned
to the model as text, which is the only one the model can act on.

## Observability

- **INFO** one line per model turn: agent id, turn number, stop reason, tool-call count,
  text and thinking lengths. This is the line that shows an agent spinning.
- **INFO** one line per tool call: tool name, its summarised argument (a path, a section,
  a recipient — never the content), and the first line of the result.
- **WARNING** on a delivery nudge, and on each `max_tokens` truncation with its retry
  count: both mean the agent is burning budget without producing.
- **ERROR** (via `log.exception`) when memory persistence, the completion report, thinking
  capture or a UI hook fails — the run continues, but a swallowed failure here is how an
  agent silently stops being inspectable.
- Every line carries the agent id, which is what an operator greps for.
- Never logged: file contents, full tool inputs, the system prompt, or the transcript.
  Reasoning *is* persisted to the blackboard as a control event (decision 44), which is a
  deliberate exception the user asked for, and never written to the run log.

## Tests

- a tool call is executed and its artifact lands on the blackboard
- sections named in `reads` appear in the rebuilt context
- a producer that writes nothing is nudged once and then delivers
- a non-producer that writes nothing is allowed to finish
- an agent that already delivered is not nudged on re-activation
- the objective survives a re-wake by a bare status ping, and the transcript resumes
- a turn with no text and no tools persists no empty text block
- a `max_tokens` turn still runs its tools, so no dangling `tool_use` is stored
- a truncated write is retried and then succeeds
- endless truncation stops at the bound rather than spinning
- an unknown tool name is reported to the model, not raised

## Deletion test

Deleting this module deletes Pixibot: it is the one loop every agent is an instance of.
The records would reappear as keyword arguments, and the four defences it encodes
(delivery nudge, truncation retry, tool-result pairing, empty-block suppression) would
have to be rediscovered — each of them was a live incident. It earns its keep.
