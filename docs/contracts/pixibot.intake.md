# Contract: pixibot.intake

**Status:** accepted
**Depth:** junior

## Responsibility

Turn a filled-in markdown form into the strict input a run needs.

## Interface

```python
FORM_TEMPLATE: str

def render_form() -> str
def parse_form(md: str) -> dict[str, Any]
```

- `render_form()` returns the blank form to fill in; the shell prints it for `form`.
- `parse_form` is **forgiving by design**: a human wrote the file. Headings are matched
  case-insensitively, bullets may use `-` or `*`, placeholder text (`<like this>`) and the
  word "none" are dropped, and a missing numeric budget falls back to a default.
- It produces the strict-input shape but does **not** validate it: the caller runs
  `schema.validate_input` and shows the errors, so the message a user sees names the form
  field rather than a JSON path from inside a parser.
- `hard` is read from the "Hard development?" section, which is how a user asks for
  principal agents at `xhigh` effort (decision 36).
- Round trip: `parse_form(render_form())` yields the shape with empty collections, not an
  error — an unfilled form is a user mistake to report, not a crash.

## Seam

No seam: text in, dict out. The variation that might one day exist (another input format)
belongs beside this module, not behind an abstraction inside it — both would produce the
same dict, which is the real interface.

Rejected: parsing into a dataclass. The consumer is `schema.validate_input` and the TPM
prompt, both of which want the dict the schema describes; a dataclass would be converted
back immediately.

## Dependencies

- **May import:** stdlib only (`re`)
- **Must not import:** `pixibot.schema` (the caller validates), `pixibot.cli`
- **Imported by:** `pixibot.cli`, `pixibot.tui`

## Errors

No error modes. Every malformed form produces a dict with missing or empty fields, which
the caller's validation turns into a readable list. A parser that raised would make the
shell's `build-from` fail with a traceback instead of a list of what to fill in.

## Observability

No logging. The parse happens in response to a command and its result is shown
immediately: either the build starts or the missing fields are listed.

## Tests

- a filled form parses into objective, target, constraints, criteria and budget
- placeholder text and "none" are dropped rather than becoming constraints
- bullets with `-` and `*` both parse
- a missing compute budget falls back to the default rather than raising
- "Hard development? yes" sets `hard`
- the blank template parses without raising (and fails validation, which is the caller's job)

## Deletion test

Deleting this module leaves only one-line objectives at the shell: the form is how a user
states constraints, non-goals and acceptance criteria, which are exactly the fields the
TPM plans from. Its tolerance (placeholders, "none", bullet styles) would otherwise live
in the CLI command, mixed with argument handling. It earns its keep.
