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

    def test_pytest_tmp_and_venv_ignored(self):
        self.ws.write_file("app.py", "x")
        self.ws.write_file("pytest-of-dt/pytest-1/junk.txt", "tmp")
        self.ws.write_file(".ffcheck/lib/pkg.py", "vendored")
        files = self.ws.list_files()
        self.assertIn("app.py", files)
        self.assertNotIn("pytest-of-dt/pytest-1/junk.txt", files)
        self.assertNotIn(".ffcheck/lib/pkg.py", files)

    def test_run_refuses_parent_traversal(self):
        rc, out = self.ws.run("cat ../../etc/hostname")
        self.assertEqual(rc, 126)
        self.assertIn("confined", out)

    def test_run_refuses_absolute_home(self):
        rc, out = self.ws.run("cat /home/someone/secret.txt")
        self.assertEqual(rc, 126)

    def test_run_refuses_key_file(self):
        rc, out = self.ws.run("cat ant_key.txt")
        self.assertEqual(rc, 126)

    def test_run_scrubs_secrets_from_env(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-should-not-leak"
        try:
            rc, out = self.ws.run("env")
            self.assertEqual(rc, 0)
            self.assertNotIn("sk-should-not-leak", out)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_run_home_is_workspace(self):
        rc, out = self.ws.run("echo $HOME")
        self.assertEqual(rc, 0)
        self.assertIn(os.path.realpath(self.root), os.path.realpath(out.strip()))


if __name__ == "__main__":
    unittest.main()
