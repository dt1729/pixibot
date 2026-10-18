"""A Model backed by the Claude Code CLI (`claude -p`), for simulated runs.

Pixibot's unit suite drives `MockModel`, which accepts anything a test scripts.
That proves the harness and nothing above it: incident 002 exists because
mock-only tests missed the real message shapes. The other option was a metered
API key, which makes every test cost money.

This is the third option. `claude -p` answers one prompt and exits, and it bills
against the operator's Claude Code subscription rather than per token, so a
simulated multi-agent run costs no API credit. It is slower (seconds per turn)
and non-deterministic, so it belongs in an opt-in test tier - never in the unit
suite.

The invocation is taken from a working one (`infra-pivot`'s judge), and each
flag is load-bearing:

* ``--json-schema`` makes the CLI return a JSON object matching our schema, so
  a tool call is parsed rather than scraped out of prose.
* ``--system-prompt`` replaces Claude Code's own system prompt with the agent's.
  Without it the CLI behaves like a coding agent and argues with the format.
* ``--max-turns`` - with 1 the CLI ends in ``error_max_turns`` before it
  answers at all, and with 2 (enough for a one-shot judge) a real agent turn
  still ran out: the CLI spends a turn answering and may need another to fit the
  schema. Six, and adjustable.
* ``--allowedTools ""`` blocks tool calls; ``--disallowedTools`` additionally
  strips the schemas, which is most of the prompt.
* ``--restricted`` removes the built-in tools outright. Measured on an identical
  Pixibot turn, it takes total input from 65,782 tokens to 30,451 and the cost
  from $0.105 to $0.070. ``infra-pivot``'s judge skips it because it makes
  Claude Code ignore project settings, where that repo's hook lives; a simulated
  Pixibot agent runs in a temp directory with no hook to protect.
* ``CLAUDE_CODE_PROMPT_CACHE_TTL=1h`` keeps the prefix warm for a whole build, so
  the overhead is paid as cache reads rather than fresh input. A read refreshes
  the entry, which a two-hour clock experiment in that repo established.
* ``--mcp-config`` (empty) with ``--strict-mcp-config`` detaches every MCP
  server, so an operator's connectors cannot reach a simulated agent.
* the prompt arrives on **stdin**, so its size is not an argv limit.
* the run happens in a temp directory and its session transcript is purged, so
  the prompt does not outlive the call.

Contract: docs/contracts/pixibot.claude_cli.md
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .lib.logbook import get as _get_logger
from .model import (
    Message,
    Model,
    ModelResponse,
    SystemPrompt,
    TokenUsage,
    ToolCall,
    ToolDef,
)

_log = _get_logger("claude_cli")

#: Environment variable that names the CLI outright, tried before anything else.
CLI_ENV = "PIXIBOT_CLAUDE_CLI"
#: Where Windows installers put the binary. WSL reaches it through /mnt/c, and the
#: account name is globbed rather than guessed: the WSL user and the Windows user
#: are usually different people's spelling of the same person.
WINDOWS_GLOBS = (
    "/mnt/c/Users/*/AppData/Local/Microsoft/WinGet/Links/claude.exe",
    "/mnt/c/Users/*/AppData/Roaming/npm/claude.cmd",
    "/mnt/c/Program Files/Claude/claude.exe",
)
DEFAULT_TIMEOUT = 600
DEFAULT_MODEL = "claude-sonnet-5"
#: Turns the CLI may take to answer one Pixibot turn. One is too few to answer;
#: two was too few for an agent prompt (a live build blocked on error_max_turns).
DEFAULT_MAX_TURNS = 6
#: Longest prompt-cache TTL the plan offers. Turns in a build are seconds apart,
#: so the harness prefix stays warm and is read rather than rewritten.
CACHE_TTL = "1h"

#: Tools Claude Code offers that a simulated Pixibot agent must never have: it
#: acts through Pixibot's own tools, and anything here would act behind the
#: harness's back. Named explicitly, because --disallowedTools also strips their
#: schemas from the prompt.
#:
#: The second group was found by :meth:`ClaudeCliModel.probe_fence`, not by
#: reading the help: with --restricted on and the first group blocked, the init
#: event still listed all of them. Evidence over assumption.
ACTING_TOOLS = (
    "WebSearch", "WebFetch", "Bash", "PowerShell", "Read", "Write", "Edit", "NotebookEdit",
    "Glob", "Grep", "Agent", "Task", "Skill", "Workflow", "ToolSearch", "SendMessage",
    "TodoWrite", "CronCreate", "CronDelete", "CronList", "RemoteTrigger", "PushNotification",
    # found attached by the fence probe, 2026-10-18
    "DesignSync", "EnterWorktree", "ExitWorktree", "ListAgents", "ReportFindings",
    "ScheduleWakeup", "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop",
    "TaskUpdate",
)

#: The one tool that must stay: `--json-schema` returns our shape through it, so
#: blocking it breaks every turn. It writes nothing and reaches nothing.
REQUIRED_TOOLS = ("StructuredOutput",)

DISALLOWED_TOOLS = ",".join(("mcp__*", *ACTING_TOOLS))

#: The shape we ask the CLI to return. Pixibot speaks tool calls, so the schema
#: is the tool-call shape and nothing else.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "tool_calls"],
    "properties": {
        "text": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "input"],
                "properties": {"name": {"type": "string"}, "input": {"type": "object"}},
            },
        },
    },
}

_FORMAT = (
    "Return one JSON object with two keys. `text` is what you would say. "
    "`tool_calls` is the list of tools you want run now, each {name, input}; use "
    "an empty list when you are finished. You have no tools of your own here: the "
    "only way to act is to name one of the tools below in `tool_calls`."
)


class CliUnavailableError(RuntimeError):
    """The Claude Code CLI is not installed, or not where we were told to look."""


class CliFailedError(RuntimeError):
    """The CLI ran and did not return a usable answer (its own error, or no JSON)."""


def windows_candidates() -> list[str]:
    """Windows installations of the CLI that this machine can reach."""
    found: list[str] = []
    for pattern in WINDOWS_GLOBS:
        root, _, tail = pattern.partition("*")
        base = Path(root)
        if not base.is_dir():
            continue
        if not tail:
            found.append(pattern)
            continue
        found += [str(p) for p in sorted(base.glob("*" + tail)) if p.exists()]
    return found


def find_cli(explicit: str | None = None) -> str:
    """The CLI to run: an explicit path, then $PIXIBOT_CLAUDE_CLI, then PATH, then
    a Windows installation reachable from WSL.

    Raises rather than falling back to a mock: a simulated run that silently
    became a scripted one would be worthless as evidence.
    """
    for candidate in (explicit, os.environ.get(CLI_ENV)):
        if not candidate:
            continue
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
        raise CliUnavailableError(f"no Claude Code CLI at {candidate!r}")
    found = shutil.which("claude")
    if found:
        return found
    windows = windows_candidates()
    if windows:
        return windows[0]
    raise CliUnavailableError(
        f"no Claude Code CLI on PATH and none found under /mnt/c; install it, "
        f"pass cli=<path>, or set {CLI_ENV}")


def available(explicit: str | None = None) -> bool:
    """Whether a simulated run is possible here. Never raises."""
    try:
        find_cli(explicit)
    except CliUnavailableError:
        return False
    return True


def transcript_dir(cwd: str | Path) -> Path:
    """Where `claude -p` keeps a session transcript for a working directory."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def purge_transcript(cwd: str | Path) -> None:
    """Remove that transcript. The prompt carries a whole agent conversation, so
    the CLI's own copy must not outlive the call."""
    for spelling in {str(cwd), str(Path(cwd).resolve())}:
        shutil.rmtree(transcript_dir(spelling), ignore_errors=True)


