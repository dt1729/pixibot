"""Standards injection (DESIGN.md §10, hybrid progressive disclosure).

A short *standards contract* goes into every agent's system prompt so the bar is
always present; the full markdown docs in ``standards/`` are read on demand.
"""

from __future__ import annotations

import os

_STD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "standards")


def standards_contract() -> str:
    """The always-in-prompt summary of the quality bar."""
    return (
        "Follow the project standards: clarity over cleverness; no premature "
        "abstraction; validate only at boundaries; test behavior, not "
        "implementation; report faithfully with evidence; do what was asked, "
        "then stop. Full standards live in standards/*.md — consult them when a "
        "decision needs the detail."
    )


def load_standard(name: str) -> str:
    """Read a full standard doc on demand, e.g. load_standard('lld.md')."""
    with open(os.path.join(_STD_DIR, name), encoding="utf-8") as f:
        return f.read()


def available_standards() -> list[str]:
    if not os.path.isdir(_STD_DIR):
        return []
    return sorted(n for n in os.listdir(_STD_DIR) if n.endswith(".md"))
