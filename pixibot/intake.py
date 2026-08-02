"""Intake form — the markdown form an agent hands the user to specify a build.

`render_form()` returns the blank template (the TPM presents it up front, which
also satisfies the "give the full spec up front" rule for hard development).
`parse_form()` best-effort parses a filled template into the strict-input dict
(DESIGN.md §6). Missing/placeholder fields are left blank for validation to flag.
"""

from __future__ import annotations

import re

FORM_TEMPLATE = """# Pixibot Build Request

Fill this in and hand it back (`/build-from <file.md>`), or run `/build <objective>`
for the quick path.

## Objective
<one clear sentence: what should Pixibot build?>

## Target
- Language: <e.g. python>
- Framework: <e.g. flask, or none>
- Platform: <e.g. linux>

## Acceptance criteria
- <how we know it's done — one per line>

## Constraints
- <hard rules: perf, no external deps, license... one per line, or "none">

## Non-goals
- <explicitly out of scope, or "none">

## Budget
- Max depth: <junior | senior | principal>
- Compute: <token/$ ceiling, e.g. 200000>
- Scope: <e.g. single service>

## Hard development?
<yes | no>

> Set **yes** for the hardest, long-horizon work: principal agents run on Claude
> Fable 5 at xhigh effort. For hard builds, specify the full task up front — it
> materially improves autonomous, long-horizon results.
"""


def render_form() -> str:
    return FORM_TEMPLATE


def _sections(md: str) -> dict:
    out: dict[str, str] = {}
    for chunk in re.split(r"^##\s+", md, flags=re.M)[1:]:
        head, _, body = chunk.partition("\n")
        out[head.strip().lower()] = body.strip()
    return out


def _clean(v: str) -> str:
    v = (v or "").strip()
    return "" if not v or v.startswith("<") else v


def _bullets(body: str) -> list[str]:
    items = [re.sub(r"^[-*]\s*", "", ln).strip()
             for ln in body.splitlines() if ln.strip().startswith(("-", "*"))]
    return [i for i in items if i and not i.startswith("<") and i.lower() != "none"]


def _kv(body: str) -> dict:
    d = {}
    for ln in body.splitlines():
        m = re.match(r"^[-*]?\s*([A-Za-z ]+):\s*(.+)$", ln)
        if m:
            d[m.group(1).strip().lower()] = m.group(2).strip()
    return d


def _num(v: str, default: int = 200000) -> int:
    m = re.search(r"\d+", v or "")
    return int(m.group()) if m else default


def parse_form(md: str) -> dict:
    s = _sections(md)
    obj_first = (s.get("objective", "").splitlines() or [""])[0]
    target = _kv(s.get("target", ""))
    budget = _kv(s.get("budget", ""))
    hard = "yes" in s.get("hard development?", "").lower()
    return {
        "objective": _clean(obj_first),
        "target": {
            "language": _clean(target.get("language", "")),
            "framework": _clean(target.get("framework", "")),
            "platform": _clean(target.get("platform", "")),
        },
        "constraints": _bullets(s.get("constraints", "")),
        "non_goals": _bullets(s.get("non-goals", "")),
        "acceptance_criteria": _bullets(s.get("acceptance criteria", "")),
        "review_cadence": "per-feature",
        "budget_ceilings": {
            "compute": _num(budget.get("compute", "")),
            "max_depth": _clean(budget.get("max depth", "")) or ("principal" if hard else "senior"),
            "scope": _clean(budget.get("scope", "")) or "as needed",
        },
        "hard": hard,
    }