def build_argv(  # noqa: PLR0913 - the parameters *are* the CLI's flags, one each
        cli: str, *, system: str, model: str, mcp_config: str,
        max_turns: int = DEFAULT_MAX_TURNS, restricted: bool = True,
        stream: bool = False) -> list[str]:
    """The argv for one zero-tool turn (pure, so the flag set is testable)."""
    argv = [
        cli, "-p",
        "--mcp-config", mcp_config, "--strict-mcp-config",
        "--permission-mode", "default",
        "--output-format", "stream-json" if stream else "json",
        "--max-turns", str(max_turns),
        "--allowedTools", "",
        "--disallowedTools", DISALLOWED_TOOLS,
        "--disable-slash-commands",
        "--system-prompt", system,
        "--json-schema", json.dumps(RESPONSE_SCHEMA),
        "--model", model,
    ]
    if stream:
        argv.append("--verbose")       # stream-json needs it to emit the init event
    if restricted:
        # Removes the built-in tools: 54% less input on a measured Pixibot turn.
        argv.append("--restricted")
    return argv


def child_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for a CLI turn: the longest cache TTL the plan offers."""
    env = dict(os.environ if environ is None else environ)
    env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = CACHE_TTL
    return env


def attached_tools(stdout: str) -> tuple[list[str], list[str]]:
    """(tools, mcp servers) the CLI reported in its init event.

    Read from `--output-format stream-json`, which announces what a session has
    before the model speaks. The model's own answer is never evidence: asked to
    list its tools it has named tools the init event proved absent. Returns empty
    lists when no init event was seen, so a caller fails closed.
    """
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and "tools" in event:
            return (list(event.get("tools") or []),
                    list(event.get("mcp_servers") or []))
    return [], []


def render_prompt(messages: Sequence[Message], tools: Sequence[ToolDef]) -> str:
    """One text prompt from a content-block conversation plus a tool list.

    The CLI takes text, not blocks, so the transcript is rendered as labelled
    turns. Tool results keep their tool name, because an agent's next move
    usually depends on what the last one returned.
    """
    lines: list[str] = []
    if tools:
        lines.append("TOOLS YOU MAY CALL")
        for tool in tools:
            schema = json.dumps(tool.get("input_schema", {}).get("properties", {}))
            lines.append(f"- {tool['name']}: {tool.get('description', '')} input={schema}")
        lines.append("")
    for message in messages:
        role = str(message.get("role", "user")).upper()
        content = message.get("content")
        if isinstance(content, str):
            lines.append(f"[{role}] {content}")
            continue
        for block in content or ():
            kind = block.get("type")
            if kind == "text":
                lines.append(f"[{role}] {block.get('text', '')}")
            elif kind == "tool_use":
                lines.append(f"[{role} called {block.get('name')}] "
                             f"{json.dumps(block.get('input', {}))[:2000]}")
            elif kind == "tool_result":
                body = block.get("content")
                text = body if isinstance(body, str) else json.dumps(body)
                lines.append(f"[TOOL RESULT] {text[:4000]}")
    lines.append("")
    lines.append(_FORMAT)
    return "\n".join(lines)


def parse_envelope(stdout: str) -> ModelResponse:
    """The CLI's JSON envelope as a ModelResponse.

    Raises `CliFailedError` when the CLI reported an error or returned no usable
    object: a simulated turn that silently becomes an empty answer would look
    like an agent choosing to do nothing.
    """
    try:
        envelope = json.loads(stdout)
    except ValueError as exc:
        raise CliFailedError(f"the CLI returned no JSON envelope: {stdout[:300]!r}") from exc
    result = envelope.get("result")
    payload = result if isinstance(result, dict) else _first_object(str(result or ""))
    if envelope.get("is_error"):
        # An error with a usable answer is still an answer: the CLI reports
        # error_max_turns when it needed one more turn to tidy up, and throwing
        # the work away turned that into a BLOCKED agent in a live build.
        if not payload:
            raise CliFailedError(f"the CLI reported an error: {envelope.get('subtype')} "
                                 f"{str(result)[:200]}")
        _log.warning("the CLI reported %s but returned an answer; using it",
                     envelope.get("subtype"))
    calls = [ToolCall(f"cli-{i}", str(c.get("name")), dict(c.get("input") or {}))
             for i, c in enumerate(payload.get("tool_calls") or [])
             if isinstance(c, dict) and c.get("name")]
    usage = envelope.get("usage") or {}
    cached = int(usage.get("cache_read_input_tokens", 0) or 0)
    created = int(usage.get("cache_creation_input_tokens", 0) or 0)
    return ModelResponse(
        text=str(payload.get("text") or ""),
        tool_calls=calls,
        stop_reason="tool_use" if calls else "end_turn",
        usage=TokenUsage(input=int(usage.get("input_tokens", 0) or 0) + cached + created,
                         output=int(usage.get("output_tokens", 0) or 0),
                         cached_input=cached),
    )


def _first_object(text: str) -> dict[str, Any]:
    """The first JSON object in some text, or {} - the CLI usually honours the
    schema, and a fenced block is the one shape it still wraps."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        found = json.loads(match.group())
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


