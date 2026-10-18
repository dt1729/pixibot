"""Provider selection (DESIGN.md §8, decision 38): one place that knows which
models a provider offers.

A provider answers three questions and nothing else: which model plans the run
(the TPM), which model an agent of a given depth gets, and which cheap model
speaks for an agent in chat. Everything provider-specific — base URL, key
variable, depth -> model-id table, reasoning effort — lives in a ``_Profile``
record here, so adding a provider is one table entry.

Nothing here touches the network or imports a provider SDK: the ``Model`` objects
returned build their clients lazily, so an unconfigured provider is still
constructible and fails only when an agent actually runs.

Contract: docs/contracts/pixibot.providers.md
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .model import Model, ModelFactory
from .schema import DEPTHS

_DEFAULT_DEPTH = "senior"

# Depth -> reasoning effort (DESIGN.md §8/§9). Native Anthropic only; the
# OpenAI-compatible endpoints have no effort knob.
_DEPTH_EFFORT = {"junior": "medium", "senior": "high", "principal": "xhigh"}
_HARD_EFFORT = "xhigh"  # hard, long-horizon development runs at xhigh at every tier


class UnknownProviderError(ValueError):
    """Raised when a provider name is not one of NAMES."""


@runtime_checkable
class Provider(Protocol):
    """What a run needs from a provider: a planner, per-agent models, a spokesbot."""

    name: str
    label: str
    live: bool

    def tpm_model(self, objective: str = "") -> Model:
        """The planning model. ``objective`` is used only by the offline mock."""
        ...

    def model_factory(self, *, hard: bool = False) -> ModelFactory:
        """spec -> Model, reading the spec's budget depth (DESIGN.md decision 25)."""
        ...

    def spokesbot(self) -> Model | None:
        """The cheap read-only chat model, or None when there is no live chat."""
        ...


@dataclass(frozen=True)
class _Profile:
    """Everything that differs between live providers."""

    name: str
    label: str
    depth_models: Mapping[str, str]
    hard_model: str          # principal + hard development routes here
    spokesbot_model: str
    api_key_env: str
    base_url: str | None = None   # None = native Anthropic SDK
    tpm_max_tokens: int = 8192


