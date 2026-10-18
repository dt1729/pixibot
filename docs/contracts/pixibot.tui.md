# Contract: pixibot.tui

**Status:** accepted
**Depth:** senior

## Responsibility

Render a run in three panes while the shell stays usable.

## Interface

```python
class Pane(Vertical)              # a focusable, zoomable region
class CommandInput(Input):
    KEYS: ClassVar[dict[str, Callable[[CommandInput], None]]]   # keys this input owns
    def load_history(self) -> None
    def remember(self, line: str) -> None

class PixibotApp(App):
    BINDINGS: ClassVar[list[BindingType]]
    def __init__(self, session: ChatSession, label: str, log_path: str = "")

def run_tui(session: ChatSession, label: str, log_path: str = "") -> None
```

- `run_tui` **blocks** until the user quits, and returns normally; the caller closes the
  blackboard afterwards.
- Every command goes through the same `ChatSession` the plain shell drives. Local commands
  (`_LOCAL`) run on the UI thread; everything else runs in a Textual worker thread, so a
  build does not freeze the interface.
- Pane updates from the worker go through `call_from_thread`, which is why the session's
  hooks are installed here as marshalling lambdas rather than called directly.
- The module **renders only**: it parses no intake form, chooses no input shape and builds
  no engine. A command's text answer is whatever `session.handle` returned.
- `CommandInput.KEYS` is the complete list of keys this widget intercepts; anything else
  falls through to Textual, including the arrow keys used for cursor movement.
- History is appended to `~/.pixibot_history` on every accepted line and capped at 1000
  lines on load, shared with the plain shell's readline history.
- `textual` is an optional extra: importing this module without it raises `ImportError`,
  which `cli.main` catches to fall back to the plain shell.

## Seam

The seam is `ChatSession` plus its four hooks (`on_build_start`, `progress_start`,
`progress`, `on_event`). That is the whole coupling, and it is one-directional: the TUI
imports the session, never the reverse.

Completion is **not** implemented here — it is `pixibot.lib.completion`, called with lookups
pointing at this session. Nothing in this module is a seam for anyone else: it is a leaf.

Rejected: letting the TUI build the engine itself (what it used to do). It duplicated
build-from parsing and input construction, and the copy drifted: a change to the engine
factory's signature broke the TUI while the plain shell kept working and the tests stayed
green.

## Dependencies

- **May import:** `pixibot.cli`, `pixibot.lib.completion`, `pixibot.lib.logbook`, `textual`
- **Must not import:** `pixibot.engine`, `pixibot.lib.orchestrator`, `pixibot.intake`,
  `pixibot.schema` — the session owns all of that
- **Imported by:** `pixibot.cli` (lazily, inside `main`)

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `ImportError` | the module is imported without `textual` | `cli.main` prints the install hint and uses the plain shell |

Nothing else escapes: a command that raises in the worker is caught, logged and printed in
the shell pane, because a traceback would take the whole interface down with it.

## Observability

- **WARNING** when a clipboard path fails (clip.exe, then OSC-52), since the user pressed
  a key and nothing visible happened.
- **ERROR** (via `log.exception`) when a command raises in the worker, naming the command
  word; the user also sees the message in the pane.
- **No INFO.** Everything a run does is already logged by the engine, the scheduler and
  the agents; a UI event is not something an operator acts on after the fact.
- Never logged: the typed line beyond its first word, pane contents, or clipboard text.

## Tests

- every editing key the module documents is present in `CommandInput.KEYS`
- keys the app owns (quit) and keys Textual owns (cursor movement) are absent from it
- `_LOCAL` is a subset of the commands the session actually understands
- `build`, `revise`, `tell` and `ask` are **not** local, so they cannot block the UI
- (skipped entirely when `textual` is not installed)

## Deletion test

Deleting this module costs the live view of a build — per-agent state and streamed
thinking — and nothing else: the plain shell remains fully functional, which is the point
of keeping every decision in the session. It earns its keep as the only place that can
show a long run while it happens.
