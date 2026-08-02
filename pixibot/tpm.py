"""TPM planning agent: strict input -> validated projection (DESIGN.md §7).

The TPM asks the model for a projection JSON, then runs it through the bounded
repair loop (schema.obtain_valid): invalid output is fed back with the concrete
validation errors until it validates or the attempt budget is spent.
"""

from __future__ import annotations

import json
from typing import Any

from .model import Model
from .schema import obtain_valid, validate_projection

TPM_SYSTEM = (
    "You are the TPM planning agent for Pixibot, which builds real software with a team "
    "of agents working in a shared filesystem workspace. Break the request into features "
    "and a team, then emit ONLY a single JSON projection object (no prose, no code fences).\n\n"
    "Keys: plan_summary, breakdown, blackboard_schema, agents, checkpoints.\n"
    "Each agent: {id, role, depends_on (list of agent ids that must finish before it runs), "
    "budget:{compute, depth, scope}, blackboard:{reads, writes}, activates_on}.\n"
    "  • role is one of: topology, lld, programmer, tester, reviewer.\n"
    "  • depth is one of: junior | senior | principal.\n"
    "  • blackboard.writes are concrete workspace file paths (e.g. 'reverse.py', "
    "'tests/test_reverse.py', 'ARCHITECTURE.md').\n\n"
    "Design a real pipeline with depends_on: a topology/architect agent first, then "
    "programmer(s), then a tester that runs the code, optionally a reviewer. Keep the team "
    "as small as the task needs."
)


def _parse_json(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start:end + 1])


def _prompt(inp: dict, errors: list[str]) -> str:
    msg = "Produce the projection JSON for this request:\n" + json.dumps(inp, indent=2)
    if errors:
        msg += ("\n\nYour previous attempt was invalid. Fix exactly these errors "
                "and return the full corrected JSON:\n- " + "\n- ".join(errors))
    return msg


def plan(inp: dict, model: Model, *, max_repairs: int = 3) -> dict:
    def generate(errors: list[str]) -> Any:
        resp = model.generate(
            system=TPM_SYSTEM,
            messages=[{"role": "user", "content": _prompt(inp, errors)}],
            tools=[],
        )
        return _parse_json(resp.text)

    return obtain_valid(generate, validate_projection, max_attempts=max_repairs)


REVISE_SYSTEM = TPM_SYSTEM + (
    " You are revising an existing projection based on user feedback on a demo. "
    "Keep what works, change what the feedback asks for, and return the full "
    "updated projection JSON (all agents, including unchanged ones)."
)


def revise(prior: dict, feedback: str, model: Model, *, max_repairs: int = 3) -> dict:
    """Re-plan from a prior projection + demo feedback (DESIGN.md §15 revision)."""
    def _revise_prompt(errors: list[str]) -> str:
        msg = ("Revise this projection based on the feedback. Return the full "
               "updated JSON.\n\nPrior projection:\n" + json.dumps(prior, indent=2) +
               "\n\nFeedback:\n" + feedback)
        if errors:
            msg += "\n\nFix these validation errors:\n- " + "\n- ".join(errors)
        return msg

    def generate(errors: list[str]) -> Any:
        resp = model.generate(
            system=REVISE_SYSTEM,
            messages=[{"role": "user", "content": _revise_prompt(errors)}],
            tools=[],
        )
        return _parse_json(resp.text)

    return obtain_valid(generate, validate_projection, max_attempts=max_repairs)
