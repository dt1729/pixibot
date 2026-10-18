"""Tab completion for the Pixibot shell — what the next word could be.

Kept out of the TUI because it has nothing to do with rendering: completion is a
function of the command table, the workspace tree and the live agent ids. Out
here it is testable without a terminal (or without ``textual`` installed at all),
which is how the CLI's completion acquired tests at all.

Contract: docs/contracts/pixibot.completion.md
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

#: Commands whose argument is a workspace path.
PATH_COMMANDS = ("cd", "cat", "ls", "tree")
#: Commands whose argument is an agent id.
AGENT_COMMANDS = ("ask", "tell", "think")


@dataclass(frozen=True)
class Sources:
    """Where candidates come from. Callables, so nothing is read until needed."""

    commands: Iterable[str]
    agent_ids: Callable[[], list[str]]
    listdir: Callable[[str], list[str] | None]
    cwd: str = ""


def common_prefix(strings: list[str]) -> str:
    """The longest prefix every string shares (bash's partial completion)."""
    if not strings:
        return ""
    lo, hi = min(strings), max(strings)
    for i, ch in enumerate(lo):
        if ch != hi[i]:
            return lo[:i]
    return lo


def path_candidates(fragment: str, src: Sources) -> list[str]:
    """Workspace entries matching `fragment`, relative to the shell's cwd."""
    directory = str(PurePosixPath(fragment).parent) if "/" in fragment else ""
    directory = "" if directory == "." else directory
    look = f"{src.cwd}/{directory}" if (src.cwd and directory) else (src.cwd or directory)
    entries = src.listdir(look) or []
    leaf = PurePosixPath(fragment).name
    return sorted(f"{directory}/{e}" if directory else e
                  for e in entries if e.startswith(leaf))


def complete(text: str, src: Sources) -> tuple[str, list[str]]:
    """Return (text with the completion applied, all candidates).

    The first word completes against the command table; later words complete by
    what that command takes — a workspace path, an agent id, or nothing. A single
    candidate is applied with a trailing space; several apply their shared prefix,
    and the caller decides whether to list them.
    """
    parts = text.split(" ")
    if len(parts) <= 1:
        candidates = sorted(c for c in src.commands if c.startswith(text))
        prefix = ""
    else:
        command, fragment = parts[0].lower(), parts[-1]
        prefix = text[: len(text) - len(fragment)]
        if command in PATH_COMMANDS:
            candidates = path_candidates(fragment, src)
        elif command in AGENT_COMMANDS:
            candidates = sorted(a for a in src.agent_ids() if a.startswith(fragment))
        else:
            candidates = []
    if not candidates:
        return text, []
    applied = (candidates[0] + " ") if len(candidates) == 1 else common_prefix(candidates)
    return prefix + applied, candidates
