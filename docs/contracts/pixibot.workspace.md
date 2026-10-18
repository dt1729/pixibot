# Contract: pixibot.workspace

**Status:** accepted
**Depth:** senior

## Responsibility

Give agents a real project directory they cannot write or read outside of.

## Interface

```python
class Workspace:
    def __init__(self, root: str, executor: Executor | None = None)
    path: Path                      # resolved root
    executor: Executor
    @property
    def root(self) -> str           # what logs, prompts and the CLI print

    def write_file(self, path: str, content: str) -> str
    def read_file(self, path: str) -> str | None
    def list_files(self) -> list[str]
    def listdir(self, path: str = "") -> list[str] | None
    def isdir(self, path: str = "") -> bool
    def snapshot(self) -> dict[str, float]
    def changed_since(self, before: dict[str, float] | None) -> list[str]
    def clear(self) -> None
    def run(self, command: str, timeout: int = 120) -> tuple[int, str]
```

- Every path argument is **workspace-relative** and is fully resolved before use: `..`
  is collapsed and symlinks are followed, so neither `../../etc/passwd` nor a symlink
  planted inside the tree can name a file outside it. Escaping raises `ValueError`.
- `read_file` returns `None` for absent, binary, or undecodable files — agents read
  whatever they like, and a `UnicodeDecodeError` would crash a turn for no reason.
- `list_files` and `snapshot` **exclude** build and tooling directories (`__pycache__`,
  `.git`, `.venv`, `build`, `pytest-of-*`, …): they are not the project the agents wrote,
  and including them made every `changed_since` report hundreds of files.
- `changed_since(None)` means "no prior snapshot", and reports every file.
- `run` returns `(returncode, output)` and never raises. A command that tries to leave the
  tree is refused **before** the executor sees it, with exit code 126 and a refusal the
  model is meant to read and obey.
- `clear()` is destructive and unconditional: it is called at the start of a build so a
  previous run's files cannot anchor a new task (decision 41).

## Seam

`executor` is the one injected dependency, and what varies across it is isolation:
a confined host subprocess (default) or a container. Path policy does **not** vary and is
not injectable — containment is this module's whole reason to exist, so there is no hook
for a caller to relax it.

The split with `pixibot.runtime` is by question: *which paths may be touched* is here,
*where a command runs* is there. The escape-pattern screen stays here because it is about
paths, and therefore holds whichever executor is in use.

Rejected: making `Workspace` a `Path` subclass. The point is the subset of operations
that are allowed, and inheriting `Path` would hand callers every operation that is not.

## Dependencies

- **May import:** `pixibot.runtime`, stdlib (`pathlib`, `re`, `shutil`, `contextlib`)
- **Must not import:** `pixibot.agent`, `pixibot.engine`, `pixibot.blackboard`
- **Imported by:** `pixibot.agent` (tool impls), `pixibot.engine`, `pixibot.cli`,
  `pixibot.tui`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `ValueError` | a path argument resolves outside the workspace | refuse the agent's tool call with the message |
| `OSError` | a write fails for a reason other than containment (full disk, permissions) | propagates; the run log records the activation that died |

A refused **command** is not an exception: `run` returns `(126, refusal text)` so the
model sees the refusal and can correct itself, which is the behaviour decision 43 wanted.

## Observability

- **No logging.** Every caller already logs with more context than this module has: the
  agent loop logs each tool call and its result, and the engine logs the workspace root
  at build start. A line here would duplicate both.
- Containment failures surface as the `ValueError` message and the 126 refusal text,
  which are read by the model and visible in the activity feed.
- Never logged here: file contents, command text, or the resolved paths of refused
  attempts.

## Tests

- write then read round-trips, creating parent directories
- `read_file` of a missing file is `None`; of a binary file is `None`, not a raise
- `write_file("../escape.py")` raises `ValueError`
- a symlink inside the tree pointing at `/etc` cannot be read through
- `list_files` excludes `pytest-of-*` and vendored/cache directories
- `run` executes in the workspace and returns the command's output
- `run` refuses parent traversal, absolute system paths and key-file names with 126
- a command cannot see a secret-bearing variable from the parent environment
- `changed_since` reports a file written after the snapshot, and nothing otherwise

## Deletion test

Deleting this module scatters path containment across the five tool implementations that
touch files, each needing its own resolve-and-compare — and the one that forgets is the
one that reads `~/ant_key.txt`, which is exactly the incident decision 43 records. It
earns its keep.
