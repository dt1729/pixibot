"""Tests for the execution runtime (local executor + docker command builder)."""

import unittest

from pixibot.runtime import LocalExecutor, docker_command, get_executor


class RuntimeTest(unittest.TestCase):
    def test_local_executor_success(self):
        r = LocalExecutor().run("echo hi")
        self.assertEqual(r.returncode, 0)
        self.assertIn("hi", r.output)

    def test_local_executor_failure_code(self):
        r = LocalExecutor().run("exit 3")
        self.assertEqual(r.returncode, 3)

    def test_docker_command_shape(self):
        argv = docker_command("img:latest", "/w", "pytest -q")
        self.assertEqual(argv[:3], ["docker", "run", "--rm"])
        self.assertIn("/w:/work", argv)
        self.assertIn("img:latest", argv)
        self.assertEqual(argv[-3:], ["bash", "-lc", "pytest -q"])

    def test_get_executor_falls_back_to_local(self):
        self.assertIsInstance(get_executor(prefer_docker=False), LocalExecutor)


if __name__ == "__main__":
    unittest.main()
