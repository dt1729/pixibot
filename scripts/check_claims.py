#!/usr/bin/env python3
"""Prove each contract's claims about its module against the module's source.

``check_contracts.py`` reads a contract as a document: the _sections are present, the
header is filled in, nothing is a placeholder. It never opens the code. So a contract
may name a function that no longer exists, or forbid an import the module makes on its
first line, and the gate still passes. That is the drift the second lld-seed pass
reported most of (18 of 77 findings), and incident 010 is what it costs.

This gate parses each module with `ast` and holds the contract to two kinds of claim:

1. **Interface** - every top-level name the ``## Interface`` code block declares exists
   in the module, and every public top-level name the module defines is declared.
   Top-level only: a class's methods are the class's business, and the prose covers
   them. The direction that bites is a declared name that is gone; the reverse catches
   a public name nobody wrote down, which is how `claude_cli` arrived unnamed in M47.
2. **Dependencies** - the ``Must not import`` line is true of the real _imports. Two
   forms are read, and the difference is deliberate:

   - ``Must not import:`` forbids the import anywhere in the file, a function body
     included. An import hidden inside a function is still a dependency.
   - ``Must not import at module scope:`` permits a stated deferral. `pixibot.cli`
     launches the TUI from inside `main`, and the TUI _imports the session back, so a
     module-scope import would be a cycle. The contract must say which it means.

What this cannot check: depth, whether a seam has two live implementations, the
deletion test. Those stay human, and the lld-seed standard says so.

It is not a replacement for import-linter. This reads one file's own _imports.
import-linter walks the whole graph and catches indirect reach, which no single file
shows: 7 graph-level contracts there, every directly declared direction here.

Run: python3 scripts/check_claims.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "docs/contracts"
ROOT_PACKAGE = "pixibot"

#: An interface claim ends at the rationale, which is prose and may name anything.
EM_DASH = chr(8212)

_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_DECL_DEF = re.compile(r"^(?:async\s+)?def\s+(\w+)", re.MULTILINE)
_DECL_CLASS = re.compile(r"^class\s+(\w+)", re.MULTILINE)
#: A contract declares a constant three ways: `NAME = value`, `NAME: type` (the
#: common one, since a contract states the type and not the value) and a bare
#: `NAME`. All three are promises.
_DECL_ASSIGN = re.compile(r"^([A-Za-z]\w*)[ \t]*(?:[:=]|$)", re.MULTILINE)
_BACKTICKED = re.compile(r"`(" + ROOT_PACKAGE + r"(?:\.\w+)*)`")
_MUST_NOT = re.compile(
    r"^-\s+\*\*Must not import(?P<scoped> at module scope)?:\*\*(?P<body>.*?)"
    r"(?=^-\s+\*\*|\Z)",
    re.MULTILINE | re.DOTALL,
)
_MAY = re.compile(r"^-\s+\*\*May import:\*\*(?P<body>.*?)(?=^-\s+\*\*|\Z)",
                  re.MULTILINE | re.DOTALL)

#: Names a contract is not expected to declare: re-exported for callers' convenience
#: or demanded by a protocol, never called through this module's own interface.
#: Each entry is a deliberate exemption, not a backlog.
UNDECLARED_OK: dict[str, frozenset[str]] = {}


def _sections(text: str) -> dict[str, str]:
    """Contract text split by `## ` heading, lowercased keys."""
    matches = list(_SECTION.finditer(text))
    out: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[match.group(1).strip().lower()] = text[match.end():end].strip()
    return out


def _declared_names(interface: str) -> set[str]:
    """Top-level names the ## Interface code blocks promise.

    Only lines at zero indentation count, so a method listed inside a `class` block
    stays the class's business rather than becoming a module-level promise.
    """
    names: set[str] = set()
    for block in _FENCE.findall(interface):
        for line in block.splitlines():
            if not line or line[0].isspace():
                continue                       # a method or field, not a module name
            # Constants share a line: `KIND_ARTIFACT = "artifact"; KIND_MESSAGE = ...`.
            # A `class` or `def` line is never split, or its own fields would read as
            # module-level promises.
            parts = ([line] if line.startswith(("class", "def", "@", "async"))
                     else [part.strip() for part in line.split(";")])
            for pattern in (_DECL_DEF, _DECL_CLASS, _DECL_ASSIGN):
                names.update(pattern.findall("\n".join(parts)))
    return names


def _defined_names(tree: ast.Module) -> set[str]:
    """Public top-level names the module actually defines.

    Imports are excluded: a re-export is not a definition, and counting one would make
    every `from pathlib import Path` a public promise.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return names


