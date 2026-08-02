"""Default tool surface for reasoning agents (DESIGN.md §9/§11).

Agents affect the world through tools. With a workspace attached they write and
run **real files**; everything is also logged to the blackboard so the Observer
keeps a full audit trail. Tool *definitions* (schema the model sees) are separate
from *implementations* (what the harness runs), so the harness can gate and log.
"""

from __future__ import annotations

from .blackboard import KIND_ARTIFACT, KIND_CONTROL

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

DEFAULT_TOOL_DEFS = [WRITE_ARTIFACT, READ_FILE, LIST_FILES, RUN_BASH, SEND_MESSAGE]


def default_tool_impls() -> dict:
    """Return name -> callable(agent, blackboard, input) -> result string."""

    def write_artifact(agent, bb, inp):
        ws = getattr(agent, "workspace", None)
        if ws is not None:
            # Write the real file; the agent logs all file changes to the blackboard
            # after its turn (so run_bash-written files are captured too, once).
            ws.write_file(inp["section"], inp["content"])
            return f"wrote {inp['section']} ({len(inp['content'])} chars)"
        bb.send(agent.agent_id, inp["content"], kind=KIND_ARTIFACT, section=inp["section"])
        return f"wrote {inp['section']} ({len(inp['content'])} chars) to blackboard"

    def read_file(agent, bb, inp):
        ws = getattr(agent, "workspace", None)
        content = ws.read_file(inp["path"]) if ws is not None else None
        if content is None:  # fall back to a blackboard artifact at that section
            ev = bb.read_section(inp["path"])
            content = ev.payload if ev else None
        return content if content is not None else f"(no file: {inp['path']})"

    def list_files(agent, bb, inp):
        ws = getattr(agent, "workspace", None)
        files = ws.list_files() if ws is not None else list(bb.current_state())
        return "\n".join(files) if files else "(empty workspace)"

    def run_bash(agent, bb, inp):
        ws = getattr(agent, "workspace", None)
        if ws is None:
            return "(no workspace available to run commands)"
        rc, out = ws.run(inp["command"])
        bb.send(agent.agent_id, f"$ {inp['command']}\n(exit {rc})\n{out}", kind=KIND_CONTROL)
        return f"(exit {rc})\n{out[:4000]}"

    def send_message(agent, bb, inp):
        bb.send(agent.agent_id, inp["content"], to=inp["to"])
        return f"sent message to {inp['to']}"

    return {
        "write_artifact": write_artifact,
        "read_file": read_file,
        "list_files": list_files,
        "run_bash": run_bash,
        "send_message": send_message,
    }
