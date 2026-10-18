"""Default tool surface for reasoning agents (DESIGN.md §9/§11).

Agents affect the world through tools. With a workspace attached they write and
run **real files**; everything is also logged to the blackboard so the Observer
keeps a full audit trail. Tool *definitions* (the schema the model sees) are kept
apart from *implementations* (what the harness runs), so the harness can gate,
refuse and log without the model's cooperation.

Each implementation is a module-level function of ``(agent, blackboard, input)``
returning the text the model will read — including refusals, which are written to
be acted on rather than merely reported.

Contract: docs/contracts/pixibot.lib.tools.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..blackboard import KIND_ARTIFACT, KIND_CONTROL, Blackboard

if TYPE_CHECKING:
    from .agent import ReasoningAgent, ToolImpl

WRITE_ARTIFACT = {
    "name": "write_artifact",
    "description": "Create or overwrite a file in the shared workspace. 'section' is "
                   "the file path (e.g. 'reverse.py' or 'tests/test_reverse.py'); "
                   "'content' is the full file contents.",
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {"type": "string", "description": "File path within the workspace"},
            "content": {"type": "string", "description": "Full file contents"},
        },
        "required": ["section", "content"],
    },
}

READ_FILE = {
    "name": "read_file",
    "description": "Read a file from the workspace (e.g. one an earlier agent wrote).",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}

LIST_FILES = {
    "name": "list_files",
    "description": "List all files currently in the workspace.",
    "input_schema": {"type": "object", "properties": {}},
}

RUN_BASH = {
    "name": "run_bash",
    "description": "Run a shell command in the workspace (e.g. `python -m pytest`).",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}

SEND_MESSAGE = {
    "name": "send_message",
    "description": "Send a message to another agent by id (or '*' to broadcast).",
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["to", "content"],
    },
}

DEFAULT_TOOL_DEFS: list[dict[str, Any]] = [
    WRITE_ARTIFACT, READ_FILE, LIST_FILES, RUN_BASH, SEND_MESSAGE,
]

# Adoption D: a test-designer designs from the specification, so implementation
# reads are refused at the tool layer rather than discouraged in prose.
_BLIND_REFUSAL = ("refused: your role designs tests from the specification and the "
                  "architecture's declared interfaces, not the implementation. You may not "
                  "read implementation files — encode what the software SHOULD do.")

# A tool input that arrives without its required field is almost always a response
# truncated at the output limit, mid-JSON. Say so, because "retry smaller" is the
# recovery and the model cannot see its own truncation.
_TRUNCATED_WRITE = ("error: write_artifact needs both 'section' and 'content' — your tool "
                    "input looks TRUNCATED (cut off at the output limit). Retry with a "
                    "smaller file, writing one file per call.")
_TRUNCATED_BASH = ("error: run_bash needs a 'command' — your tool input looks TRUNCATED "
                   "(cut off at the output limit). Retry with a shorter command.")

_OUTPUT_CLIP = 4000  # command output handed back to the model


def _denies(agent: ReasoningAgent, target: str) -> bool:
    """True if `target` (a path, or a command naming one) hits the read denylist."""
    for pat in agent.read_denylist or ():
        p = pat.rstrip("/")
        if target in (pat, p) or target.startswith(p + "/") or p in target:
            return True
    return False


def write_artifact(agent: ReasoningAgent, bb: Blackboard, inp: dict[str, Any]) -> str:
    """Create or replace a file — in the workspace, or on the blackboard without one."""
    if not inp.get("section") or inp.get("content") is None:
        return _TRUNCATED_WRITE
    section, content = inp["section"], inp["content"]
    ws = agent.workspace
    if ws is not None:
        # The agent logs all file changes to the blackboard after its turn (so
        # run_bash-written files are captured too, exactly once).
        ws.write_file(section, content)
        return f"wrote {section} ({len(content)} chars)"
    bb.send(agent.agent_id, content, kind=KIND_ARTIFACT, section=section)
    return f"wrote {section} ({len(content)} chars) to blackboard"


def read_file(agent: ReasoningAgent, bb: Blackboard, inp: dict[str, Any]) -> str:
    """Read a workspace file, falling back to a blackboard artifact at that section."""
    path = inp["path"]
    if _denies(agent, path):
        return _BLIND_REFUSAL
    ws = agent.workspace
    content = ws.read_file(path) if ws is not None else None
    if content is None:
        ev = bb.read_section(path)
        content = ev.payload if ev else None
    return content if content is not None else f"(no file: {path})"


def list_files(agent: ReasoningAgent, bb: Blackboard, inp: dict[str, Any]) -> str:  # noqa: ARG001
    """Every file in the workspace, or every written section without one."""
    ws = agent.workspace
    files = ws.list_files() if ws is not None else list(bb.current_state())
    return "\n".join(files) if files else "(empty workspace)"


def run_bash(agent: ReasoningAgent, bb: Blackboard, inp: dict[str, Any]) -> str:
    """Run a command in the workspace; the transcript is kept on the blackboard."""
    if not inp.get("command"):
        return _TRUNCATED_BASH
    command = inp["command"]
    ws = agent.workspace
    if ws is None:
        return "(no workspace available to run commands)"
    if _denies(agent, command):
        return _BLIND_REFUSAL
    rc, out = ws.run(command)
    bb.send(agent.agent_id, f"$ {command}\n(exit {rc})\n{out}", kind=KIND_CONTROL)
    return f"(exit {rc})\n{out[:_OUTPUT_CLIP]}"


def send_message(agent: ReasoningAgent, bb: Blackboard, inp: dict[str, Any]) -> str:
    """Address another agent (or '*'); delivery is the scheduler's inbox (§3)."""
    bb.send(agent.agent_id, inp["content"], to=inp["to"])
    return f"sent message to {inp['to']}"


def default_tool_impls() -> dict[str, ToolImpl]:
    """name -> callable(agent, blackboard, input) -> result text."""
    return {
        "write_artifact": write_artifact,
        "read_file": read_file,
        "list_files": list_files,
        "run_bash": run_bash,
        "send_message": send_message,
    }
