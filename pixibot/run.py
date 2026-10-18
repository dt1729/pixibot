"""Top-level pipeline: strict input -> TPM -> MoA -> run -> Observer report.

``run_pipeline`` is provider-agnostic: pass a ``tpm_model`` and a
``model_factory``, both of which :mod:`pixibot.providers` will hand you for any
provider name (``providers.get("offline")`` needs no API key).

    p = providers.get(providers.detect())
    run_pipeline(inp, tpm_model=p.tpm_model(), model_factory=p.model_factory(),
                 bb=Blackboard("pixibot.db"))

For a persistent run you can steer and revise, use :class:`pixibot.engine.Engine`
instead; this function is the one-shot form.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import observer, orchestrator, tpm
from .blackboard import Blackboard
from .context_manager import ContextManager
from .model import Model
from .schema import ValidationFailed, validate_input


def run_pipeline(
    inp: dict[str, Any],
    *,
    tpm_model: Model,
    model_factory: Callable[[dict[str, Any]], Model],
    bb: Blackboard,
    max_steps: int = 100,
) -> dict[str, Any]:
    """Plan, materialize and drive one run to quiescence; return its report."""
    errors = validate_input(inp)
    if errors:
        raise ValidationFailed(errors)
    projection = tpm.plan(inp, tpm_model)
    cm = ContextManager(bb, max_steps=max_steps)
    orchestrator.materialize(projection, cm, model_factory)
    orchestrator.kick(bb, projection)
    steps = cm.run()
    return {"projection": projection, "steps": steps, "report": observer.report(bb)}
