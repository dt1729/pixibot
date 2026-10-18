# Contract: pixibot.interaction

**Status:** accepted
**Depth:** senior

## Responsibility

Let a person talk to a running team without pausing it.

## Interface

```python
PROJECT_TARGETS = {"tpm", "project", "overseer", "orchestrator"}
SpokesbotFactory = Callable[[], Model | None]

def snapshot(bb: Blackboard, agent_id: str) -> str
def project_snapshot(bb: Blackboard) -> str
def latest_reports(bb: Blackboard) -> dict[str, str]

class Broker:
    def __init__(self, bb: Blackboard, spokesbot_factory: SpokesbotFactory | None = None)
    def talk(self, agent_id: str, message: str,
             on_delta: Callable[[str], None] | None = None) -> str
    def tell(self, agent_id: str, directive: str) -> int
```

- `talk` is **read-only and never interrupts the agent** (decision 35). It snapshots that
  agent's blackboard context and asks a cheap, separate *spokesbot* model about it. The
  working agent is not consulted and does not stop.
- Steering is the other method: `tell` appends a `directive` event to the agent's inbox and
  returns its event id. The agent picks it up at its next activation — so steering is
  asynchronous by construction, not by a timeout.
- Addressing one of `PROJECT_TARGETS` talks to the **project overseer** instead: a
  snapshot across all agents, their latest completion reports and recent messages. That is
  what plain typed text in the shell reaches.
- A spokesbot factory returning `None` means **offline**: `talk` returns the context
  snapshot itself, so the feature degrades to "show me what it knows" rather than failing.
- Chat history is kept per target so a conversation has continuity; it is in memory only
  and not part of the run's record.
- Snapshots are **clipped** (200 characters per payload, the last 10 events, 8 recent
  messages): a spokesbot prompt that grew with the run would get slower and more expensive
  exactly as a run gets interesting.

## Seam

`SpokesbotFactory` is the seam, and what varies is the model behind the chat: Haiku or a
flash-tier model live, `None` offline, a `MockModel` in tests. It is a factory rather than
a model so the Broker survives a provider switch — the CLI re-wires it by building a new
Broker, and nothing else changes.

Rejected: letting the Broker reach into the agent object. It would couple chat to a live
run in the same process, and the point of a snapshot is that a second Pixibot instance,
or a run that has already finished, can still be interrogated.

## Dependencies

- **May import:** `pixibot.blackboard`, `pixibot.model`, stdlib
- **Must not import:** `pixibot.lib.agent`, `pixibot.engine`, `pixibot.cli` — the broker
  reads the log, not the run
- **Imported by:** `pixibot.cli` (and the TUI through the session)

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| whatever the spokesbot model raises | the chat request fails (auth, rate limit) | the shell shows it; the build is unaffected, because nothing about it ran through here |

Talking to an agent that does not exist is not an error: the snapshot says
"(not registered)" and the spokesbot answers from that.

## Observability

No logging. Every message a broker sends is an event on the blackboard (`tell`) or a
model call that `pixibot.model` already logs usage for; the chat text itself is the user's
and is not written to the run log.

## Tests

- a snapshot carries the agent's identity, its received messages and its produced work
- a snapshot of an unregistered agent says so instead of raising
- `talk` with no model returns the snapshot (offline mode)
- `talk` with a model passes the snapshot as the system prompt and keeps chat history
- talking to a project target uses the project-wide snapshot
- `latest_reports` returns each agent's most recent completion report
- `tell` appends a directive addressed to the agent and returns its event id
- long payloads are clipped in the snapshot

## Deletion test

Deleting this module takes the conversation out of the system: the only ways to inspect a
run would be the raw report and `cat`, and the only way to steer one would be to stop and
rebuild. The non-blocking split (snapshot + spokesbot for questions, directive events for
changes) is decision 35, and it exists here or nowhere. It earns its keep.
