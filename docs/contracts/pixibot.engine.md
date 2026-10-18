# Contract: pixibot.engine

**Status:** accepted
**Depth:** principal

## Responsibility

Own one steerable run: plan it, drive it, gate it, and let a human change it mid-flight.

## Interface

```python
ALL_FEATURES = frozenset({"continuous_testing", "auto_gate"})
DEFAULT_FEATURES = frozenset({"continuous_testing"})

def default_input(objective: str, *, hard: bool = False) -> dict

class Engine:
    def __init__(self, bb: Blackboard, tpm_model: Model, model_factory: ModelFactory,
                 *, max_steps: int = 200)
    # hooks the UI sets
    on_activation: ActivationHook | None; on_agent_start: StartHook | None
    on_event: EventHook | None
    # configuration
    workspace_base: str | None; sandbox: str = "auto"; features: set[str]
    max_gate_revises: int = 2
    # state
    workspace: Workspace | None; projection: dict | None; last_gate: GateReport | None

    def build(self, inp: dict) -> dict
    def build_objective(self, objective: str, *, hard: bool = False) -> dict
    def gate(self) -> GateReport
    def resume(self) -> int
    def tell(self, agent_id: str, directive: str) -> int
    def revise(self, feedback: str) -> int
    def report(self) -> str
```

- `build` is **destructive of nothing**: it allocates a fresh `run_id` and a fresh
  workspace directory, so a rebuild cannot overwrite an earlier run's files (decision 45).
  Prior runs stay on disk.
- `build` returns `{"projection", "steps", "workspace", "run_id"}`, plus
  `{"gate_passed", "gate"}` when the `auto_gate` arm is on and the project was checkable.
- `tell` and `revise` are **non-blocking in the agent's sense**: they post to the
  blackboard and resume the scheduler; no agent is interrupted mid-turn (§12).
- `revise` requires a prior `build` (it needs a projection to revise) and only registers
  agents the scheduler does not already know. New agents depend only on agents that have
  **already completed**, so they can start immediately.
- `resume` is safe to call at any time and returns 0 when there is nothing to drive.
- `gate` runs the mechanical gate once over the current workspace, in that workspace's
  sandbox, and records the report on `last_gate`.
- `sandbox` is `"auto"`, `"docker"` or `"local"` and is read at **workspace creation**:
  changing it after a build affects the next build, not this one.
- `features` toggles the ablation arms (decision 47). `auto_gate` is off by default
  because its re-plan loop is the largest cost multiplier in the system.

## Seam

The engine is a composition root, and its seams are the three things it is handed: a
`Blackboard`, a planning `Model` and a `ModelFactory` (which `pixibot.providers` builds).
That is what lets the whole run be exercised offline with scripted models.

The hooks (`on_activation`, `on_agent_start`, `on_event`) are the seam to the UI, kept as
plain callables so the TUI, the plain shell and the tests can each pass a function without
implementing an interface.

Deliberately **not** owned here: what the gate checks (`pixibot.gates` derives it from the
workspace), how agents are wired (`pixibot.lib.orchestrator`), and where commands run
(`pixibot.runtime`). Each of those was once an engine responsibility and each made the
engine a file you had to edit to add a language, a topology or a sandbox.

## Dependencies

- **May import:** `pixibot.gates`, `pixibot.observer`, `pixibot.lib.orchestrator`,
  `pixibot.lib.tpm`, `pixibot.lib.agent`, `pixibot.blackboard`, `pixibot.lib.context_manager`,
  `pixibot.model`, `pixibot.runtime`, `pixibot.workspace`, `pixibot.lib.logbook`
- **Must not import:** `pixibot.cli`, `pixibot.tui`, `pixibot.providers` — the UI calls
  the engine, and the provider is chosen before one is built
- **Imported by:** `pixibot.cli`, `pixibot.tui`, `pixibot.demo`

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `RuntimeError` | `revise` is called before any `build` | tell the user to build first |
| `ValidationFailed` | the TPM cannot produce a valid projection within its repair budget | show the validation errors; the objective usually needs narrowing |
| `SandboxUnavailableError` | `sandbox="docker"` and no daemon | build the image or accept `"auto"` |

A crashing agent is **not** an error here: the scheduler marks it BLOCKED and the run
continues, which is what keeps one bad activation from losing the whole build.

## Observability

- **INFO** `BUILD start` — run id, objective, workspace root. The line that ties a run to
  its files.
- **INFO** `BUILD run` — the agent ids the TPM planned, so a thin team is visible.
- **INFO** `BUILD done` — steps, file count, gate verdict.
- **INFO** `GATE` — one line per gate attempt: run id, attempt number, language, verdict.
- **ERROR** is never emitted from this module; failures that reach it propagate to the
  shell, which shows them to the user.
- Every line carries the run id, which is the key into both the workspace and the
  blackboard.
- Never logged: the objective's full text beyond the start line, file contents, or
  projection JSON.

## Tests

- a build runs the full cascade: every planned section gets an artifact
- a build allocates a fresh run id and workspace, leaving a previous run's files intact
- `tell` resumes the run and the addressed agent's artifact changes
- a dependent agent re-runs off the new artifact (the handshake), not just the addressee
- `revise` splices in a new agent and that agent produces its section
- `revise` before `build` raises `RuntimeError`
- `auto_gate` is absent from the default feature set; `continuous_testing` is present
- `gate()` on a project with no tests is not applicable
- `gate()` on a green suite passes and names the language
- `gate()` on a red suite fails and its summary names the failing test
- a C/C++ workspace is gated with cmake/ctest commands, not pytest

## Deletion test

Deleting this module leaves only the one-shot pipeline: no resume, no directive, no
revision, no per-build isolation, and no place for the gate's bounded re-plan loop. Every
interactive surface (shell, TUI) would grow its own copy of the build sequence. It earns
its keep.
