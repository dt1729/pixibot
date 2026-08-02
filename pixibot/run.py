"""Top-level pipeline: strict input -> TPM -> MoA -> run -> Observer report.

``run_pipeline`` is provider-agnostic — pass a ``tpm_model`` and a
``model_factory``. Use MockModel for offline/tests; the ``anthropic_*`` helpers
wire real Claude (needs ``ANTHROPIC_API_KEY`` and the ``anthropic`` package).
"""

from __future__ import annotations

from typing import Callable

from . import config, observer, orchestrator, tpm
from .blackboard import Blackboard
from .context_manager import ContextManager
from .model import Model
from .schema import ValidationFailed, validate_input


def run_pipeline(
    inp: dict,
    *,
    tpm_model: Model,
    model_factory: Callable[[dict], Model],
    bb: Blackboard,
    max_steps: int = 100,
) -> dict:
    errors = validate_input(inp)
    if errors:
        raise ValidationFailed(errors)
    projection = tpm.plan(inp, tpm_model)
    cm = ContextManager(bb, max_steps=max_steps)
    orchestrator.materialize(projection, bb, cm, model_factory)
    orchestrator.kick(bb, projection)
    steps = cm.run()
    return {"projection": projection, "steps": steps, "report": observer.report(bb)}


# ── real-Claude wiring (lazy; needs ANTHROPIC_API_KEY) ───────────────────────
def anthropic_tpm_model() -> Model:
    from .model import AnthropicModel
    return AnthropicModel(config.DEFAULT_MODEL)


def make_anthropic_model_factory(hard: bool = False):
    """Per-agent model factory. Hard development routes principal -> Fable 5 at xhigh."""
    from .model import AnthropicModel

    def factory(spec: dict) -> Model:
        depth = spec.get("budget", {}).get("depth", "senior")
        return AnthropicModel(
            config.model_for(depth, hard=hard),
            effort=config.effort_for(depth, hard=hard),
        )
    return factory


def anthropic_model_factory(spec: dict) -> Model:
    return make_anthropic_model_factory(False)(spec)


def _demo() -> None:  # pragma: no cover - manual smoke run
    import json
    import os
    import tempfile

    from .model import MockModel, ModelResponse, ToolCall

    inp = {
        "objective": "Build a greeting library",
        "target": {"language": "python", "framework": "none", "platform": "linux"},
        "constraints": [], "non_goals": [], "acceptance_criteria": ["greet() returns a string"],
        "review_cadence": "per-feature",
        "budget_ceilings": {"compute": 100000, "max_depth": "senior", "scope": "one module"},
    }
    projection = {
        "plan_summary": "one feature: greet()",
        "breakdown": [{"id": "f1", "desc": "greet", "depends_on": []}],
        "blackboard_schema": {"sections": ["impl/f1"]},
        "agents": [{
            "id": "prog-f1", "role": "programmer",
            "budget": {"compute": 40000, "depth": "senior", "scope": "impl/f1"},
            "blackboard": {"reads": [], "writes": ["impl/f1"]},
            "activates_on": "message",
        }],
        "checkpoints": [{"id": "cp1", "after": ["prog-f1"], "demo": "import and call greet",
                         "gate": "user_feedback"}],
    }
    tpm_model = MockModel([ModelResponse(text=json.dumps(projection))])

    def factory(spec):
        section = spec["blackboard"]["writes"][0]
        return MockModel([
            ModelResponse(tool_calls=[ToolCall("t1", "write_artifact",
                          {"section": section, "content": "def greet(): return 'hi'"})],
                          stop_reason="tool_use"),
            ModelResponse(text="done", stop_reason="end_turn"),
        ])

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    bb = Blackboard(path, run_id="demo")
    result = run_pipeline(inp, tpm_model=tpm_model, model_factory=factory, bb=bb)
    print(result["report"])


if __name__ == "__main__":  # pragma: no cover
    _demo()
