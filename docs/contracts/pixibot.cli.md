# Contract: pixibot.cli

**Status:** accepted
**Depth:** senior

## Responsibility

Turn a typed line into an action on the run, and return the text to show.

## Interface

```python
HELP: str
COMMANDS: frozenset[str]        # every word the shell understands
QUIT = "__quit__"               # sentinels a front end acts on rather than prints
CLEAR = "__clear__"

class EngineFactory(Protocol):
    def __call__(self, objective: str, *, hard: bool = False) -> Engine: ...

class ChatSession:
    DISPATCH: ClassVar[dict[str, str]]     # command -> method name
    ALIASES: ClassVar[dict[str, str]]      # "?" -> "help", "buildfrom" -> "build-from"

    def __init__(self, bb: Blackboard, broker: Broker, engine_factory: EngineFactory, *,
                 workspace: Workspace | None = None, provider_name: str = "custom")
    def handle(self, line: str) -> str

    # hooks a front end sets (all optional)
    progress: Callable[[str, str, list[str | None]], None] | None
    progress_start: Callable[[str], None] | None
    on_event: Callable[[str, str, str], None] | None
    on_build_start: Callable[[str], None] | None
    sandbox: str                            # passed to every engine it builds

def wire(bb: Blackboard, provider: Provider) -> tuple[Broker, EngineFactory]
def main(argv: list[str] | None = None) -> None
```

- `handle` **never raises for bad input**: unknown commands, missing arguments and
  unreadable files all return text a person can read. A failure inside a build does
  propagate — the front end decides whether that kills the session.
- A first word not in `DISPATCH`/`ALIASES` is a question for the project overseer, not an
  error. That is why `COMMANDS` is exported: a front end that wants to stream a chat reply
  needs the same test the dispatcher uses.
- `QUIT`/`CLEAR` are returned, not acted on, because the session owns no terminal.
- `handle` is **synchronous and blocking**: `build`, `revise` and `tell` can take minutes.
  A front end that must stay responsive runs it on another thread (the TUI does) — the
  session holds no thread affinity, and the blackboard is thread-safe.
- Build sequencing lives here, not in a front end: `_start_engine` applies `sandbox` and
  all four hooks to each new engine, so the plain shell and the TUI cannot drift.
- `workspace` is replaced by each build with that build's isolated directory, and `cwd`
  resets to the root — a stale `cd` would otherwise point into a previous run.

## Seam

Two injected seams, both real: `Broker` (chat) and `EngineFactory` (work). `wire()` builds
both from a `Provider`, so switching provider at runtime is one call and the session never
learns what a provider is.

`EngineFactory` is a Protocol rather than a `Callable` alias so the hard-development flag
is keyword-only: `factory(objective, hard=True)` reads at the call site, where
`factory(True, objective)` did not.

The hooks are plain callables, not an interface, because each front end has exactly one
implementation and the TUI's are thread-marshalling lambdas.

Rejected: a `Command` class per command. Twenty-two commands whose bodies are two lines
each would become twenty-two files; the dispatch table keeps them one method apiece and
one line in the table, which is also what the completer reads.

## Dependencies

- **May import:** `pixibot.providers`, `pixibot.engine`, `pixibot.interaction`,
  `pixibot.observer`, `pixibot.intake`, `pixibot.schema`, `pixibot.workspace`,
  `pixibot.console`, `pixibot.logbook`, `pixibot.blackboard`
- **Must not import:** `pixibot.tui` (the TUI imports the session, not the reverse),
  `pixibot.agent`, `pixibot.orchestrator`
- **Imported by:** `pixibot.tui`, `pixibot.__main__`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `UnknownProviderError` | never reaches the caller — `_provider` checks `providers.NAMES` first and returns text | none |
| whatever a build raises | the TPM, a provider or the sandbox fails during `build`/`revise` | the plain shell lets it surface; the TUI catches it in its worker and prints it |

A bad path, a missing argument, an unreadable form and an unknown provider are all
answered with text, because the person typing is the one who must fix them.

## Observability

- **No logging of its own.** `main` calls `logbook.setup()` so everything underneath
  logs to one file, and tells the user where that file is; the session itself adds nothing.
- What the operator sees instead: per-agent progress lines (`_progress`), the planning
  line, and `status`/`updates`/`report` on demand.
- Never logged: the user's typed lines, which may contain anything.

## Tests

- navigation: `ls`, `cd`, `pwd`, `cat` and `tree` work relative to a workspace
- `cd ..` above the root is refused, and `cd` with no argument returns to the root
- a build routes to the engine and adopts its workspace
- `tell` with an engine routes to the engine; without one falls back to the broker
- `revise` before a build explains itself instead of raising
- plain text goes to the overseer, and a known command does not
- `provider <name>` switches and re-wires the broker; an unknown name is refused
- every hook the session holds is handed to the engine a build creates
- `gate` before a build explains itself; after one it reports the verdict

## Deletion test

Deleting this module leaves the engine with no human in the loop. Both front ends would
re-grow argument parsing, path resolution, form validation and build sequencing — which is
exactly what the TUI had done, and its copy had already drifted. It earns its keep.
