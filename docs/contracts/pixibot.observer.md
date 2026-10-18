# Contract: pixibot.observer

**Status:** accepted
**Depth:** senior

## Responsibility

Reconstruct what happened in a run from its event log, as a report a human reads.

## Interface

```python
def dag_edges(events: list[Event]) -> list[tuple[str, str, int]]
def mermaid(events: list[Event]) -> str
def report(bb: Blackboard) -> str
```

- Everything here is **deterministic and model-free** (decision 11 is an Observer *agent*;
  this is its structural half). The same log always produces the same report, which is
  what makes it usable in a test.
- `dag_edges` yields one edge per **addressed** event: broadcasts and pure artifacts
  contribute none, because an edge to "everyone" is not information. `in_reply_to` is left
  on the events for deeper causal analysis rather than collapsed here.
- `mermaid` sanitises agent ids into mermaid-safe node names (`-` becomes `_`, `*` becomes
  `broadcast`), so the diagram renders even when the TPM names an agent `prog-f1`.
- `report` returns markdown with the event and artifact counts, activity per agent, the
  artifact list with its author, and the DAG in a mermaid block. It reads the log through
  the blackboard's own queries, so it is always consistent with what the agents saw.
- Order is stable: artifacts sorted by section, agents by descending activity.

## Seam

No seam. Two pure functions over a list of events and one that reads a blackboard — the
variation a future caller might want (a richer narrative from a model) is a *new* function
beside these, not a strategy inside them.

Rejected: rendering through a template engine. The output is three lists and a code
fence; a dependency would be larger than the module.

## Dependencies

- **May import:** `pixibot.blackboard`, stdlib (`collections.Counter`)
- **Must not import:** `pixibot.engine`, `pixibot.cli`, `pixibot.model` — a report is not
  allowed to need a model
- **Imported by:** `pixibot.engine`, `pixibot.cli`, `pixibot.run`

## Errors

No error modes. An empty log reports zero events and an empty DAG; an agent with no
artifacts simply does not appear in the artifact list.

## Observability

No logging. This module *is* the observability surface — its output is the thing an
operator reads, and a log line about producing a report would be the only entry nobody
could act on.

## Tests

- an addressed message and its reply both appear as edges, with their event ids
- a broadcast contributes no edge
- an artifact with no recipient contributes no edge
- agent ids with hyphens render as mermaid-safe node names
- a report names every artifact with its author, and counts events per agent
- an empty log produces a report rather than an error

## Deletion test

Deleting this module means a finished run leaves only rows in SQLite: no way to see who
talked to whom, which agent produced which file, or what the run did at all without
writing queries by hand. The shell's `report` and the engine's final summary are both
this function. It earns its keep.
