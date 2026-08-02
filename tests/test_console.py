"""Tests for the console styling helpers (color disabled under test/pipe)."""

import unittest

from pixibot import console


class ConsoleTest(unittest.TestCase):
    def test_c_is_plain_when_disabled(self):
        # stdout isn't a TTY under the test runner, so styling is a no-op.
        self.assertEqual(console.c("hello", "red", "bold"), "hello")

    def test_banner_and_label(self):
        self.assertIn("Pixibot", console.banner())
        self.assertIn("prog-f1", console.label("prog-f1"))

    def test_agent_color_is_deterministic_and_valid(self):
        self.assertEqual(console.agent_color("prog"), console.agent_color("prog"))
        self.assertIn(console.agent_color("prog"), console._AGENT_COLORS)

    def test_spinner_is_a_noop_context_manager(self):
        with console.Spinner("working"):
            pass  # must not raise, even when color is disabled


if __name__ == "__main__":
    unittest.main()