_PROFILES: Mapping[str, _Profile] = {
    "anthropic": _Profile(
        name="anthropic",
        label="anthropic (Claude)",
        # DESIGN.md decision 25: junior = Sonnet 5, senior/principal = Opus 4.8.
        depth_models={"junior": "claude-sonnet-5", "senior": "claude-opus-4-8",
                      "principal": "claude-opus-4-8"},
        hard_model="claude-fable-5",
        spokesbot_model="claude-haiku-4-5",   # cheapest per token (§12)
        api_key_env="ANTHROPIC_API_KEY",
        tpm_max_tokens=32000,
    ),
    "gemini": _Profile(
        name="gemini",
        label="gemini (Google AI Studio)",
        depth_models={"junior": "gemini-2.5-flash", "senior": "gemini-2.5-flash",
                      "principal": "gemini-2.5-pro"},
        hard_model="gemini-2.5-pro",
        spokesbot_model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    "openrouter": _Profile(
        name="openrouter",
        label="openrouter",
        # One key, many providers. Edit these slugs to any OpenRouter model.
        depth_models={"junior": "google/gemini-2.5-flash",
                      "senior": "anthropic/claude-sonnet-4.5",
                      "principal": "anthropic/claude-opus-4.1"},
        hard_model="anthropic/claude-opus-4.1",
        spokesbot_model="google/gemini-2.5-flash",
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
    ),
}

# Auto-detection order is cost, not capability: where an unconfigured user should land.
_DETECT_ORDER = ("gemini", "openrouter", "anthropic")

#: Depth -> model alias for the CLI provider. Cheaper than the API tiers on
#: purpose: a simulated run is for exercising the harness, not for quality.
_CLI_DEPTH_MODELS = {"junior": "claude-haiku-4-5", "senior": "claude-sonnet-5",
                    "principal": "claude-opus-5"}
_CLI_HARD_MODEL = "claude-fable-5-1"
_CLI_SPOKESBOT_MODEL = "claude-haiku-4-5"

NAMES: tuple[str, ...] = (*_PROFILES, "claude-cli", "offline")


def _depth_of(spec: Mapping[str, Any]) -> str:
    depth = str(spec.get("budget", {}).get("depth", _DEFAULT_DEPTH))
    return depth if depth in DEPTHS else _DEFAULT_DEPTH


class _LiveProvider:
    """A real provider, driven entirely by its profile."""

    def __init__(self, profile: _Profile):
        self._p = profile
        self.name = profile.name
        self.label = profile.label
        self.live = True

    # -- model construction (lazy clients; no key is read here) --------------
    def _build(self, model_id: str, *, effort: str | None, max_tokens: int) -> Model:
        p = self._p
        if p.base_url is None:
            from .model import AnthropicModel
            return AnthropicModel(model_id, effort=effort, max_tokens=max_tokens)
        from .model import OpenAICompatModel
        return OpenAICompatModel(model_id, base_url=p.base_url,
                                 api_key_env=p.api_key_env, max_tokens=max_tokens)

    def _model_id(self, depth: str, *, hard: bool) -> str:
        if hard and depth == "principal":
            return self._p.hard_model
        return self._p.depth_models[depth]

    @staticmethod
    def _effort(depth: str, *, hard: bool) -> str:
        return _HARD_EFFORT if hard else _DEPTH_EFFORT[depth]

    # -- the interface ------------------------------------------------------
    def tpm_model(self, objective: str = "") -> Model:  # noqa: ARG002 - offline uses it
        return self._build(self._p.depth_models[_DEFAULT_DEPTH],
                           effort=_DEPTH_EFFORT[_DEFAULT_DEPTH],
                           max_tokens=self._p.tpm_max_tokens)

    def model_factory(self, *, hard: bool = False) -> ModelFactory:
        def factory(spec: dict[str, Any]) -> Model:
            depth = _depth_of(spec)
            return self._build(self._model_id(depth, hard=hard),
                               effort=self._effort(depth, hard=hard),
                               max_tokens=32000)
        return factory

    def spokesbot(self) -> Model | None:
        p = self._p
        if p.base_url is None:
            from .model import AnthropicModel
            # No caching: the spokesbot's system prompt embeds a live snapshot that
            # changes every call, so a cache write would never be read.
            return AnthropicModel(p.spokesbot_model, effort=None, use_thinking=False,
                                  max_tokens=1024, use_cache=False)
        from .model import OpenAICompatModel
        return OpenAICompatModel(p.spokesbot_model, base_url=p.base_url,
                                 api_key_env=p.api_key_env, max_tokens=1024)


class _CliProvider:
    """Models answered by the Claude Code CLI (`claude -p`).

    The point is the billing: the CLI runs against the operator's subscription,
    so a simulated multi-agent run spends no API credit. It is slower and
    non-deterministic, which is why it is chosen explicitly and never by
    :func:`detect`.
    """

    name = "claude-cli"
    label = "claude-cli (subscription, simulated)"
    live = True

    def tpm_model(self, objective: str = "") -> Model:  # noqa: ARG002 - offline uses it
        from .claude_cli import ClaudeCliModel
        return ClaudeCliModel(_CLI_DEPTH_MODELS[_DEFAULT_DEPTH])

    def model_factory(self, *, hard: bool = False) -> ModelFactory:
        from .claude_cli import ClaudeCliModel

        def factory(spec: dict[str, Any]) -> Model:
            depth = _depth_of(spec)
            model_id = (_CLI_HARD_MODEL if hard and depth == "principal"
                        else _CLI_DEPTH_MODELS[depth])
            return ClaudeCliModel(model_id)
        return factory

    def spokesbot(self) -> Model | None:
        from .claude_cli import ClaudeCliModel
        return ClaudeCliModel(_CLI_SPOKESBOT_MODEL)


class _OfflineProvider:
    """Scripted models: the full wiring runs deterministically without a key."""

    name = "offline"
    label = "offline (mock)"
    live = False

    def tpm_model(self, objective: str = "") -> Model:
        from .lib import mockrun
        return mockrun.mock_tpm_model(objective or "the requested feature")

    def model_factory(self, *, hard: bool = False) -> ModelFactory:  # noqa: ARG002
        from .lib import mockrun
        return mockrun.mock_model_factory()

    def spokesbot(self) -> Model | None:
        return None  # the caller shows the raw context snapshot instead


def get(name: str) -> Provider:
    """Resolve a provider name. Builds no client and reads no key."""
    if name == "offline":
        return _OfflineProvider()
    if name == "claude-cli":
        return _CliProvider()
    profile = _PROFILES.get(name)
    if profile is None:
        raise UnknownProviderError(f"unknown provider {name!r}; options: {', '.join(NAMES)}")
    return _LiveProvider(profile)


def detect(env: Mapping[str, str] | None = None) -> str:
    """The provider to use when the user did not choose one (cheapest key wins).

    `claude-cli` is never detected: it spends the operator's subscription rather
    than an API key, and that is a decision for a person to make explicitly.
    """
    environ = os.environ if env is None else env
    for name in _DETECT_ORDER:
        if environ.get(_PROFILES[name].api_key_env):
            return name
    return "offline"
