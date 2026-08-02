"""Strict input + projection schemas and validation (DESIGN.md §6, §7).

Pure-stdlib structural validation — no jsonschema dependency. Each validator
returns a list of human-readable error strings; an empty list means valid.
``obtain_valid`` implements the **bounded repair loop** (DESIGN.md §15 Q1
default): call a generator up to N times, feeding validation errors back in,
until it produces something valid or the budget is exhausted.
"""

from __future__ import annotations

from typing import Any, Callable

DEPTHS = {"junior", "senior", "principal"}


def _require(obj: dict, key: str, typ, errors: list[str], path: str):
    if key not in obj:
        errors.append(f"{path}: missing '{key}'")
        return None
    val = obj[key]
    if typ and not isinstance(val, typ):
        name = typ.__name__ if isinstance(typ, type) else "/".join(t.__name__ for t in typ)
        errors.append(f"{path}.{key}: expected {name}")
    return val


def validate_input(inp: Any) -> list[str]:
    """Validate a strict input request. Returns error strings ([] == valid)."""
    errors: list[str] = []
    if not isinstance(inp, dict):
        return ["input: must be an object"]
    _require(inp, "objective", str, errors, "input")
    target = _require(inp, "target", dict, errors, "input")
    if isinstance(target, dict):
        for k in ("language", "framework", "platform"):
            _require(target, k, str, errors, "input.target")
    for k in ("constraints", "non_goals", "acceptance_criteria"):
        _require(inp, k, list, errors, "input")
    bc = _require(inp, "budget_ceilings", dict, errors, "input")
    if isinstance(bc, dict):
        _require(bc, "compute", (int, float, str), errors, "input.budget_ceilings")
        md = _require(bc, "max_depth", str, errors, "input.budget_ceilings")
        if isinstance(md, str) and md not in DEPTHS:
            errors.append(
                f"input.budget_ceilings.max_depth: must be one of {sorted(DEPTHS)}"
            )
    return errors


def validate_projection(proj: Any) -> list[str]:
    """Validate a TPM projection. Returns error strings ([] == valid)."""
    errors: list[str] = []
    if not isinstance(proj, dict):
        return ["projection: must be an object"]
    _require(proj, "plan_summary", str, errors, "projection")
    agents = _require(proj, "agents", list, errors, "projection")
    if isinstance(agents, list):
        if not agents:
            errors.append("projection.agents: must be non-empty")
        seen: set[str] = set()
        for i, a in enumerate(agents):
            p = f"projection.agents[{i}]"
            if not isinstance(a, dict):
                errors.append(f"{p}: must be an object")
                continue
            aid = _require(a, "id", str, errors, p)
            if isinstance(aid, str):
                if aid in seen:
                    errors.append(f"{p}.id: duplicate '{aid}'")
                seen.add(aid)
            _require(a, "role", str, errors, p)
            budget = _require(a, "budget", dict, errors, p)
            if isinstance(budget, dict):
                depth = _require(budget, "depth", str, errors, f"{p}.budget")
                if isinstance(depth, str) and depth not in DEPTHS:
                    errors.append(
                        f"{p}.budget.depth: must be one of {sorted(DEPTHS)}"
                    )
                _require(budget, "scope", str, errors, f"{p}.budget")
    _require(proj, "checkpoints", list, errors, "projection")
    return errors


class ValidationFailed(Exception):
    """Raised when the repair loop exhausts its attempts."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors) or "validation failed")
        self.errors = errors


def obtain_valid(
    generate: Callable[[list[str]], Any],
    validate: Callable[[Any], list[str]],
    *,
    max_attempts: int = 3,
) -> Any:
    """Bounded repair loop: regenerate with error feedback until valid.

    ``generate(errors)`` produces a candidate given the previous attempt's
    errors ([] on the first attempt). Raises ``ValidationFailed`` if no valid
    candidate appears within ``max_attempts``.
    """
    errors: list[str] = []
    for _ in range(max_attempts):
        candidate = generate(errors)
        errors = validate(candidate)
        if not errors:
            return candidate
    raise ValidationFailed(errors)
