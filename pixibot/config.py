"""Static configuration for Pixibot (see DESIGN.md §8).

Depth is a budget dimension that turns three knobs at once: prompt scope
(handled by the agent's role prompt), reasoning effort, and model tier.
"""

# Depth -> model tier (DESIGN.md decision 25).
DEPTH_MODELS = {
    "junior": "claude-sonnet-5",
    "senior": "claude-opus-4-8",
    "principal": "claude-opus-4-8",   # escalate to PRINCIPAL_HARD_MODEL for the hardest work
}

# The hardest principal work can be routed to Anthropic's most capable model.
PRINCIPAL_HARD_MODEL = "claude-fable-5"

# Depth -> reasoning effort (DESIGN.md §8/§9).
DEPTH_EFFORT = {
    "junior": "medium",
    "senior": "high",
    "principal": "xhigh",
}

DEFAULT_MODEL = "claude-opus-4-8"


def model_for(depth: str, *, hard: bool = False) -> str:
    """Resolve the model id for a depth tier."""
    if depth == "principal" and hard:
        return PRINCIPAL_HARD_MODEL
    return DEPTH_MODELS.get(depth, DEFAULT_MODEL)


def effort_for(depth: str) -> str:
    """Resolve the reasoning effort for a depth tier."""
    return DEPTH_EFFORT.get(depth, "high")
