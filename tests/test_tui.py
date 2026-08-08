"""Tests for TUI pure helpers (skipped when textual isn't installed)."""

import unittest

try:
    from pixibot.tui import _common_prefix
    HAVE_TEXTUAL = True
except Exception:  # noqa: BLE001 - textual optional in some envs
    HAVE_TEXTUAL = False


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class CommonPrefixTest(unittest.TestCase):
    def test_shared_prefix(self):
        self.assertEqual(_common_prefix(["build", "builder", "build-from"]), "build")

    def test_single(self):
        self.assertEqual(_common_prefix(["status"]), "status")

    def test_none(self):
        self.assertEqual(_common_prefix([]), "")

    def test_no_common(self):
        self.assertEqual(_common_prefix(["ls", "cat"]), "")


if __name__ == "__main__":
    unittest.main()
