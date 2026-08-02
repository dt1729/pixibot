"""Tests for the Workspace (real files, path safety, command execution)."""

import os
import shutil
import tempfile
import unittest

from pixibot.workspace import Workspace


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.ws = Workspace(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_read_roundtrip(self):
        self.ws.write_file("pkg/mod.py", "print('hi')")
        self.assertEqual(self.ws.read_file("pkg/mod.py"), "print('hi')")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "pkg", "mod.py")))

    def test_list_files(self):
        self.ws.write_file("a.py", "1")
        self.ws.write_file("sub/b.py", "2")
        self.assertEqual(self.ws.list_files(), ["a.py", "sub/b.py"])

    def test_read_missing_returns_none(self):
        self.assertIsNone(self.ws.read_file("nope.py"))

    def test_path_traversal_blocked(self):
        with self.assertRaises(ValueError):
            self.ws.write_file("../escape.py", "x")

    def test_run_command(self):
        self.ws.write_file("hello.py", "print('from workspace')")
        rc, out = self.ws.run("python3 hello.py")
        self.assertEqual(rc, 0)
        self.assertIn("from workspace", out)


if __name__ == "__main__":
    unittest.main()
