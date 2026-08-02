"""Observer: reconstruct the message DAG and produce a review report (DESIGN.md §11).

Structural analysis is deterministic (no model needed): every event carries
from/to and an optional in_reply_to, so the communication graph — and thus "why
did this decision survive" — is a literal graph over the immutable log. A richer
narrative can be layered on with a model later.
"""

from __future__ import annotations

from collections import Counter

from .blackboard import BROADCAST, Blackboard, Event


def dag_edges(events: list[Event]) -> list[tuple[str, str, int]]:
    """Directed message edges (from_agent, to_agent, event_id).

    Every addressed (non-broadcast) event contributes a sender -> recipient
    edge — the base communication graph. Each event still carries
    ``in_reply_to`` for deeper causal-chain analysis when needed.
    """
    edges: list[tuple[str, str, int]] = []
    for e in events:
        if e.to_agent and e.to_agent != BROADCAST:
            edges.append((e.from_agent, e.to_agent, e.id))
    return edges


def _node(name: str) -> str:
    return name.replace("-", "_").replace("*", "broadcast")


def mermaid(events: list[Event]) -> str:
    lines = ["flowchart TD"]
    for src, dst, _ in dag_edges(events):
        lines.append(f"    {_node(src)} --> {_node(dst)}")
    return "\n".join(lines)


def report(bb: Blackboard) -> str:
    """A markdown run report: activity, artifacts, and the message DAG."""
    events = bb.history()
    by_agent = Counter(e.from_agent for e in events)
    artifacts = bb.current_state()

    lines = ["# Pixibot run report", ""]
    lines.append(f"- events: {len(events)}")
    lines.append(f"- artifacts: {len(artifacts)}")
    lines += ["", "## Activity by agent"]
    for agent, count in by_agent.most_common():
        lines.append(f"- {agent}: {count} events")
    lines += ["", "## Artifacts"]
    for section in sorted(artifacts):
        lines.append(f"- `{section}` (by {artifacts[section].from_agent})")
    lines += ["", "## Message DAG", "```mermaid", mermaid(events), "```"]
    return "\n".join(lines)
