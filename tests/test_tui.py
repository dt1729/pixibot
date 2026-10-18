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


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class PaneReviewerTest(unittest.TestCase):
    """The TUI must not review through stdin: Textual owns the terminal."""

    def test_the_tui_reviews_in_a_pane_and_never_through_stdin(self):
        self.assertTrue(hasattr(tui.PixibotApp, "_pane_reviewer"))
        # the terminal reviewer is not imported here: Textual owns stdin
        self.assertFalse(hasattr(tui, "ask_at_checkpoint"))

    def test_an_answer_resolves_the_waiting_worker(self):
        app = tui.PixibotApp.__new__(tui.PixibotApp)   # no terminal needed
        app._review_answer = None
        app._review_ready = tui.threading.Event()
        app._awaiting_review = True
        app._shell = lambda text: None

        app._answer_review("accept")
        self.assertTrue(app._review_ready.is_set())
        self.assertTrue(app._review_answer.accepted)
        self.assertFalse(app._awaiting_review)

    def test_free_text_becomes_feedback(self):
        app = tui.PixibotApp.__new__(tui.PixibotApp)
        app._review_answer = None
        app._review_ready = tui.threading.Event()
        app._awaiting_review = True
        app._shell = lambda text: None

        app._answer_review("use a dataclass")
        self.assertFalse(app._review_answer.accepted)
        self.assertEqual(app._review_answer.feedback, "use a dataclass")


if __name__ == "__main__":
    unittest.main()