Runner = Callable[[list[str], str], str]
"""argv and stdin -> the CLI's stdout. Substituted in tests."""


def _run(argv: list[str], prompt: str, *, cwd: str, timeout: int) -> str:
    proc = subprocess.run(argv, cwd=cwd, input=prompt, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, check=False,
                          env=child_env())
    if proc.returncode != 0 and not proc.stdout.strip():
        raise CliFailedError(f"the CLI exited {proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


class ClaudeCliModel(Model):
    """A Model that answers through `claude -p`. One process per turn.

    Slow and non-deterministic by nature, so it is for simulated runs and the
    opt-in test tier, never for the unit suite.
    """

    def __init__(self, model_id: str = DEFAULT_MODEL, *, cli: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT, max_turns: int = DEFAULT_MAX_TURNS,
                 restricted: bool = True, runner: Runner | None = None):
        self.model_id = model_id
        self.timeout = timeout
        self.max_turns = max_turns
        self.restricted = restricted
        self._cli = cli
        self._runner = runner      # tests pass a double; production resolves the CLI

    def _invoke(self, argv: list[str], prompt: str, cwd: str) -> str:
        if self._runner is not None:
            return self._runner(argv, prompt)
        return _run(argv, prompt, cwd=cwd, timeout=self.timeout)

    def generate(self, *, system: SystemPrompt, messages: list[Message],
                 tools: list[ToolDef]) -> ModelResponse:
        prompt = render_prompt(messages, tools)
        system_text = system if isinstance(system, str) else json.dumps(system)
        cli = self._cli if self._runner is not None else find_cli(self._cli)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                empty = Path(tmp) / "no_mcp.json"
                empty.write_text('{"mcpServers": {}}', encoding="utf-8")
                argv = build_argv(cli or "claude", system=system_text,
                                  model=self.model_id, mcp_config=str(empty),
                                  max_turns=self.max_turns,
                                  restricted=self.restricted)
                stdout = self._invoke(argv, prompt, tmp)
            finally:
                purge_transcript(tmp)
        response = parse_envelope(stdout)
        _log.info("cli turn model=%s tools=%d tokens=%d", self.model_id,
                  len(response.tool_calls), response.usage.total)
        return response

    def probe_fence(self) -> tuple[list[str], list[str]]:
        """What this configuration actually attaches: (tools, mcp servers).

        Runs one real turn with `stream-json` and reads the init event, so the
        fence is proven rather than trusted. Both lists empty is the pass.
        """
        cli = self._cli if self._runner is not None else find_cli(self._cli)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                empty = Path(tmp) / "no_mcp.json"
                empty.write_text('{"mcpServers": {}}', encoding="utf-8")
                argv = build_argv(cli or "claude", system="Reply with an empty tool list.",
                                  model=self.model_id, mcp_config=str(empty),
                                  max_turns=self.max_turns, restricted=self.restricted,
                                  stream=True)
                stdout = self._invoke(argv, "Say ok and call nothing.", tmp)
            finally:
                purge_transcript(tmp)
        return attached_tools(stdout)

    def stream_generate(self, *, system: SystemPrompt, messages: list[Message],
                        tools: list[ToolDef],
                        on_delta: Callable[[str], None] | None = None) -> ModelResponse:
        """No token streaming, and no thinking: `claude -p` returns one envelope,
        and a probe showed its thinking blocks arrive with no content. The whole
        text is emitted once, which is what the base Model does."""
        response = self.generate(system=system, messages=messages, tools=tools)
        if on_delta and response.text:
            on_delta(response.text)
        return response
