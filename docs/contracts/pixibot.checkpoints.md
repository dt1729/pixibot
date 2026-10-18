# Contract: pixibot.checkpoints

**Status:** accepted
**Depth:** senior

## Responsibility

Say which declared stopping points are due, and carry the verdict back.

## Interface

```python
HUMAN_GATE = "user_feedback"

@dataclass(frozen=True)
class Checkpoint:
    id: str
    demo: str                       # what to show, in the TPM's words
    after: tuple[str, ...] = ()     # agents that must be DONE first
    gate: str = HUMAN_GATE
    @property
    def human(self) -> bool

@dataclass(frozen=True)
class Review:
    accepted: bool
    feedback: str = ""
    @classmethod
    def accept(cls) -> Review
    @classmethod
    def revise(cls, feedback: str) -> Review

Reviewer = Callable[[Checkpoint, str], Review]   # (checkpoint, gate summary) -> verdict

def parse(projection: Mapping[str, Any]) -> tuple[Checkpoint, ...]
def due(checkpoints: Iterable[Checkpoint], completed: Iterable[str],
        *, already_passed: Iterable[str] = ()) -> tuple[Checkpoint, ...]
```

- `parse` **never raises on a loose projection**: a missing `id` is numbered by position,
  a missing `demo` says so rather than inventing one, a string `after` is accepted as a
  one-element list, and a non-dict entry is skipped. The TPM is a language model.
- `gate` defaults to `HUMAN_GATE`: a checkpoint that does not say otherwise needs a person,
  which is the safe reading of decision 12 (termination is human-gated).
- A checkpoint with an empty `after` is due **at quiescence** — the TPM left the trigger
  open, and "at the end" is the honest interpretation.
- `due` is ordered as the projection declared them, so a run asks about its earliest
  checkpoint first.
- `Review.feedback` is only meaningful when `accepted` is False; an accepted review carries
  no text, because a compliment is not an instruction.
- Both functions are pure: nothing here runs a gate, shows a demo, or waits. The ordering
  rule that matters — mechanical gate first, demo second — is enforced by the one caller
  (`Engine._review_loop`), which is the only place that can run a gate.

## Seam

`Reviewer` is the seam, and what varies is *who answers*: a person at a terminal
(`cli.ask_at_checkpoint`, which blocks on input), a scripted verdict in tests, and
nothing at all for an unattended run (`reviewer=None`, which skips the pause entirely).
That third case is why the parameter is optional rather than a required collaborator.

Rejected: letting the engine call `input()` itself. It would make every build
terminal-bound — unusable from the TUI's worker thread, from a benchmark, or from a test.

Rejected: a `Checkpoint.run()` that gates and asks. It would need the gate, the workspace
and the TPM, which is the engine's whole collaborator set; the record would stop being a
record.

## Dependencies

- **May import:** stdlib only
- **Must not import:** `pixibot.engine`, `pixibot.gates`, `pixibot.cli` — a checkpoint
  describes a stopping point; it does not know what runs or who looks
- **Imported by:** `pixibot.engine`, `pixibot.cli`

## Errors

No error modes. Every projection shape has a defined reading, and a verdict is a value.
The engine is where a review can fail to converge, and it bounds that with
`max_review_rounds` rather than raising.

## Observability

- **No logging.** These are pure functions over a plan; the caller logs each checkpoint at
  **INFO** with the run id, the checkpoint id, the round number and the gate verdict, and
  at **WARNING** when a checkpoint runs out of review rounds without being accepted.
- The verdict itself is also appended to the blackboard as a control event by the engine,
  so `report` and the Observer can show that a human accepted (or what they asked for).
- Never logged here or by the caller: the demo text or the feedback text in full — the
  feedback goes to the TPM, and the blackboard already holds it.

## Tests

- a full checkpoint entry parses into its four fields and reads as human-gated
- a projection with no checkpoints, or `None`, parses to an empty tuple
- a checkpoint missing every field is numbered, says it has no demo, and is human-gated
- a single-string `after` is accepted
- a non-human `gate` value reads as mechanical-only
- junk entries (a string, a number) are skipped rather than raising
- only checkpoints whose agents have all finished are due
- a checkpoint already passed is not offered again
- a checkpoint with no `after` is due at quiescence

## Deletion test

Deleting this module leaves decision 12 unimplemented: runs would end on quiescence, and
the projection's `checkpoints` field would stay decorative — which is what it was before
this landed. The parsing tolerance would otherwise end up inline in the engine's review
loop, where "what if the TPM omitted the demo?" is six branches away from the gate logic.
It earns its keep.
