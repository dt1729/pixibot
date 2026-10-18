# Contract: pixibot.claude_cli

**Status:** accepted
**Depth:** senior

## Responsibility

Answer a model turn through the Claude Code CLI, so a run can be simulated without an API key.

## Interface

```python
WINDOWS_FALLBACK: str                 # the winget path, reachable from WSL
DEFAULT_TIMEOUT = 600
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TURNS = 6
CLI_ENV = "PIXIBOT_CLAUDE_CLI"
WINDOWS_GLOBS: tuple[str, ...]
ACTING_TOOLS: tuple[str, ...]         # Claude Code tools a simulated agent must not have
DISALLOWED_TOOLS: str                 # "mcp__*,<ACTING_TOOLS>"
RESPONSE_SCHEMA: dict                 # {text, tool_calls:[{name, input}]}
Runner = Callable[[list[str], str], str]   # argv, stdin -> stdout (tests substitute)

def find_cli(explicit: str | None = None) -> str
def available(explicit: str | None = None) -> bool
def windows_candidates() -> list[str]
def build_argv(cli: str, *, system: str, model: str, mcp_config: str,
               max_turns: int = DEFAULT_MAX_TURNS) -> list[str]
def render_prompt(messages: Sequence[Message], tools: Sequence[ToolDef]) -> str
def parse_envelope(stdout: str) -> ModelResponse
def transcript_dir(cwd: str | Path) -> Path
def purge_transcript(cwd: str | Path) -> None

class ClaudeCliModel(Model):
    def __init__(self, model_id: str = DEFAULT_MODEL, *, cli: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT, max_turns: int = DEFAULT_MAX_TURNS,
                 runner: Runner | None = None)

class CliUnavailableError(RuntimeError)
class CliFailedError(RuntimeError)
```

- One **process per turn**: seconds of latency, and roughly 20,000 input tokens of Claude
  Code system prompt even with `--system-prompt` replacing it. This model is for simulated
  runs and the opt-in `simulation` test tier, never for the unit suite.
- Billing is the point: the CLI runs against the operator's **subscription**, so a
  simulated multi-agent run spends no API credit.
- Every flag in `build_argv` is load-bearing, and each was established by a failing probe
  or by `infra-pivot`'s working judge: `--json-schema` makes the CLI return our shape
  (so a tool call is parsed, not scraped); `--system-prompt` replaces Claude Code's own
  prompt, without which the CLI behaves like a coding agent and argues with the format;
  `--max-turns` 6, because 1 cannot answer and 2 ran out on a real agent prompt
  (incident 007); `--allowedTools ""`
  blocks tool calls while `--disallowedTools` also strips their schemas; `--mcp-config`
  (empty) with `--strict-mcp-config` detaches every MCP server, so an operator's
  connectors cannot reach a simulated agent; `--permission-mode default`, never a
  skip-permissions flag.
- The prompt goes on **stdin**, so its size is not an argv limit.
- Each turn runs in a temp directory and its session transcript is **purged in a finally**:
  the prompt carries a whole agent conversation and must not outlive the call.
- `parse_envelope` parses the answer **first** and raises only when an error left nothing
  usable: the CLI reports `error_max_turns` when it needed one more turn to tidy up, and
  discarding that work turned a finished turn into a BLOCKED agent in a live build
  (incident 007). An error that still carried an answer is logged at WARNING and used.
  Output that is not JSON at all does raise — a simulated turn that silently became empty
  text would read as an agent choosing to do nothing. A result that parses but does not
  match the schema yields an empty answer rather than a raise.
- A turn costs about **20,000 input tokens** of Claude Code's own system prompt even with
  `--system-prompt` replacing it. Per-agent compute ceilings written for API calls
  therefore buy only two or three turns here: raise them when simulating.
- `find_cli` reads `$PIXIBOT_CLAUDE_CLI` before `PATH`, and **globs** the Windows account
  name rather than guessing it from the WSL home — those are two different names on the
  same machine, and guessing made the simulation tier skip itself.
- `usage` is read from the CLI envelope, which is what lets the compute budget (decision
  14) apply to a simulated run exactly as it does to an API one.
