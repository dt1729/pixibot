# Contract: pixibot.schema

**Status:** accepted
**Depth:** senior

## Responsibility

Decide whether a request or a plan is well-formed, and get a valid one out of a model.

## Interface

```python
DEPTHS = {"junior", "senior", "principal"}

def compute_ceiling(raw: Any) -> int | None

def validate_input(inp: Any) -> list[str]
def validate_projection(proj: Any) -> list[str]

class ValidationFailed(Exception):
    errors: list[str]

def obtain_valid(generate: Callable[[list[str]], Any],
                 validate: Callable[[Any], list[str]], *,
                 max_attempts: int = 3) -> Any
```

- Both validators **return error strings and never raise** — an empty list means valid.
  The strings are written to be fed back to a model, so they name the path
  (`projection.agents[2].budget.depth`) and what was expected.
- Validation is **structural only**: shapes, types and enum membership. Whether an
  objective is sensible is not a schema question and is not pretended to be one.
- `compute_ceiling` reads a token ceiling out of whatever a model or a form wrote —
  `40000`, `"40000"`, `"40k"` — and returns `None` for anything unparseable, zero or
  negative. `None` means **unbounded**, never zero: a budget nobody can parse must not
  stop a run before it starts. It lives here because both ceilings read the same loose
  value, and it was duplicated verbatim in `engine` and `factory` until M50, where the two
  copies could have diverged.
- `validate_input` accepts anything (`Any`) because the caller may be handing over a
  parsed form or a model's JSON; a non-object is one error, not a crash.
- `obtain_valid` is the **bounded repair loop** (decision for §15 Q1): it calls
  `generate(errors)` up to `max_attempts` times, passing the previous attempt's errors,
  and raises `ValidationFailed` with the last set if none validates. The loop is bounded
  because an unbounded one is a billing incident.
- `generate` receives `[]` on the first attempt, so the same callable serves the first
  try and every repair.
- Pure stdlib: no `jsonschema` dependency, because the shapes are small and the error
  text is part of the interface.

## Seam

`obtain_valid` is the seam, and it is a function seam rather than a class: both
parameters are callables, so the loop is reusable for any generate/validate pair and
testable with none of the system attached (the tests pass lambdas).

Rejected: a JSON Schema document plus a library. The error text a library produces is
written for developers, and here the reader is a language model that must fix its own
output — which is why the messages are ours.

## Dependencies

- **May import:** stdlib only
- **Must not import:** anything in `pixibot` — validation must not depend on what it
  validates
- **Imported by:** `pixibot.lib.tpm`, `pixibot.run`, `pixibot.cli`, `pixibot.tui`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `ValidationFailed` | `obtain_valid` exhausts `max_attempts` | show `.errors` to the user; the plan the model wants to make does not fit the grammar |

The validators themselves have no error modes: malformed input is a returned string.

## Observability

No logging. A validator that logged would log once per repair attempt, three times per
plan, with nothing an operator can act on — and the errors are already carried in the
return value and the exception, which is where the caller shows them.

## Tests

- a minimal valid input validates with no errors
- a missing `objective`, a non-object input and a bad `max_depth` each produce a named error
- a projection with no agents, a duplicate agent id, or an unknown depth is rejected
- a valid projection validates
- `obtain_valid` returns the first valid candidate without further attempts
- `obtain_valid` passes the previous attempt's errors to the next call
- `obtain_valid` raises `ValidationFailed` after `max_attempts`, carrying the last errors
- `compute_ceiling` reads an int, a string and a "k" suffix identically, and reads
  nonsense, zero and a negative as `None`

## Deletion test

Deleting this module removes the only thing standing between a language model's JSON and
the orchestrator's `projection["agents"][0]["id"]`. The repair loop would be re-invented
inside the TPM, unbounded the first time. It earns its keep.
