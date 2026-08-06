"""Tests for tool-level enforcement (adoption D — test-designer blindness)."""

import shutil
import tempfile
import unittest

from pixibot.tools import default_tool_impls
from pixibot.workspace import Workspace


class _FakeAgent:
    def __init__(self, workspace=None, denylist=()):
        self.agent_id = "test-designer"
        self.workspace = workspace
        self.read_denylist = denylist


class DenylistTest(unittest.TestCase):
    def setUp(self):
        self.impls = default_tool_impls()

    def test_read_file_refuses_denied_path(self):
        agent = _FakeAgent(denylist=("impl/", "viewer/"))
        out = self.impls["read_file"](agent, None, {"path": "impl/core.py"})
        self.assertIn("refused", out.lower())

    def test_read_file_allows_other_paths(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        ws = Workspace(root)
        ws.write_file("ARCHITECTURE.md", "the design")
        agent = _FakeAgent(workspace=ws, denylist=("impl/",))
        out = self.impls["read_file"](agent, None, {"path": "ARCHITECTURE.md"})
        self.assertEqual(out, "the design")

    def test_run_bash_refuses_command_touching_denied_section(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        agent = _FakeAgent(workspace=Workspace(root), denylist=("impl/",))
        out = self.impls["run_bash"](agent, None, {"command": "cat impl/core.py"})
        self.assertIn("refused", out.lower())


if __name__ == "__main__":
    unittest.main()
