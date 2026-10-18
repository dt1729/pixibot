# Contract: pixibot.tools

**Status:** accepted
**Depth:** senior

## Responsibility

Give an agent the five things it can do to the world, and the text it gets back.

## Interface

```python
WRITE_ARTIFACT: dict; READ_FILE: dict; LIST_FILES: dict; RUN_BASH: dict; SEND_MESSAGE: dict
DEFAULT_TOOL_DEFS: list[dict]            # the schemas the model sees

def write_artifact(agent, bb, inp) -> str
def read_file(agent, bb, inp) -> str
def list_files(agent, bb, inp) -> str
def run_bash(agent, bb, inp) -> str
def send_message(agent, bb, inp) -> str
def default_tool_impls() -> dict[str, ToolImpl]
```

- Every implementation returns **text the model will read**, including every refusal and
  every error. None of them raises for an input the model controls: a tool that raised
  would end the activation, and the model cannot see an exception.
- Definitions and implementations are deliberately separate values: the harness can hand a
  model a tool it will then refuse (adoption D's blinded test-designer), and can log what
  was attempted.
- `write_artifact`/`run_bash` detect their **own truncation**: a call missing a required
  field is almost always a response cut off mid-JSON at the output limit, so the result
  says that and asks for a smaller retry.
- With a workspace, files are real and the agent logs its changes afterwards; without one,
  `write_artifact` falls back to a blackboard artifact at that section and `list_files`
  lists written sections. The fallback is what lets a test drive an agent with no disk.
- `run_bash` output is clipped to 4000 characters for the model; the **full** transcript
  (command, exit code, output) goes to the blackboard as a control event.
- `read_file` consults the workspace first, then a blackboard artifact at the same path,
  and reports `(no file: path)` rather than raising.
- The read denylist is enforced here for both `read_file` and `run_bash`, because a shell
  command naming a denied path is the obvious way around a path check.

## Seam

No seam of its own: these are the concrete five. The variation point is one level up —
`agent.Toolset` chooses *which* implementations an agent gets, which is how the
test-designer runs with a denylist and how tests substitute recorders.

They are module-level functions rather than closures in a factory so each is importable
and testable on its own; `default_tool_impls()` only assembles the mapping.

Rejected: a `Tool` class per tool, pairing schema with implementation. The pairing is the
thing we want breakable — the harness decides which implementation backs a schema.

## Dependencies

- **May import:** `pixibot.blackboard`, `pixibot.agent` (types only, under TYPE_CHECKING)
- **Must not import:** `pixibot.workspace` directly (it arrives on the agent),
  `pixibot.engine`, `pixibot.cli`
- **Imported by:** `pixibot.agent`

## Errors

No error modes. Every failure is a returned string: a truncated call, a denied read, a
missing file, a missing workspace, a non-zero exit. The agent loop also catches anything
unexpected and returns its text, so a tool raising degrades to a message rather than a
lost turn.

## Observability

- **No logging here.** The agent loop logs every tool call with its summarised argument and
  the first line of the result, which is the one place that knows which activation and
  which agent the call belongs to.
- `run_bash` writes the full command transcript to the **blackboard** (a control event),
  which is what `report` and the Observer read.
- Never logged or echoed back in full: file contents (only a character count is reported)
  and the untruncated command output.

## Tests

- `write_artifact` writes a real file when a workspace is attached, and a blackboard
  artifact when one is not
- `write_artifact` without `content` reports truncation instead of writing nothing silently
- `read_file` returns workspace content, then blackboard content, then a "no file" message
- a denied path and a command naming a denied path are both refused
- `run_bash` without a command reports truncation
- `run_bash` records the command and exit code on the blackboard
- `send_message` lands in the addressee's inbox

## Deletion test

Deleting this module leaves agents able to think and not to act. The refusals and
truncation hints would reappear inside the agent loop, mixed with turn handling — and both
were added because a live run failed without them. It earns its keep.
