"""Default tool surface for reasoning agents.

Tools are how an agent affects the world. In v0 an agent can write artifacts to
its scope and message other agents — both land on the blackboard as events, so
everything is auditable by the Observer. Tool *definitions* (the schema the
model sees) are separated from *implementations* (what the harness runs), so the
harness can gate, log, and scope-check each call.
"""

from __future__ import annotations

from .blackboard import KIND_ARTIFACT

WRITE_ARTIFACT = {
    "name": "write_artifact",
    "description": "Write a work product (code, design, tests) to a blackboard "
                   "section you own. Overwrites the section's current value.",
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {"type": "string", "description": "Blackboard section, e.g. impl/f1"},
            "content": {"type": "string", "description": "The full content to store"},
        },
        "required": ["section", "content"],
    },
}

SEND_MESSAGE = {
    "name": "send_message",
    "description": "Send a message to another agent by id (or '*' to broadcast).",
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient agent id, or '*'"},
            "content": {"type": "string", "description": "Message body"},
        },
        "required": ["to", "content"],
    },
}

DEFAULT_TOOL_DEFS = [WRITE_ARTIFACT, SEND_MESSAGE]


def default_tool_impls() -> dict:
    """Return name -> callable(agent, blackboard, input) -> result string."""

    def write_artifact(agent, bb, inp):
        bb.send(agent.agent_id, inp["content"], kind=KIND_ARTIFACT, section=inp["section"])
        return f"wrote {inp['section']} ({len(inp['content'])} chars)"

    def send_message(agent, bb, inp):
        bb.send(agent.agent_id, inp["content"], to=inp["to"])
        return f"sent message to {inp['to']}"

    return {"write_artifact": write_artifact, "send_message": send_message}