def _forbidden(dependencies: str) -> tuple[frozenset[str], frozenset[str]]:
    """(forbidden anywhere, forbidden at module scope) from the Dependencies section.

    "anything in `pixibot`" and "any other `pixibot` module" both reduce to the root
    package, which forbids every submodule by the prefix rule in `_violations`.
    """
    anywhere: set[str] = set()
    scoped: set[str] = set()
    for match in _MUST_NOT.finditer(dependencies):
        claim = match.group("body").split(EM_DASH)[0]
        (scoped if match.group("scoped") else anywhere).update(_BACKTICKED.findall(claim))
    return frozenset(anywhere), frozenset(scoped)


def _allowed(dependencies: str) -> frozenset[str]:
    """Modules the `May import` line permits.

    This is what makes "any other `pixibot` module" mean anything: `pixibot.runtime`
    is the bottom of the stack and may still import the log, and its contract says
    both. A blanket forbid is read as "everything except what is allowed above";
    a forbid that names a module outright is absolute.
    """
    match = _MAY.search(dependencies)
    return frozenset(_BACKTICKED.findall(match.group("body"))) if match else frozenset()


def _resolve(package: str, node: ast.ImportFrom) -> list[str]:
    """Every module path a `from ... import ...` could name, relative resolved.

    Both the module and each imported name are returned, because `from .tui import
    run_tui` and `from . import tui` must read the same.
    """
    if node.level == 0:
        base = node.module or ""
    else:
        parts = package.split(".") if package else []
        keep = len(parts) - (node.level - 1)
        base = ".".join(parts[:keep]) if keep > 0 else ""
        if node.module:
            base = f"{base}.{node.module}" if base else node.module
    if not base:
        return []
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


def _imports(package: str, tree: ast.Module) -> list[tuple[str, bool]]:
    """(dotted module path, is at module scope) for every import in the file."""
    top = {id(node) for node in tree.body}
    found: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, id(node) in top) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found += [(path, id(node) in top) for path in _resolve(package, node)]
    return found


def _covers(target: str, path: str) -> bool:
    return path == target or path.startswith(target + ".")


def _violations(dotted: str, tree: ast.Module, dependencies: str) -> list[str]:
    """Imports the contract forbids and the module makes anyway."""
    anywhere, scoped = _forbidden(dependencies)
    permitted = _allowed(dependencies)
    package = dotted.rsplit(".", 1)[0]
    out = []
    for path, at_module_scope in _imports(package, tree):
        if _covers(dotted, path):                      # a module importing itself
            continue
        for target in anywhere:
            if target == ROOT_PACKAGE and any(_covers(ok, path) for ok in permitted):
                continue                               # "any OTHER module"
            if _covers(target, path):
                out.append(f"imports {path!r}, which its contract forbids ({target})")
        if at_module_scope:
            for target in scoped:
                if _covers(target, path):
                    out.append(f"imports {path!r} at module scope, which its contract "
                               f"forbids ({target})")
    return sorted(set(out))


def claims(contract: Path, module: Path) -> list[str]:
    """Why this contract is not true of this module. Empty means it holds."""
    found = _sections(contract.read_text(encoding="utf-8"))
    dotted = contract.stem          # the file name IS the dotted module path
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    declared = _declared_names(found.get("interface", ""))
    defined = _defined_names(tree)
    problems = [f"## Interface declares {name!r}, which the module does not define"
                for name in sorted(declared - defined)]
    problems += [f"defines public {name!r}, which ## Interface never declares"
                 for name in sorted(defined - declared - UNDECLARED_OK.get(dotted, frozenset()))]
    return problems + _violations(dotted, tree, found.get("dependencies", ""))


def _pairs() -> list[tuple[Path, Path]]:
    """(contract, module) for every contract that names a module in the tree."""
    out = []
    for contract in sorted(CONTRACTS.glob("*.md")):
        if contract.name.startswith("_"):
            continue
        module = ROOT / (contract.stem.replace(".", "/") + ".py")
        if module.exists():
            out.append((contract, module))
    return out


def main() -> int:
    problems: list[str] = []
    checked = _pairs()
    for contract, module in checked:
        for problem in claims(contract, module):
            problems.append(f"{module.relative_to(ROOT)}: {problem}")

    if problems:
        print(f"{len(problems)} false claim(s):")
        for problem in problems:
            print("  -", problem)
        return 1
    print(f"claims: {len(checked)} contracts true of their modules "
          f"(interface names and import directions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
