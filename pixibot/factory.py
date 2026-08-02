"""Agent factory: build a ReasoningAgent's system prompt from role + depth.

Depth shapes the prompt (which abstraction layers to reason about); the caller
separately picks the model tier and effort for that depth (see config.py). The
standards contract is injected here so the quality bar is always present.
"""

from __future__ import annotations

from typing import Optional

from .agent import ReasoningAgent
from .model import Model
from .standards import standards_contract

ROLE_BRIEFS = {
    "topology": "You decide overall structure, framework/library choices, and the "
                "interfaces (handshakes) between features. Write them down for others.",
    "lld": "You produce the low-level design for a single feature: interfaces, "
           "data models, and contracts, before any code is written against them.",
    "programmer": "You implement clean, correct code: sound algorithms, good library "
                  "choices, no premature abstraction.",
    "tester": "You validate every interface and integration point, ask for the hooks "
              "you need, and write thorough tests including the error paths.",
}

DEPTH_BRIEFS = {
    "junior": "Reason at the implementation layer.",
    "senior": "Reason across design and tradeoffs as well as implementation.",
    "principal": "Reason across architecture, cross-cutting concerns, and longevity; "
                 "question the requirement itself when warranted.",
}


def build_system_prompt(role: str, depth: str) -> str:
    return "\n\n".join(
        p for p in (
            f"You are a {depth} {role} agent in the Pixibot multi-agent system.",
            ROLE_BRIEFS.get(role, "You are a specialist agent."),
            DEPTH_BRIEFS.get(depth, ""),
            standards_contract(),
            "Use write_artifact to record work in your scope; use send_message to "
            "hand off to or ask a peer. Do what was asked, then stop.",
        ) if p
    )


def make_agent(
    agent_id: str,
    *,
    role: str,
    depth: str,
    model: Model,
    scope: Optional[str] = None,
    reads=(),
    **kwargs,
) -> ReasoningAgent:
    return ReasoningAgent(
        agent_id,
        model,
        system_prompt=build_system_prompt(role, depth),
        role=role,
        depth=depth,
        scope=scope,
        reads=tuple(reads),
        **kwargs,
    )
