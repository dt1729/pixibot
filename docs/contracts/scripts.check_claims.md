# Contract: scripts.check_claims

**Status:** accepted
**Depth:** senior

## Responsibility

Prove that every claim a contract makes about its module is true of the code.

## Interface

```python
ROOT: Path                        # the repo, found from this file
CONTRACTS: Path                   # docs/contracts
ROOT_PACKAGE = "pixibot"
EM_DASH: str                      # where a forbid line stops claiming and starts explaining
UNDECLARED_OK: dict[str, frozenset[str]]   # stated exemptions; empty, and meant to stay so

def claims(contract: Path, module: Path) -> list[str]
def main() -> int
```

- `claims` returns one line per false claim and `[]` when the contract holds. It takes
  the dotted module path from the **contract's file name**, which is what that name is,
  so it needs no repo root and can check any pair — which is how the tests reach it.
- Two kinds of claim are checked. **Interface**: every top-level name the
  `## Interface` code block declares exists, and every public top-level name the module
  defines is declared. **Dependencies**: the `Must not import` line is true.
- Only **top-level** names are module promises. A method listed under a `class` in the
  contract is the class's business; demanding it twice would make the gate noisy and the
  contract repetitive.
- A contract declares a constant three ways — `NAME = value`, `NAME: type` and a bare
  `NAME` — and several to a line, separated by `;`. All of those are promises. A `class`
  or `def` line is never split on `;`, or its own fields would read as module names.
- `Must not import:` forbids the import **anywhere**, a function body included: an import
  hidden in a function is still a dependency. `Must not import at module scope:` is the
  only form that permits a deliberate deferral, and exists because `pixibot.cli` launches
  the TUI that imports it back.
- "any other `pixibot` module" is read against the `May import` line above it. Without
  that, `pixibot.runtime` — which may import the log and says so — would be reported for
  doing exactly what it is permitted.
- A forbid line explains itself after an em dash. Everything after the dash is prose and
  claims nothing, so a rationale may name any module without forbidding it.
- Relative imports resolve against the module's own package, so `from ..engine import X`
  inside `pixibot/lib` reads the same as the absolute form.

## Seam

No seam, and deliberately none. The pair to check is passed in as two paths, which is the
whole seam a test needs; `main` supplies the real pairs. A `root` parameter would let a
caller point the gate at a different tree, which is not a thing anyone needs, and
`claims` already avoids the repo root entirely by reading the dotted path off the
contract's name.

Rejected: importing `pixibot` to inspect modules by reflection. A gate that imports what
it checks fails to run exactly when the package is broken, which is when it is needed.
`ast.parse` reads the source and has no opinion about whether it runs.

Rejected: enforcing the `May import` line **exhaustively** — that every intra-package
import appears on it. It is the natural next rule and three of those lines were already
stale at M51 (`engine`, `providers`, `lib.orchestrator` had each grown an import nobody
listed). It is not done here because the line also carries prose about stdlib, and the
rule needs its own pass over all 23 contracts rather than being half-applied.

## Dependencies

- **May import:** stdlib (`ast`, `re`, `pathlib`) only
- **Must not import:** anything in `pixibot` — a gate that imports what it checks breaks
  when its subject breaks
- **Imported by:** nothing; `.pre-commit-config.yaml` runs it, and `tests/test_claims.py`
  loads it by path

## Errors

| Type | Raised when | Caller response |
| --- | --- | --- |
| `SyntaxError` | a module does not parse | propagates; ruff and mypy fail first, and an unparseable module is not a contract question |
| `FileNotFoundError` | a contract names a module that is not there | cannot happen through `main`, which pairs a contract with an existing file; a direct caller passing a bad path gets the stdlib error |

`claims` raises nothing for a false claim. A false claim is a return value, because the
gate reports every problem at once rather than stopping at the first.

## Observability

No logging. It is a gate: the exit code is the result, and every false claim is printed
with the module path and the name or import at fault. A log level would add a second
channel to a program whose whole output is its verdict.

## Tests

- a declared name the module does not define is reported
- a public name no contract declares is reported
- a private name, and an imported one, are not promises
- a method is the class's business, not a module promise
- names sharing one line are all read, and a typed or bare declaration counts
- a forbidden import is caught inside a function body
- the module-scope form permits a deferral and still catches a top-level import
- a relative import resolves against its own package
- "any other module" respects the `May import` line, and still catches an unlisted module
- the rationale after an em dash claims nothing
- every contract in the real repo is true of its module

## Deletion test

Deleting this gate returns the repo to M50, where `check_contracts.py` read the contracts
as documents and never opened the code. A contract could then name a function that was
gone, or forbid an import made on line one, and five green gates would say nothing. It
found eleven false claims on its first run, including a class M50 believed it had deleted
and three type aliases declared twice. Those are not failures a behavioural test can see:
a dead name breaks nothing.
