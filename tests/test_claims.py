"""The gate that holds a contract to its module.

Every case writes a contract and a module into a temporary directory and asks
`claims` what is false about the pair. Nothing reaches past the interface: the eight
steps behind it are private, and a test that needed one of them would be saying the
module is the wrong shape.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "check_claims", ROOT / "scripts/check_claims.py")
assert _SPEC is not None and _SPEC.loader is not None
check_claims = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_claims)


def _contract(interface: str, dependencies: str) -> str:
    return ("# Contract: test" + chr(10) * 2
            + "## Interface" + chr(10) * 2
            + "```python" + chr(10) + interface + chr(10) + "```" + chr(10) * 2
            + "## Dependencies" + chr(10) * 2 + dependencies + chr(10))


class Claims(unittest.TestCase):
    def check(self, *, dotted: str = "pixibot.thing", interface: str = "",
              dependencies: str = "", source: str = "") -> list[str]:
        with TemporaryDirectory() as tmp:
            contract = Path(tmp) / (dotted + ".md")
            module = Path(tmp) / (dotted.rsplit(".", 1)[-1] + ".py")
            contract.write_text(_contract(interface, dependencies), encoding="utf-8")
            module.write_text(source, encoding="utf-8")
            return check_claims.claims(contract, module)

    # ── the interface half ───────────────────────────────────────────────────
    def test_a_declared_name_the_module_does_not_define_is_reported(self):
        """The drift that bites: a contract promising a function that is gone."""
        problems = self.check(interface="def gone() -> None", source="_x = 1" + chr(10))
        self.assertEqual(1, len(problems), problems)
        self.assertIn("'gone'", problems[0])

    def test_a_public_name_no_contract_declares_is_reported(self):
        problems = self.check(source="def surprise() -> None:" + chr(10) + "    pass" + chr(10))
        self.assertEqual(1, len(problems), problems)
        self.assertIn("'surprise'", problems[0])

    def test_a_private_name_is_not_a_promise(self):
        self.assertEqual([], self.check(
            source="def _helper() -> None:" + chr(10) + "    pass" + chr(10)))

    def test_an_imported_name_is_not_a_definition(self):
        """Otherwise every `from pathlib import Path` would be a public promise."""
        self.assertEqual([], self.check(source="from pathlib import Path" + chr(10)))

    def test_a_method_is_the_class_business_not_a_module_promise(self):
        """A contract lists methods under their class. Only top-level names are
        module-level promises, so an indented `def` is never demanded twice."""
        interface = ("class Agent:" + chr(10)
                     + "    def act(self) -> None" + chr(10)
                     + "    tool_defs: list[dict]")
        source = ("class Agent:" + chr(10)
                  + "    def act(self) -> None:" + chr(10) + "        pass" + chr(10))
        self.assertEqual([], self.check(interface=interface, source=source))

    def test_names_sharing_one_line_are_all_declared(self):
        """Constants are written `KIND_A = "a"; KIND_B = "b"` in these contracts.
        Reading only the first would report the rest as undeclared."""
        self.assertEqual([], self.check(
            interface='KIND_A = "a"; KIND_B = "b"',
            source='KIND_A = "a"' + chr(10) + 'KIND_B = "b"' + chr(10)))

    def test_a_name_declared_with_a_type_and_no_value_counts(self):
        """`TABLE: dict[str, int]` is how a contract states a constant it does not
        assign, and a bare `TABLE` is the terser form. Both are promises."""
        self.assertEqual([], self.check(
            interface="TABLE: dict[str, int]", source="TABLE = {}" + chr(10)))
        self.assertEqual([], self.check(
            interface="TABLE", source="TABLE = {}" + chr(10)))

    # ── the dependency half ──────────────────────────────────────────────────
    def test_a_forbidden_import_is_reported_even_inside_a_function(self):
        """An import hidden in a function body is still a dependency."""
        source = ("def _launch() -> None:" + chr(10)
                  + "    from pixibot.tui import run_tui" + chr(10))
        problems = self.check(dependencies="- **Must not import:** `pixibot.tui`",
                              source=source)
        self.assertTrue(problems)
        self.assertTrue(all("pixibot.tui" in p for p in problems), problems)

    def test_a_module_scope_claim_permits_a_deliberate_deferral(self):
        """`pixibot.cli` launches the TUI that imports it back. The contract says
        which it means, and only this form allows the function-local import."""
        source = ("def _launch() -> None:" + chr(10)
                  + "    from pixibot.tui import run_tui" + chr(10))
        self.assertEqual([], self.check(
            dependencies="- **Must not import at module scope:** `pixibot.tui`",
            source=source))

    def test_a_module_scope_claim_still_catches_a_top_level_import(self):
        problems = self.check(
            dependencies="- **Must not import at module scope:** `pixibot.tui`",
            source="from pixibot.tui import run_tui" + chr(10))
        self.assertTrue(problems)
        self.assertIn("at module scope", problems[0])

    def test_a_relative_import_resolves_against_its_own_package(self):
        """`from ..engine import Engine` inside pixibot/lib is an import of
        pixibot.engine, and must read the same as the absolute form."""
        problems = self.check(
            dotted="pixibot.lib.thing",
            dependencies="- **Must not import:** `pixibot.engine`",
            source="from ..engine import Engine" + chr(10))
        self.assertTrue(problems)
        self.assertIn("pixibot.engine", problems[0])

    def test_any_other_module_means_other_than_the_allowed_ones(self):
        """`pixibot.runtime` is the bottom of the stack and may still import the
        log. Its contract says both, and a blanket forbid that ignored the
        allowance would report it for doing exactly what it is permitted."""
        deps = ("- **May import:** `pixibot.lib.logbook`, stdlib" + chr(10)
                + "- **Must not import:** any other `pixibot` module")
        self.assertEqual([], self.check(
            dependencies=deps,
            source="from pixibot.lib.logbook import get" + chr(10)))

    def test_a_blanket_forbid_still_catches_an_unlisted_module(self):
        deps = ("- **May import:** `pixibot.lib.logbook`, stdlib" + chr(10)
                + "- **Must not import:** any other `pixibot` module")
        problems = self.check(
            dependencies=deps, source="from pixibot.engine import Engine" + chr(10))
        self.assertTrue(problems)
        self.assertIn("pixibot.engine", problems[0])

    def test_the_rationale_after_the_dash_is_prose_not_a_claim(self):
        """A forbid line explains itself after an em dash. The explanation may name
        any module without forbidding it."""
        deps = ("- **Must not import:** `pixibot.cli` " + chr(8212)
                + " the engine is what `pixibot.tui` calls")
        self.assertEqual([], self.check(
            dependencies=deps, source="from pixibot.tui import run_tui" + chr(10)))

    # ── the repo itself ──────────────────────────────────────────────────────
    def test_every_contract_in_the_repo_is_true_of_its_module(self):
        """The gate, run over the real tree. This is the regression that holds even
        when the hook is skipped."""
        self.assertEqual(0, check_claims.main())


if __name__ == "__main__":
    unittest.main()
