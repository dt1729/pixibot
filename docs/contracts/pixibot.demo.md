# Contract: pixibot.demo

**Status:** accepted
**Depth:** junior

## Responsibility

Show the whole system working offline, in one scripted pass.

## Interface

```python
def main() -> None      # python -m pixibot.demo
```

- Runs with **no API key and no network**: the offline provider's scripted models drive a
  real build through the real engine, scheduler, blackboard and workspace.
- Prints six labelled stages in order: the intake form, a build with its dependency
  cascade, a read-only chat with an agent, a non-blocking directive, a revision that
  splices in a new agent, and the Observer report.
- Uses a **temp database** and removes it (and its WAL siblings) afterwards, so running the
  demo twice is identical and leaves nothing behind.
- Output is plain text on stdout; nothing here is interactive, so it can be piped into a
  file or a terminal recording.
- It is a **demonstration, not a test**: it asserts nothing. The same paths are covered by
  the suite; this exists so a person can watch them.

## Seam

No seam, deliberately: the demo wires concrete offline parts so that what it shows is what
the system does. Parameterising it (which provider? which objective?) is what the CLI is
for, and a configurable demo would stop being a fixed thing to compare against.

## Dependencies

- **May import:** `pixibot.engine`, `pixibot.interaction`, `pixibot.intake`,
  `pixibot.blackboard`, `pixibot.lib.mockrun`
- **Must not import:** `pixibot.cli`, `pixibot.tui` — a demo of the system is not a demo
  of a front end
- **Imported by:** nothing; it is an entry point

## Errors

No error modes in normal use: the scripted models cannot fail for credentials or network
reasons. A genuine bug in the engine surfaces here as a traceback, which is the useful
behaviour for a demo of a developer's own change.

## Observability

No logging. Its entire output is the demonstration, printed; a run log would compete with
what the reader is watching. (The engine it drives still logs, to the usual file.)

## Tests

- not directly tested: it prints rather than returns, and every path it walks has its own
  test (engine build, broker talk, directive resume, revision, observer report)
- the offline scripted models it relies on are covered by `tests/test_providers.py`
- the end-to-end cascade it shows is covered by `tests/test_engine.py`

## Deletion test

Deleting this module costs the one place a newcomer can see the architecture move without
a key, a container or a credit card — and the one artifact that would catch "the offline
path no longer works end to end" in a single command. It earns its keep.
