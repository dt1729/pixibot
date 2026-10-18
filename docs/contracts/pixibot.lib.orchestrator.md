# Contract: pixibot.lib.orchestrator

**Status:** accepted
**Depth:** senior

## Responsibility

Turn a validated projection into registered agents wired for dependency activation.

## Interface

```python
ModelFactory = Callable[[dict], Model]
Projection = dict[str, Any]
ROLE_RANK: dict[str, float]            # lower runs first; unknown roles = implementation tier

def infer_deps(projection: Projection) -> dict[str, set[str]]
def blind_sections(projection: Projection) -> tuple[str, ...]
def register_one(spec: dict, cm: ContextManager, model_factory: ModelFactory,
                 run: RunContext, *, read_denylist: tuple[str, ...] = ()) -> ReasoningAgent
def materialize(projection: Projection, cm: ContextManager, model_factory: ModelFactory,
                run: RunContext | None = None, *, continuous: bool = True) -> ContextManager
def root_ids(projection: Projection) -> list[str]
def kick(bb: Blackboard, projection: Projection) -> None
```

- `materialize` then `kick` is the required order: registration must complete before the
  first message lands, or a root agent's inbox event is routed to nobody.
- `infer_deps` prefers the projection's explicit `depends_on`; when **no** agent declares
  one it falls back to a strict chain ordered by `ROLE_RANK`. The fallback is serial by
  design — a loose plan that runs every agent beats a parallel plan that runs one
  (decision 39).
- Dependencies are filtered to ids that exist in the projection, so a hallucinated
  `depends_on` cannot block an agent forever.
- `continuous=True` (adoption A) additionally registers every agent as a reader of the
  sections it declares, so it re-activates whenever an artifact lands there.
- `blind_sections` is computed once from the projection and passed **into** construction,
  so an agent's read denylist is set before it can run rather than patched afterwards.
- `run.objective` falls back to the projection's `plan_summary` when the caller leaves it
  empty, so every agent always has a goal in context.
- `materialize` is idempotent per agent id only in the sense that `ContextManager.register`
  overwrites: calling it twice on one projection re-registers the same agents.

## Seam

No new seam. The two it crosses are given: `ModelFactory` (from `pixibot.providers`) and
`ContextManager` (the scheduler). This module is deliberately a composition root for one
projection — it holds policy (how dependencies are inferred, who is blinded) and no
mechanism.

Rejected: a `Topology` class per adoption arm. The arms differ by a boolean and a denylist,
so classes would be ceremony; the ablation switch lives in `Engine.features`, which is
where an experiment is configured.

## Dependencies

- **May import:** `pixibot.lib.agent`, `pixibot.lib.factory`, `pixibot.lib.context_manager`,
  `pixibot.blackboard`, `pixibot.model`, stdlib
- **Must not import:** `pixibot.engine`, `pixibot.cli`, `pixibot.providers` — it is handed
  a factory, it does not choose one
- **Imported by:** `pixibot.engine`, `pixibot.run`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `KeyError` | the projection has no `agents`, or an agent-spec has no `id` | propagates: the schema validator should have rejected it, so this is a bug in the caller's order of operations |

Everything else about a loose projection is absorbed: a missing role becomes
`specialist`, a missing depth becomes `senior`, an unknown `depends_on` is dropped.

## Observability

- **No logging.** Registration is synchronous and its result is observable: the scheduler
  logs every activation at INFO, and the engine logs the agent ids it planned at build
  start. A line per registration would restate the engine's list.
- The dependency graph an operator may need to see is reconstructable from the blackboard
  by the Observer (`in_reply_to` edges), not from a log line.
- Never logged: agent prompts.

## Tests

- explicit `depends_on` is honoured and filtered to ids that exist
- with no `depends_on` anywhere, agents form a role-ranked chain
- an unknown role sorts at the implementation tier rather than raising
- `blind_sections` returns every programmer-written section, and only those
- a test-designer is constructed with that denylist; other roles are not
- `continuous=True` registers readers for each declared read section
- `continuous=False` registers none
- `root_ids` returns the dependency-free agents, and the first agent when a cycle leaves
  none
- `kick` addresses the plan summary to exactly the roots

## Deletion test

Deleting this module scatters projection interpretation into the engine and the pipeline:
two copies of dependency inference, two of the blindness rule, and two of the
register-then-kick ordering whose reversal silently drops the first message. It earns its
keep.
