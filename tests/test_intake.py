"""Tests for the markdown intake form: render + parse (incl. the hard flag)."""

import unittest

from pixibot import intake
from pixibot.schema import validate_input

FILLED = """# Pixibot Build Request

## Objective
A URL shortener service

## Target
- Language: python
- Framework: flask
- Platform: linux

## Acceptance criteria
- POST /shorten returns a code
- GET /<code> redirects

## Constraints
- no external database

## Non-goals
- none

## Budget
- Max depth: principal
- Compute: 300000
- Scope: single service

## Hard development?
yes
"""


class IntakeTest(unittest.TestCase):
    def test_render_form(self):
        self.assertIn("Build Request", intake.render_form())
        self.assertIn("Hard development", intake.render_form())

    def test_parse_filled_form(self):
        inp = intake.parse_form(FILLED)
        self.assertEqual(inp["objective"], "A URL shortener service")
        self.assertEqual(inp["target"]["language"], "python")
        self.assertEqual(inp["target"]["framework"], "flask")
        self.assertEqual(inp["acceptance_criteria"],
                         ["POST /shorten returns a code", "GET /<code> redirects"])
        self.assertEqual(inp["constraints"], ["no external database"])
        self.assertEqual(inp["non_goals"], [])         # "none" dropped
        self.assertTrue(inp["hard"])
        self.assertEqual(inp["budget_ceilings"]["max_depth"], "principal")
        self.assertEqual(inp["budget_ceilings"]["compute"], 300000)

    def test_parsed_form_validates(self):
        self.assertEqual(validate_input(intake.parse_form(FILLED)), [])


if __name__ == "__main__":
    unittest.main()
