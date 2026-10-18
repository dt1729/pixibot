# Contract: pixibot.blackboard

**Status:** accepted
**Depth:** principal

## Responsibility

Be the run's single source of truth: an append-only event log every agent reads and writes.

## Interface

```python
KIND_ARTIFACT = "artifact"; KIND_MESSAGE = "message"
KIND_DIRECTIVE = "directive"; KIND_CONTROL = "control"
VALID_KINDS: set[str]
BROADCAST = "*"

@dataclass
class Event:
    id: int; run_id: str; ts: str; kind: str
    from_agent: str; to_agent: str | None; section: str | None
    payload: str; in_reply_to: int | None; urgent: bool; meta: dict[str, Any] | None

class ScopeError(PermissionError)

class Blackboard:
    def __init__(self, db_path: str, run_id: str = "default")
    run_id: str
    def close(self) -> None
    def reset_run(self, run_id: str) -> None

    # writing (append-only)
    def send(self, from_agent: str, payload: Any, *, to=None, kind=KIND_MESSAGE,
             section=None, in_reply_to=None, urgent=False, meta=None,
             enforce_scope=True) -> int

    # reading
    def poll_inbox(self, agent_id: str, *, advance: bool = True) -> list[Event]
    def history(self, *, from_agent=None, to_agent=None, kind=None) -> list[Event]
    def read_section(self, section: str) -> Event | None
    def current_state(self) -> dict[str, Event]
    def latest_event_id(self) -> int

    # mutable state, kept out of the log
    def register_agent(self, agent_id, *, role=None, depth=None, model=None,
                       scope=None, state="SPAWNED", budget=None) -> None
    def set_state(self, agent_id: str, state: str) -> None
    def get_agent(self, agent_id: str) -> dict[str, Any] | None
    def list_agents(self) -> list[dict[str, Any]]
    def save_memory(self, agent_id: str, transcript: str) -> None
    def load_memory(self, agent_id: str) -> str | None
```

- `events` is **immutable**: there is no update or delete. Mutable state (cursors, the
  agent registry, per-agent memory) lives in separate tables, which is decision 30 and the
  reason the Observer can trust the log.
- `send` returns the new event id and is the **only** way to write. An artifact write
  requires a `section` and is checked against the sender's `scope` — scope is the write
  lock (decision 32). `enforce_scope=False` exists for the harness logging files an agent
  wrote outside its scope, never for an agent.
- `poll_inbox` **advances the agent's cursor** by default: calling it twice returns the
  second time only what arrived in between. That is what makes activation
  message-driven rather than polling-based (decision 15); pass `advance=False` to look
  without consuming.
- A non-string payload is JSON-encoded on the way in; it always reads back as `str`.
- The instance is **thread-safe** (one connection, `check_same_thread=False` behind an
  `RLock`), because the TUI runs builds on a worker thread while the UI reads.
- `reset_run` gives a fresh run id and clears the registry, cursors and memory, leaving
  prior runs' events on disk (decision 45): an accidental rebuild cannot erase history.
- Parameter lists mirror the table columns on purpose; see the Seam section.

## Seam

No injected seam, and deliberately none: this is the substrate (decision 29 — SQLite behind
one scope-enforcing service). A `Storage` protocol with one implementation would be a
hypothetical seam, and every call site would pay for it.

The seam that does exist is the **event shape**: `kind` + `section` + `to_agent` is the
vocabulary every other module speaks, which is why those are module constants rather than
strings at call sites.

`send`/`register_agent` keep wide keyword signatures with a stated reason: the parameters
*are* the table columns, so folding them into a record would push row-building into every
call site and add a type nobody reads. That judgment is recorded here rather than
re-litigated at each `# noqa: PLR0913`.

## Dependencies

- **May import:** stdlib only (`sqlite3`, `json`, `threading`, `time`)
- **Must not import:** anything in `pixibot` — this is the bottom of the data stack
- **Imported by:** nearly everything; `pixibot.lib.agent`, `engine`, `cli`, `observer`,
  `interaction`, `benchmark`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `ScopeError` | an agent writes an artifact outside its `scope`, or is unregistered | the tool returns the message to the model, which must write where it was scoped |
| `ValueError` | an unknown `kind`, or an artifact with no `section` | a programming error in the harness; fix the call |
| `sqlite3.Error` | the database is unreadable or the disk is full | propagates; the run cannot continue without its log |

## Observability

- **No logging.** Every write already produces a durable, queryable row — logging it would
  duplicate the log into a second log. Callers that need narration (the scheduler, the
  agent loop) log with the context they have.
- The operator-facing views are `history`, `current_state` and `pixibot.observer.report`,
  which read the same rows.
- Never logged: payloads (they hold whole source files and model reasoning).

## Tests

- an addressed event lands in exactly that agent's inbox, and the cursor advances
- a second `poll_inbox` returns nothing until something new arrives
- `advance=False` leaves the cursor where it was
- a broadcast reaches every registered agent
- an artifact write outside the sender's scope raises `ScopeError`
- an unregistered sender writing an artifact raises `ScopeError`
- `current_state` returns the latest artifact per section, not every version
- `history` filters by from/to/kind
- `in_reply_to` survives a round trip, so the Observer can rebuild causality
- `reset_run` clears the registry and cursors but keeps prior events
- memory saves and loads per (run, agent), and returns `None` when absent

## Deletion test

Deleting this module deletes the architecture: coordination *is* the log (decision 9).
Agents would need direct references to each other, activation would need a scheduler
holding queues in memory, and nothing could be resumed or audited after the process
exits. It earns its keep.
