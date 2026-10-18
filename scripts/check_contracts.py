#!/usr/bin/env python3
"""Check that every public module has a contract and guidance, and that each contract
is usable.

Three rules, all mechanical:

1. **Contract coverage** — every module in ``pixibot/`` (the public surface) has a
   contract at ``docs/contracts/<dotted.path>.md``. Modules under ``pixibot/lib/`` are
   internal, so a contract there is welcome but not required.
2. **Guidance coverage** — ``pixibot/CLAUDE.md`` names every public module. A module the
   folder guidance never mentions is invisible to the next reader, which is how
   ``claude_cli`` arrived in M47 and sat unnamed until the guidance pass was re-run.
3. **Structure** — a contract carries every required section, a ``Status`` and ``Depth``,
   at least one signature in a code block, a stated import direction, an error table (or
   the explicit "No error modes."), a logging level (or the explicit "No logging."), and
   at least three test behaviours.

Structure is all that can be checked. A contract that passes and says nothing is a failure
of its author, which is why the sections ask for specifics.

Run: python3 scripts/check_contracts.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "pixibot"
CONTRACTS = ROOT / "docs/contracts"
GUIDANCE = PACKAGE / "CLAUDE.md"

#: Entry-point plumbing with no interface of its own.
EXEMPT = {"__init__", "__main__"}

REQUIRED_SECTIONS = ("Responsibility", "Interface", "Seam", "Dependencies",
                     "Errors", "Observability", "Tests", "Deletion test")
VALID_STATUS = {"draft", "accepted", "superseded"}
VALID_DEPTH = {"junior", "senior", "principal"}
LOG_LEVELS = ("debug", "info", "warning", "error")
MIN_TEST_BEHAVIOURS = 3

_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"^(TODO|TBD|\.\.\.|\{\{[^}]+\}\})\s*$", re.MULTILINE)


def sections(text: str) -> dict[str, str]:
    matches = list(_HEADING.finditer(text))
    out: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[match.group(1).strip().lower()] = text[match.end():end].strip()
    return out


def field(text: str, name: str) -> str | None:
    match = re.search(rf"^\*\*{name}:\*\*\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip().lower() if match else None


def _check_sections(found: dict[str, str]) -> list[str]:
    """Every required heading is present and says something."""
    errors = []
    for required in REQUIRED_SECTIONS:
        body = found.get(required.lower())
        if body is None:
            errors.append(f"missing section: ## {required}")
        elif not body or _PLACEHOLDER.search(body):
            errors.append(f"section is empty or a placeholder: ## {required}")
    return errors


def _check_header(text: str) -> list[str]:
    """Status and Depth are declared, and from the allowed sets."""
    errors = []
    status, depth = field(text, "Status"), field(text, "Depth")
    if status not in VALID_STATUS:
        errors.append(f"Status must be one of {sorted(VALID_STATUS)}, got {status!r}")
    if depth not in VALID_DEPTH:
        errors.append(f"Depth must be one of {sorted(VALID_DEPTH)}, got {depth!r}")
    return errors


def _check_content(found: dict[str, str]) -> list[str]:
    """What each section must contain beyond being non-empty."""
    errors = []
    if (interface := found.get("interface", "")) and "```" not in interface:
        errors.append("## Interface has no signature in a code block")
    if (deps := found.get("dependencies", "")) and "may import" not in deps.lower():
        errors.append("## Dependencies has no 'May import' line")
    errs = found.get("errors", "").lower()
    if errs and "|" not in errs and "no error modes" not in errs:
        errors.append("## Errors needs a table, or the sentence 'No error modes.'")
    obs = found.get("observability", "").lower()
    if obs and "no logging" not in obs and not any(lv in obs for lv in LOG_LEVELS):
        errors.append("## Observability names no level, and does not say 'No logging.'")
    bullets = [ln for ln in found.get("tests", "").splitlines()
               if ln.lstrip().startswith(("-", "*"))]
    if bullets and len(bullets) < MIN_TEST_BEHAVIOURS:
        errors.append(f"## Tests lists {len(bullets)} behaviours; "
                      f"at least {MIN_TEST_BEHAVIOURS} are required")
    return errors


def validate(text: str) -> list[str]:
    """Why this contract is unusable. Empty means it validates."""
    found = sections(text)
    return [*_check_sections(found), *_check_header(text), *_check_content(found)]


def contract_for(module: Path) -> Path:
    """pixibot/lib/agent.py -> docs/contracts/pixibot.lib.agent.md"""
    dotted = ".".join(module.relative_to(ROOT).with_suffix("").parts)
    return CONTRACTS / f"{dotted}.md"


def public_modules() -> list[Path]:
    return sorted(p for p in PACKAGE.glob("*.py") if p.stem not in EXEMPT)


def unnamed_in_guidance() -> list[str]:
    """Public modules the folder guidance never mentions.

    Matched on the bare module name, which is how the guidance refers to them
    (`engine`, not `pixibot/engine.py`).
    """
    if not GUIDANCE.exists():
        return [m.stem for m in public_modules()]
    text = GUIDANCE.read_text(encoding="utf-8")
    return [m.stem for m in public_modules() if m.stem not in text]


def main() -> int:
    problems: list[str] = []

    for module in public_modules():
        contract = contract_for(module)
        if not contract.exists():
            problems.append(f"{module.relative_to(ROOT)}: no contract at "
                            f"{contract.relative_to(ROOT)}")

    for name in unnamed_in_guidance():
        problems.append(f"{GUIDANCE.relative_to(ROOT)}: never names the public module "
                        f"{name!r}")

    for contract in sorted(CONTRACTS.glob("*.md")):
        if contract.name.startswith("_"):      # the waiver ledger, not a contract
            continue
        for error in validate(contract.read_text(encoding="utf-8")):
            problems.append(f"{contract.relative_to(ROOT)}: {error}")

    count = len(list(CONTRACTS.glob("*.md")))
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print("  -", p)
        return 1
    print(f"contracts: {count} valid, every public module covered and named in guidance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
