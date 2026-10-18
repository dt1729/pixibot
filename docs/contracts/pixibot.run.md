# Contract: pixibot.run

**Status:** accepted
**Depth:** junior

## Responsibility

Run one projection start to finish, with nobody steering.

## Interface

```python
def run_pipeline(inp: dict[str, Any], *, tpm_model: Model,
                 model_factory: Callable[[dict[str, Any]], Model],
                 bb: Blackboard, max_steps: int = 100) -> dict[str, Any]
```

- Returns `{"projection", "steps", "report"}` — the plan, how many activations ran, and
  the Observer's markdown report.
- The input is **validated first**: an invalid request raises `ValidationFailed` before a
  single token is spent.
- Ordering inside is fixed and not a caller's business: validate, plan, materialize, kick,
  drive to quiescence, report. Materialize-then-kick matters — reversed, the first message
  is addressed to an agent nobody has registered.
- There is **no workspace**: agents coordinate through the blackboard only. A run that
  should write real files wants `Engine`, which owns a workspace per build.
- `max_steps` bounds the scheduler, so a cycle of agents messaging each other terminates.
- Synchronous and blocking; no hooks, no interaction, no checkpoints. That is the point.

## Seam

Two injected seams, both mandatory: the planning `Model` and the per-agent `ModelFactory`.
`pixibot.providers` produces both for any provider name, including `offline`, which is why
this function is the simplest way to exercise the whole system in a test.

Deliberately **not** a class. There is no state to keep between calls; the mutable state
is the blackboard the caller passed in.

Rejected: giving it optional hooks and a workspace to "match Engine". It would become
Engine with a worse name; the useful distinction is that this one has no middle.

## Dependencies

- **May import:** `pixibot.lib.orchestrator`, `pixibot.lib.tpm`, `pixibot.observer`,
  `pixibot.schema`, `pixibot.blackboard`, `pixibot.model`, `pixibot.lib.context_manager`
- **Must not import:** `pixibot.cli`, `pixibot.tui`, `pixibot.engine`
- **Imported by:** tests, and callers embedding Pixibot in another program

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `ValidationFailed` | the input fails the strict schema, or the TPM cannot produce a valid projection in its repair budget | show `.errors`; the objective usually needs narrowing |
| `ScopeError` | an agent writes outside its scope and the tool layer re-raises | a projection bug; the scope and the write section disagree |

A crashing agent is not an error: the scheduler marks it BLOCKED and the run continues.

## Observability

- **No logging of its own.** Everything it calls logs with more context: the TPM's plan,
  each activation, each tool call. A line here would announce what the next line proves.
- The return value's `report` is the operator-facing artifact.
- Never logged: the objective or the projection JSON.

## Tests

- a full mock pipeline writes the expected artifact and reports at least one step
- an invalid input raises `ValidationFailed` before any model call
- the report contains the message DAG

## Deletion test

Deleting this module costs the one-shot path: every embedding caller would re-create the
validate → plan → materialize → kick → run → report sequence, and the ordering trap
(kick before materialize silently drops the first message) is exactly the kind of thing
each copy would get wrong once. It earns its keep.
