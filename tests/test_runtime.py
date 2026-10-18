"""Tests for the execution runtime: confinement, the docker argv, and selection."""

import tempfile
import unittest
from unittest import mock

from pixibot.runtime import (
    DockerExecutor,
    LocalExecutor,
    SandboxUnavailableError,
    confined_env,
    docker_command,
    executor_for,
)


class LocalExecutorTest(unittest.TestCase):
    def test_success_and_failure_codes(self):
        self.assertTrue(LocalExecutor().run("echo hi").ok)
        self.assertIn("hi", LocalExecutor().run("echo hi").output)
        self.assertEqual(LocalExecutor().run("exit 3").returncode, 3)

    def test_output_combines_stdout_and_stderr(self):
        r = LocalExecutor().run("echo out; echo err 1>&2")
        self.assertIn("out", r.output)
        self.assertIn("err", r.output)

    def test_timeout_is_reported_not_raised(self):
        r = LocalExecutor().run("sleep 5", timeout=1)
        self.assertFalse(r.ok)
        self.assertIn("timed out", r.output)

    def test_runs_in_the_given_workdir(self):
        d = tempfile.mkdtemp()
        self.assertIn(d.rsplit("/", 1)[-1], LocalExecutor(d).run("pwd").output)


class ConfinedEnvTest(unittest.TestCase):
    def test_secrets_are_withheld(self):
        env = confined_env("/w", {"ANTHROPIC_API_KEY": "s", "OPENAI_API_KEY": "s",
                                  "MY_TOKEN": "s", "PATH": "/usr/bin"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("MY_TOKEN", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_home_and_temp_are_pinned_to_the_workspace(self):
        env = confined_env("/w", {"HOME": "/home/real", "TMPDIR": "/tmp"})
        self.assertEqual(env["HOME"], "/w")
        self.assertEqual(env["TMPDIR"], "/w")
        self.assertEqual(env["PWD"], "/w")

    def test_a_confined_subprocess_cannot_see_a_key(self):
        ex = LocalExecutor("/tmp", env=confined_env("/tmp", {"ANTHROPIC_API_KEY": "sk-x",
                                                            "PATH": "/usr/bin:/bin"}))
        self.assertNotIn("sk-x", ex.run("env").output)


class DockerCommandTest(unittest.TestCase):
    def test_argv_shape(self):
        argv = docker_command("img:latest", "/w", "pytest -q")
        self.assertEqual(argv[:3], ["docker", "run", "--rm"])
        self.assertIn("/w:/work", argv)
        self.assertIn("img:latest", argv)
        self.assertEqual(argv[-3:], ["bash", "-lc", "pytest -q"])

    def test_no_network_and_no_host_environment(self):
        argv = docker_command("img", "/w", "ls")
        self.assertIn("--network", argv)
        self.assertIn("none", argv)
        self.assertIn("HOME=/work", argv)
        self.assertIn("TMPDIR=/work", argv)

    def test_optional_container_name(self):
        self.assertIn("--name", docker_command("img", "/w", "ls", name="run-7"))


class ExecutorSelectionTest(unittest.TestCase):
    def test_local_mode_never_consults_docker(self):
        with mock.patch("pixibot.runtime.docker_available",
                        side_effect=AssertionError("must not be called")):
            self.assertIsInstance(executor_for("/tmp", sandbox="local"), LocalExecutor)

    def test_auto_prefers_a_container_when_docker_answers(self):
        with mock.patch("pixibot.runtime.docker_available", return_value=True):
            self.assertIsInstance(executor_for("/tmp", sandbox="auto"), DockerExecutor)

    def test_auto_falls_back_to_a_confined_subprocess(self):
        with mock.patch("pixibot.runtime.docker_available", return_value=False):
            self.assertIsInstance(executor_for("/tmp", sandbox="auto"), LocalExecutor)

    def test_docker_mode_refuses_to_pretend(self):
        with mock.patch("pixibot.runtime.docker_available", return_value=False), \
                self.assertRaises(SandboxUnavailableError):
            executor_for("/tmp", sandbox="docker")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            executor_for("/tmp", sandbox="vm")


if __name__ == "__main__":
    unittest.main()
