# Programming standards (v0)

Judgment standards Pixibot's coding agents read on demand. Mechanical checks
(linters, type-checkers, tests) are enforced separately at checkpoints (§10).

- **Clarity over cleverness.** Code is read far more than it is written.
- **No premature abstraction.** Solve the task in front of you; don't build for
  hypothetical futures. A bug fix doesn't need surrounding cleanup.
- **Validate at boundaries only.** Trust internal code and framework guarantees;
  validate user input and external API responses.
- **Errors are values to handle, not decoration.** No empty `except`/`catch`.
  Fail loudly at the boundary; recover deliberately.
- **Small, named, single-purpose functions.** Prefer composition over deep nesting.
- **Match the surrounding code** — naming, idiom, comment density.
- **Dependencies are liabilities.** Prefer the standard library; justify each new one.