- `find_cli` searches an explicit path, then `PATH`, then the Windows binary (WSL reaches
  it through `/mnt/c`). It raises rather than falling back to a mock: a simulated run that
  silently became a scripted one would be worthless as evidence.
- No token streaming: `claude -p` returns one envelope, so `stream_generate` emits the
  whole text once.

## Seam

`Runner` is the seam: argv and stdin in, stdout out. Production runs a subprocess; the
tests pass a double, which is how the whole adapter - argv, prompt rendering, envelope
parsing, usage - is tested with no process, no subscription usage and no network.

The outer seam is `Model` itself (`pixibot.model`), so this is a third implementation
beside the Anthropic SDK and the OpenAI-compatible endpoints. Nothing above it knows which
one a run is using.

Rejected: parsing a tool call out of prose with a regex. The first probes did exactly that
and the model returned commentary instead of JSON; `--json-schema` makes the CLI enforce
the shape, which removes the failure mode rather than handling it.

Rejected: reusing the CLI's own tools (Bash, Write) to let it act directly. The harness
must own every side effect - that is what the workspace confinement and the artifact log
are for - so the CLI's tools are blocked *and* their schemas stripped.

## Dependencies

- **May import:** `pixibot.model`, `pixibot.lib.logbook`, stdlib (`subprocess`, `json`,
  `tempfile`, `shutil`, `re`, `pathlib`)
- **Must not import:** `pixibot.cli`, `pixibot.tui`, `pixibot.engine`, `pixibot.providers`
  — a model does not know who is calling it
- **Imported by:** `pixibot.providers` (lazily, for the `claude-cli` provider)

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `CliUnavailableError` | no CLI at the explicit path, on `PATH`, or at the Windows fallback | install Claude Code, or choose another provider |
| `CliFailedError` | the CLI reported `is_error`, exited non-zero with no output, or returned no JSON envelope | propagates to the agent loop, which marks the agent BLOCKED and keeps the run alive |
| `subprocess.TimeoutExpired` | a turn exceeded `timeout` (default 600s) | propagates; a hung CLI must not hang a run forever |

A model that answers with unusable JSON is **not** an error: the turn is an empty answer,
which the agent loop treats as a turn that did nothing.

## Observability

- **INFO** one line per turn: the model id, the number of tool calls, and the tokens the
  envelope reported. That is the line that shows a simulated run progressing, and the only
  place its cost is visible.
- **No WARNING or ERROR**: a failure here is an exception, and the agent loop logs it
  against a named agent, which is the context an operator needs.
- Never logged: the prompt, the rendered transcript, the system prompt, or the answer text.
  The prompt carries the agent's whole conversation.

## Tests

- every load-bearing flag is present in the argv, and no skip-permissions flag is
- the output schema passed to the CLI is `RESPONSE_SCHEMA`
- `--allowedTools` is empty and `--disallowedTools` names `mcp__*` and the acting tools
- the MCP config written for a turn is `{"mcpServers": {}}`
- a conversation renders with its tool calls and tool results intact, format last
- a structured result, and a fenced JSON string, both parse
- tool calls get ids, and set `stop_reason` to `tool_use`
- usage maps through, counting cache reads toward input
- a CLI error raises `CliFailedError`; so does output that is not JSON
- an unparseable result yields an empty answer rather than a crash
- a missing explicit CLI path raises; `available()` never raises
- `$PIXIBOT_CLAUDE_CLI` is honoured, and a bad value raises rather than being ignored
- every globbed Windows candidate exists
- the turn budget is at least 4, and is adjustable
- an error that still produced an answer keeps the answer
- `simulation` tier (excluded by default, skipped with no CLI): a real turn returns a
  tool call in our schema, and leaves no session transcript behind

## Deletion test

Deleting this module leaves two ways to exercise Pixibot: `MockModel`, which proves the
harness and nothing above it (incident 002 is exactly that gap), and a metered API key,
which makes every test cost money. It is the only path that puts a real, unscripted model
behind the harness at no API cost, which is what a simulated run needs to be evidence. It
earns its keep.
