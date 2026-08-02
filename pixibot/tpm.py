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
    "You are the TPM planning agent for Pixibot. Break the request into features "
    "and emit ONLY a single JSON projection object (no prose, no code fences) with "
    "keys: plan_summary, breakdown, blackboard_schema, agents, checkpoints. Each "
    "agent has: id, role, budget{compute,depth,scope}, blackboard{reads,writes}, "
    "activates_on. depth is one of junior|senior|principal."
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
