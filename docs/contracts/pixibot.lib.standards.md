# Contract: pixibot.lib.standards

**Status:** accepted
**Depth:** junior

## Responsibility

Put the quality bar in every agent's prompt, and hand over the full documents on request.

## Interface

```python
def standards_contract() -> str
def load_standard(name: str) -> str
def available_standards() -> list[str]
```

- `standards_contract()` is the **always-in-prompt** summary (DESIGN.md §10's hybrid
  progressive disclosure, decision 33). It is a constant: no file is read, so it cannot
  fail, and `pixibot.lib.factory` appends it to every role prompt.
- `load_standard(name)` reads one document by file name, e.g. `load_standard("lld.md")`.
  It **raises** `FileNotFoundError` for a name that is not there rather than returning an
  empty string — a silently empty standard is how an agent ends up with no bar at all.
- `available_standards()` lists the `.md` file names, sorted. It returns `[]` only when the
  directory itself is missing, which is a packaging failure, not an empty-but-valid state.
- The documents live **inside the package** at `pixibot/standards/`, shipped as package
  data, so the path resolves identically from a clone and from an installed wheel. They sat
  at the repo root until M50 and the module read them through `__file__.parent.parent` —
  which stopped reaching the root when the module moved into `lib/` at M41, leaving two of
  these three functions dead for six milestones (docs/incidents.md 010).
- Documents are read on **every** call: there is no cache, because an agent reads at most
  one or two per run and a stale cache would outlive an edit to the standard.

## Seam

No seam, and deliberately none: the directory is package data, not a configuration point.
A `standards_dir` parameter would let a caller point the agents at a different bar, which
is the one thing this module exists to prevent — the bar is the project's, not the
caller's.

Rejected: reading the documents at import time into a module constant. It would make a
packaging failure an import failure for the whole system, and the always-on contract (the
part every agent needs) does not touch the filesystem at all.

## Dependencies

- **May import:** stdlib (`pathlib`) only
- **Must not import:** anything in `pixibot` — the bar cannot depend on what it judges
- **Imported by:** `pixibot.lib.factory` (for the always-on contract)

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `FileNotFoundError` | `load_standard` is given a name with no document | the tool returns the message to the model, which should call `available_standards` first |
| `OSError` | the document exists but cannot be read | propagates; a packaging or permissions fault, not a run-time condition |

`standards_contract()` has no error modes: it reads nothing.

## Observability

No logging. Two of these three functions read a file and return it, and the third returns
a constant; a log line per read would say only that an agent asked, which the agent loop
already records as a tool call. A missing document surfaces as `FileNotFoundError` with
the path in the message, which is what an operator needs.

## Tests

- the directory the module reads actually exists (the M41 regression, caught directly)
- every expected document is discoverable by name
- every discoverable document loads and is non-trivial
- a missing document raises `FileNotFoundError` rather than returning empty
- the always-on contract points at the documents it names
- the documents resolve **inside** the package, not beside it

## Deletion test

Deleting this module removes the standards from every agent prompt: decision 21 authored
four documents and decision 33 injects them progressively, and this is the whole
injection path. Its own history is the argument for keeping it as a module with a
contract rather than a string in `factory`: when it had no contract and no test, it broke
and stayed broken for six milestones. It earns its keep.
