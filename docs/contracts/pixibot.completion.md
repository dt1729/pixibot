# Contract: pixibot.completion

**Status:** accepted
**Depth:** senior

## Responsibility

Decide what the half-typed word at the shell could become.

## Interface

```python
PATH_COMMANDS = ("cd", "cat", "ls", "tree")
AGENT_COMMANDS = ("ask", "tell", "think")

@dataclass(frozen=True)
class Sources:
    commands: Iterable[str]
    agent_ids: Callable[[], list[str]]
    listdir: Callable[[str], list[str] | None]
    cwd: str = ""

def common_prefix(strings: list[str]) -> str
def path_candidates(fragment: str, src: Sources) -> list[str]
def complete(text: str, src: Sources) -> tuple[str, list[str]]
```

- `complete` returns **(text to put in the input, every candidate)**. One candidate is
  applied with a trailing space; several apply only their shared prefix, which is what
  makes repeated Tab feel like bash.
- With no candidates the text comes back **unchanged** — never emptied, never guessed.
- The first word completes against `commands`; a later word completes by what the command
  takes. A command in neither list completes nothing rather than offering paths.
- `agent_ids` and `listdir` are callables so nothing is read until a completion actually
  needs it: a Tab on the command word never touches the filesystem or the blackboard.
- Paths are resolved **relative to the shell's `cwd`**, and a fragment containing `/`
  keeps its directory prefix in the candidates, so the applied text stays valid input.
- `listdir` returning `None` means "not a directory" and yields no candidates.

## Seam

`Sources` is the seam: three lookups and a cwd. It exists so completion is a pure function
of its inputs — the whole point of the module being here rather than inside the TUI, where
it could only be tested by driving a terminal.

Rejected: passing the `ChatSession`. It would couple completion to the shell's object
graph (blackboard, workspace, engine) to read three things, and make the tests build a
session.

## Dependencies

- **May import:** stdlib only (`dataclasses`, `pathlib.PurePosixPath`, `collections.abc`)
- **Must not import:** `pixibot.tui`, `pixibot.cli`, `pixibot.workspace` — it is handed
  lookups, it does not find them
- **Imported by:** `pixibot.tui`

## Errors

No error modes. Every input — empty text, a command with no completions, a missing
directory, no agents yet — has a defined, non-raising answer, because this runs on a
keystroke and an exception would take the UI down with it.

## Observability

No logging. A completion is an immediate, visible answer to a keystroke; a log line per
Tab would be noise in the run log and an operator never needs one after the fact.

## Tests

- a unique command completes with a trailing space
- several candidates apply the shared prefix and return the full list
- an unmatched word is returned unchanged
- a path command completes workspace entries, relative to the shell's cwd
- a fragment with a directory keeps the prefix in its candidates
- an unknown directory completes nothing
- agent commands complete live agent ids, and complete nothing before any build
- a command that takes neither a path nor an agent completes nothing

## Deletion test

Deleting this module puts completion back inside the TUI widget, where it was untestable
without a terminal — and where its path handling (cwd-relative, directory-prefixed) had no
tests at all. It earns its keep.
