# Contract: pixibot.runtime

**Status:** accepted
**Depth:** senior

## Responsibility

Run a shell command somewhere confined, and report what came back.

## Interface

```python
DEFAULT_IMAGE = "pixibot-run:latest"
DEFAULT_TIMEOUT = 120
SECRET_HINTS: tuple[str, ...]

@dataclass(frozen=True)
class ExecResult:
    returncode: int
    output: str          # stdout + stderr, in the order the child wrote them
    @property
    def ok(self) -> bool

class Executor(Protocol):
    workdir: str
    def run(self, command: str, *, timeout: int = DEFAULT_TIMEOUT) -> ExecResult: ...

def confined_env(root: str, environ: Mapping[str, str] | None = None) -> dict[str, str]
def docker_command(image: str, workdir: str, command: str, *, name: str | None = None) -> list[str]
def docker_available() -> bool
def executor_for(workdir: str = ".", *, sandbox: str = "auto", image: str = DEFAULT_IMAGE) -> Executor

class LocalExecutor:   # confined host subprocess
class DockerExecutor:  # one ephemeral container per command
class SandboxUnavailableError(RuntimeError)
```

- `run` **never raises** for an unhappy command: a non-zero exit, a timeout (returncode
  124) and a crash all come back as an `ExecResult`. The caller is a model-facing tool
  loop, so the output text is the product.
- `output` is interleaved stdout+stderr because the model reads it as one transcript.
  Callers wanting them apart would need a second method; none does today.
- `confined_env` withholds any variable whose **name** contains a `SECRET_HINTS` token
  and pins `HOME`, `TMPDIR` and `PWD` to `root`. It is a filter over names, never values:
  nothing here inspects what a variable holds.
- `DockerExecutor` forwards **no** host environment, mounts only `workdir` at `/work`, and
  runs with `--network none` unless constructed with `network=True`. Each `run` is a fresh
  container, so no state carries between commands except the mounted tree.
- `docker_command` is pure and exported for exactly that reason: the argv shape is the
  security surface, so it is asserted in tests without Docker installed.
- `docker_available()` runs `docker info`, not `which docker`: a CLI with no daemon is the
  common case on a developer box and must not be mistaken for a sandbox.
- Timeouts are in **seconds**. A command that times out is killed; its partial output is
  not recovered.

## Seam

`Executor` is the seam, and what varies is **how much isolation the host can give**:
a container (the designed answer, DESIGN.md decision 24) or a confined subprocess (the
fallback and what tests use). Both exist today, so the Protocol is real, not speculative.

`executor_for(..., sandbox=)` keeps the choice in one place: `"docker"` demands a
container and raises `SandboxUnavailableError` rather than silently downgrading;
`"auto"` downgrades with a WARNING, because a missing daemon should cost isolation, not
the run; `"local"` never consults Docker at all, which is what keeps tests fast.

Rejected: a single executor with an `in_docker: bool`. The two differ in what they can
promise, not in a flag, and the Protocol lets the gate and the agent tool share one
injected object.

## Dependencies

- **May import:** `pixibot.lib.logbook`, stdlib (`subprocess`, `shutil`, `pathlib`)
- **Must not import:** any other `pixibot` module — this is the bottom of the stack
- **Imported by:** `pixibot.workspace`, `pixibot.gates`, `pixibot.engine`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `SandboxUnavailableError` | `sandbox="docker"` and no working daemon | build the image, or accept `sandbox="auto"` |
| `ValueError` | `sandbox` is not auto/docker/local | fix the call; it is a programming error |

A failing or timed-out command is **not** an error: it is an `ExecResult` with a non-zero
`returncode`, because the agent that ran it needs the output, not an exception.

## Observability

- **WARNING** when a command times out — names the timeout and the first 120 characters
  of the command, which is what identifies the hung step in the run log.
- **WARNING** when `executor_for("auto")` finds no daemon and falls back to the host:
  the run is less isolated than the design promises, and the operator should know once.
- **No INFO per command.** The agent loop already logs every tool call with its result
  (`pixibot.lib.agent`), so a line here would double every entry.
- Never logged: the command's output (it can contain the whole project), and the
  environment — neither variable names nor values.

## Tests

- a successful command reports `ok` and its combined stdout+stderr
- a failing command reports its exit code rather than raising
- a command that exceeds its timeout returns 124 with "timed out" text
- `confined_env` withholds `ANTHROPIC_API_KEY` and any `*_TOKEN`, and keeps `PATH`
- a confined subprocess running `env` cannot see a key that is set in the parent
- `docker_command` mounts only the workspace, forwards no environment, disables network
- `executor_for("local")` never calls `docker_available`
- `executor_for("auto")` returns a container when the daemon answers, else a subprocess
- `executor_for("docker")` raises `SandboxUnavailableError` when the daemon is absent
- an unknown sandbox mode raises `ValueError`

## Deletion test

Deleting this module pushes `subprocess.run` into `workspace.run` and into `gates`
separately — two copies of the timeout handling, two copies of the secret filter, and no
way to put either inside a container without writing it twice. It is also the only place
that can answer "was this run actually sandboxed?". It earns its keep.
