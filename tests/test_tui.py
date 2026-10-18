"""Tests for the TUI's wiring (skipped when textual is not installed).

Rendering is not tested here; what is tested is the contract between the TUI and
the session - that every slow command goes through ChatSession and that the key
table covers the keys the shell documents.
"""

import unittest

try:
    from pixibot import tui
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class KeyTableTest(unittest.TestCase):
    def test_documented_editing_keys_are_all_bound(self):
        for key in ("up", "down", "tab", "ctrl+a", "ctrl+e", "ctrl+u", "ctrl+k", "ctrl+w"):
            self.assertIn(key, tui.CommandInput.KEYS)

    def test_unbound_keys_fall_through(self):
        self.assertNotIn("ctrl+q", tui.CommandInput.KEYS)   # the app owns quit
        self.assertNotIn("left", tui.CommandInput.KEYS)     # textual owns editing


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class LocalCommandTest(unittest.TestCase):
    def test_local_commands_are_a_subset_of_the_shell_commands(self):
        from pixibot.cli import ChatSession
        known = set(ChatSession.DISPATCH) | set(ChatSession.ALIASES)
        self.assertTrue(known >= tui._LOCAL, tui._LOCAL - known)

    def test_build_is_not_local(self):
        # builds must go to the worker, or the UI freezes for minutes
        for slow in ("build", "revise", "tell", "ask"):
            self.assertNotIn(slow, tui._LOCAL)


if __name__ == "__main__":
    unittest.main()
