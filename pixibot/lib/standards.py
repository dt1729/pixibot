"""Standards injection (DESIGN.md §10, hybrid progressive disclosure).

A short *standards contract* goes into every agent's system prompt so the bar is
always present; the full markdown docs in ``standards/`` are read on demand.
"""

from __future__ import annotations

from pathlib import Path

_STD_DIR = Path(__file__).resolve().parent.parent / "standards"


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
    return (_STD_DIR / name).read_text(encoding="utf-8")


def available_standards() -> list[str]:
    if not _STD_DIR.is_dir():
        return []
    return sorted(p.name for p in _STD_DIR.iterdir() if p.suffix == ".md")
