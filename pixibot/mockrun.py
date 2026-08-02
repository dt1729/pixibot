"""Offline mock scaffolding for the CLI and demo — a multi-agent scenario.

Provides a canned 3-agent projection (topology -> programmer -> tester) and
scripted per-agent models, so the full wiring (dependency handshakes, directive
resume, revision) can be exercised deterministically without an API key.
"""

from __future__ import annotations

import json

from .model import MockModel, ModelResponse, ToolCall


def _w(section: str, content: str) -> ModelResponse:
    return ModelResponse(
        tool_calls=[ToolCall("t", "write_artifact", {"section": section, "content": content})],
        stop_reason="tool_use",
    )


_END = ModelResponse(text="done", stop_reason="end_turn")


def build_projection(objective: str = "the requested feature") -> dict:
    return {
        "plan_summary": f"Plan for: {objective}",
        "breakdown": [{"id": "f1", "desc": objective, "depends_on": []}],
        "blackboard_schema": {"sections": ["topology", "impl/f1", "tests/f1"]},
        "agents": [
            {"id": "topo", "role": "topology",
             "budget": {"compute": 40000, "depth": "principal", "scope": "topology"},
             "blackboard": {"reads": [], "writes": ["topology"]}, "activates_on": "message"},
            {"id": "prog-f1", "role": "programmer",
             "budget": {"compute": 40000, "depth": "senior", "scope": "impl/f1"},
             "blackboard": {"reads": ["topology"], "writes": ["impl/f1"]}, "activates_on": "message"},
            {"id": "test-f1", "role": "tester",
             "budget": {"compute": 40000, "depth": "senior", "scope": "tests/f1"},
             "blackboard": {"reads": ["impl/f1"], "writes": ["tests/f1"]}, "activates_on": "message"},
        ],
        "checkpoints": [{"id": "cp1", "after": ["test-f1"], "demo": "run tests", "gate": "user_feedback"}],
    }


def revised_projection(objective: str = "the requested feature") -> dict:
    proj = build_projection(objective)
    proj["plan_summary"] = f"(revised) {objective}"
    proj["agents"].append(
        {"id": "docs-f1", "role": "lld",
         "budget": {"compute": 20000, "depth": "junior", "scope": "docs/f1"},
         "blackboard": {"reads": ["impl/f1"], "writes": ["docs/f1"]}, "activates_on": "message"}
    )
    return proj


def mock_tpm_model(objective: str = "the requested feature") -> MockModel:
    # First generate -> build projection; second -> revised projection.
    return MockModel([
        ModelResponse(text=json.dumps(build_projection(objective))),
        ModelResponse(text=json.dumps(revised_projection(objective))),
    ])


def mock_model_factory():
    scripts = {
        "topo": [_w("topology", "microservices: api gateway + key/value store"), _END],
        "prog-f1": [
            _w("impl/f1", "def shorten(url): ...  # v1"), _END,
            _w("impl/f1", "@dataclass\nclass Link: ...  # v2 (dataclass, per directive)"), _END,
        ],
        "test-f1": [
            _w("tests/f1", "1 test passing"), _END,
            _w("tests/f1", "2 tests passing"), _END,
        ],
        "docs-f1": [_w("docs/f1", "# API docs\nPOST /shorten -> {code}"), _END],
    }

    def factory(spec: dict) -> MockModel:
        return MockModel(list(scripts.get(spec["id"], [_END])))
    return factory
