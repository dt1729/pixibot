"""Checkpoints: where a run stops and asks a human (DESIGN.md §10, decisions 12/33).

A projection declares checkpoints — "after these agents finish, here is the demo,
and the gate is user feedback". Termination is human-gated, so this is the only
place a build is allowed to end on someone's judgment rather than on quiescence.

The order is fixed and not configurable: **mechanical gate first, demo second.**
A red suite never reaches a person, because their attention is the expensive part.

This module holds the part that is a function of the plan — which checkpoints are
due, and what a review decided. Driving them is :class:`pixibot.engine.Engine`.

Contract: docs/contracts/pixibot.checkpoints.md
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The gate value that means "a person decides" (anything else is mechanical-only).
HUMAN_GATE = "user_feedback"


@dataclass(frozen=True)
class Checkpoint:
    """One declared stopping point in a projection."""

    id: str
    demo: str                       # what to show, in the TPM's words
    after: tuple[str, ...] = ()     # agents that must be DONE first
    gate: str = HUMAN_GATE

    @property
    def human(self) -> bool:
        return self.gate == HUMAN_GATE


@dataclass(frozen=True)
class Review:
    """A human's verdict on a demo."""

    accepted: bool
    feedback: str = ""

    @classmethod
    def accept(cls) -> Review:
        return cls(accepted=True)

    @classmethod
    def revise(cls, feedback: str) -> Review:
        return cls(accepted=False, feedback=feedback)


#: handler(checkpoint, gate_summary) -> Review. Blocking is the caller's choice.
Reviewer = Callable[[Checkpoint, str], Review]


def parse(projection: Mapping[str, Any]) -> tuple[Checkpoint, ...]:
    """Read the projection's checkpoints, tolerating what the TPM leaves out.

    A checkpoint with no ``id`` is numbered by position; a missing ``demo`` keeps
    the run honest by saying so rather than inventing one.
    """
    out = []
    for i, raw in enumerate(projection.get("checkpoints") or ()):
        if not isinstance(raw, dict):
            continue
        after = raw.get("after") or ()
        out.append(Checkpoint(
            id=str(raw.get("id") or f"cp{i + 1}"),
            demo=str(raw.get("demo") or "(no demo described)"),
            after=tuple(str(a) for a in after) if isinstance(after, Sequence)
            and not isinstance(after, str) else (str(after),),
            gate=str(raw.get("gate") or HUMAN_GATE),
        ))
    return tuple(out)


def due(checkpoints: Iterable[Checkpoint], completed: Iterable[str],
        *, already_passed: Iterable[str] = ()) -> tuple[Checkpoint, ...]:
    """The checkpoints whose agents have all finished and which have not been passed.

    A checkpoint with no ``after`` is due as soon as the run quiesces: the TPM
    left the trigger open, and the honest reading is "at the end".
    """
    done = set(completed)
    passed = set(already_passed)
    return tuple(cp for cp in checkpoints
                 if cp.id not in passed and set(cp.after) <= done)
