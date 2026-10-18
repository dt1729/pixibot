# Contract: pixibot.providers

**Status:** accepted
**Depth:** senior

## Responsibility

Resolve a provider name into the three models a run needs (planner, per-agent, spokesbot).

## Interface

```python
NAMES: tuple[str, ...]            # ("anthropic", "gemini", "openrouter", "offline")

class Provider(Protocol):
    name: str                     # the key callers pass to get()
    label: str                    # human label for banners ("gemini (Google AI Studio)")
    live: bool                    # False only for the offline mock

    def tpm_model(self, objective: str = "") -> Model: ...
    def model_factory(self, *, hard: bool = False) -> Callable[[dict], Model]: ...
    def spokesbot(self) -> Model | None: ...

def get(name: str) -> Provider
def detect(env: Mapping[str, str] | None = None) -> str
class UnknownProviderError(ValueError)
```

- **No network, no key, no SDK import** happens in `get()` or in any of the three
  methods: they construct `Model` objects whose clients are lazy. A provider for a key
  you do not have is therefore constructible, and fails only when an agent runs.
- `model_factory` returns a callable taking an **agent spec** (the projection's agent
  dict) and reading `spec["budget"]["depth"]`; an unknown or missing depth resolves to
  `senior`, never raises.
- `hard=True` routes **principal** agents to the provider's strongest model, and (native
  Anthropic only) raises reasoning effort to `xhigh` for every tier — DESIGN.md §8.
- `objective` is used **only** by the offline provider, whose canned projection names it;
  live providers ignore it. It is a parameter rather than two interfaces because callers
  switch providers at runtime (`provider <name>`) and must not branch on which they hold.
- `spokesbot()` returns `None` for the offline provider, which is the documented signal
  for "no live chat"; callers show the raw context snapshot instead.
- `detect()` reads the environment and prefers the cheapest usable key:
  `GEMINI_API_KEY` → `OPENROUTER_API_KEY` → `ANTHROPIC_API_KEY` → `"offline"`. Ordering
  is cost, not capability: it is what an unconfigured user should land on.

## Seam

The seam is `Provider`, and what varies across it is **how models are built**, not which
ids they use. Two implementations exist, which is why it is a Protocol and not a dataclass
with a flag:

- a table-driven live provider over a `_Profile` record (anthropic, gemini, openrouter —
  three profiles, one implementation), and
- the offline provider, which hands back scripted `MockModel`s and no spokesbot.

Adding a fourth live provider is one `_Profile` entry: a base URL, a key env var, a
depth → model-id table and a spokesbot id. Nothing else in the package changes.

Rejected: a `Provider` dataclass carrying three callables. It makes every provider a
construction site and loses the one place a reader can see what a provider *is*.
Rejected: subclassing per live provider. The three differ only in data, so three classes
would be ceremony around one table.

## Dependencies

- **May import:** `pixibot.model`, `pixibot.schema` (`DEPTHS`, the one definition),
  `pixibot.lib.mockrun`, stdlib
- **Must not import:** `pixibot.engine`, `pixibot.cli`, `pixibot.tui`, `pixibot.run` —
  provider choice must not know what a run does with the models
- **Imported by:** `pixibot.cli`, `pixibot.tui` (through the session), `pixibot.run`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `UnknownProviderError` | `get()` is given a name outside `NAMES` | show `NAMES` and keep the current provider |

A missing API key is **not** an error here: the model object is built, and the failure
surfaces from the provider SDK at first call, where the agent-level error handling and the
run log can report it against a specific agent.

## Observability

- **No logging.** This module makes decisions, not calls: there is nothing an operator
  must act on. Model construction, token usage and failures are logged by `model.py`
  (`pixibot.model`) and the activation that used them by `context_manager`.
- The chosen provider reaches the operator through the shell banner and `status`, which
  read `Provider.label`/`.name` — not through a log line.
- Never logged here: `api_key_env` **values** (only the variable name is ever held).

## Tests

- `get("offline")` builds with no environment at all; `spokesbot()` is `None`
- `get("anthropic").model_factory()` resolves junior → `claude-sonnet-5` at effort `medium`
- `hard=True` routes a principal spec to `claude-fable-5` at effort `xhigh`
- `hard=True` on gemini routes principal to `gemini-2.5-pro`
- a spec with no budget, or an unknown depth, resolves to the senior model (no raise)
- `get("nope")` raises `UnknownProviderError`
- `detect({})` is `"offline"`; `detect({"ANTHROPIC_API_KEY": "x"})` is `"anthropic"`
- `detect` prefers gemini over openrouter over anthropic when several keys are present
- every name in `NAMES` is constructible by `get()`

## Deletion test

Deleting this module restores the state before it: provider knowledge in three places —
per-provider model tables and `*_model_for` helpers in `config.py`, six `*_tpm_model` /
`make_*_model_factory` functions in `run.py`, and three spokesbot builders plus four
engine factories in `cli.py`. Adding a provider meant editing three files and adding six
functions, and the CLI had to unpack a positional 4-tuple to switch one. It earns its keep.
