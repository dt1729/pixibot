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

# Cheapest per-token model — used for the read-only user<->agent spokesbots (§12).
HAIKU_MODEL = "claude-haiku-4-5"
SPOKESBOT_MODEL = HAIKU_MODEL


def model_for(depth: str, *, hard: bool = False) -> str:
    """Resolve the model id for a depth tier."""
    if depth == "principal" and hard:
        return PRINCIPAL_HARD_MODEL
    return DEPTH_MODELS.get(depth, DEFAULT_MODEL)


def effort_for(depth: str, *, hard: bool = False) -> str:
    """Resolve the reasoning effort for a depth tier.

    Hard, long-horizon development runs at ``xhigh`` (the sweet spot for agentic
    coding) regardless of tier.
    """
    if hard:
        return "xhigh"
    return DEPTH_EFFORT.get(depth, "high")


# ── OpenAI-compatible providers (Gemini / OpenRouter / Groq / Ollama) ─────────
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEPTH_MODELS = {
    "junior": "gemini-2.5-flash",
    "senior": "gemini-2.5-flash",
    "principal": "gemini-2.5-pro",
}
GEMINI_SPOKESBOT_MODEL = "gemini-2.5-flash"


def gemini_model_for(depth: str, *, hard: bool = False) -> str:
    if hard and depth == "principal":
        return "gemini-2.5-pro"
    return GEMINI_DEPTH_MODELS.get(depth, "gemini-2.5-flash")


# OpenRouter (one key -> many providers). Edit these slugs to any OpenRouter model.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEPTH_MODELS = {
    "junior": "google/gemini-2.5-flash",
    "senior": "anthropic/claude-sonnet-4.5",
    "principal": "anthropic/claude-opus-4.1",
}
OPENROUTER_SPOKESBOT_MODEL = "google/gemini-2.5-flash"


def openrouter_model_for(depth: str, *, hard: bool = False) -> str:
    if hard and depth == "principal":
        return "anthropic/claude-opus-4.1"
    return OPENROUTER_DEPTH_MODELS.get(depth, "google/gemini-2.5-flash")
