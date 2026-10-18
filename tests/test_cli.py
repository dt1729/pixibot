"""Tests for the shell command router (bash-style nav + actions; fakes, no models)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from pixibot.blackboard import KIND_ARTIFACT
from pixibot.cli import ChatSession
from pixibot.workspace import Workspace

from .support import WithBlackboard


class FakeBroker:
    def __init__(self):
        self.calls = []

    def talk(self, agent, message, on_delta=None):
        self.calls.append(("talk", agent, message))
        return f"[{agent}] {message}"

    def tell(self, agent, text):
        self.calls.append(("tell", agent, text))
        return 7


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.on_activation = None
        self.on_agent_start = None
        self.on_event = None
        self.sandbox = "auto"
        self.workspace = None

    def build_objective(self, objective, *, hard=False, reviewer=None):
        self.calls.append(("build", objective, hard))
        return {"steps": 1, "projection": {"agents": [{"id": "a"}]}}

    def build(self, inp, *, reviewer=None):
        self.calls.append(("build_from", inp.get("objective")))
        return {"steps": 1}

    def tell(self, agent, text):
        self.calls.append(("tell", agent, text))
        return 2

    def revise(self, feedback):
        self.calls.append(("revise", feedback))
        return 3


class CliTest(WithBlackboard, unittest.TestCase):
    run_id = "cli"

    def setUp(self):
        super().setUp()
        self.wsdir = tempfile.mkdtemp()
        self.ws = Workspace(self.wsdir)
        self.ws.write_file("reverse.py", "print('hi')")
        self.ws.write_file("tests/test_reverse.py", "assert True")
        self.broker = FakeBroker()
        self.engine = FakeEngine()
        self.session = ChatSession(self.bb, self.broker, lambda objective, hard=False: self.engine, workspace=self.ws)

    def tearDown(self):
        shutil.rmtree(self.wsdir, ignore_errors=True)

    # ── navigation ──────────────────────────────────────────────────────────
    def test_ls(self):
        out = self.session.handle("ls")
        self.assertIn("reverse.py", out)
        self.assertIn("tests/", out)

    def test_cd_pwd_and_relative_ls(self):
        self.assertEqual(self.session.handle("cd tests"), "/tests")
        self.assertEqual(self.session.handle("pwd"), "/tests")
        self.assertIn("test_reverse.py", self.session.handle("ls"))
        self.assertEqual(self.session.handle("cd .."), "/")

    def test_cd_missing_and_escape(self):
        self.assertIn("no such directory", self.session.handle("cd nope"))
        self.assertIn("above the workspace", self.session.handle("cd .."))

    def test_cat(self):
        self.assertEqual(self.session.handle("cat reverse.py"), "print('hi')")
        self.assertIn("no such file", self.session.handle("cat nope.py"))

    def test_tree(self):
        out = self.session.handle("tree")
        self.assertIn("reverse.py", out)
        self.assertIn("tests/test_reverse.py", out)

    # ── actions ─────────────────────────────────────────────────────────────
    def test_build_wires_workspace(self):
        out = self.session.handle("build a reverser")
        self.assertEqual(self.engine.calls[-1], ("build", "a reverser", False))
        self.assertIn("Built", out)
        self.assertIs(self.engine.workspace, self.ws)

    def test_build_usage(self):
        self.assertIn("usage", self.session.handle("build"))

    def test_build_from(self):
        form = ("# Pixibot Build Request\n## Objective\nA thing\n## Target\n- Language: python\n"
                "- Framework: none\n- Platform: linux\n## Acceptance criteria\n- works\n"
                "## Constraints\n- none\n## Non-goals\n- none\n## Budget\n- Max depth: senior\n"
                "- Compute: 100000\n- Scope: one\n## Hard development?\nno\n")
        p = str(Path(self.wsdir) / "form.md")
        Path(p).write_text(form, encoding="utf-8")
        out = self.session.handle(f"build-from {p}")
        self.assertEqual(self.engine.calls[-1][0], "build_from")
        self.assertIn("Built", out)

    def test_tell_uses_engine_then_broker(self):
        self.session.handle("build x")
        self.session.handle("tell prog fix it")
        self.assertEqual(self.engine.calls[-1], ("tell", "prog", "fix it"))

    def test_tell_without_engine_falls_back(self):
        self.session.handle("tell prog hi")
        self.assertEqual(self.broker.calls[-1], ("tell", "prog", "hi"))

    def test_ask_talks_to_agent(self):
        self.session.handle("ask prog why a dict?")
        self.assertEqual(self.broker.calls[-1], ("talk", "prog", "why a dict?"))

    def test_plain_text_talks_to_overseer(self):
        self.session.handle("what is the status of the project")
        self.assertEqual(self.broker.calls[-1], ("talk", "tpm", "what is the status of the project"))

    def test_agents_and_status(self):
        self.assertIn("no agents", self.session.handle("agents"))
        self.bb.register_agent("prog", role="programmer", depth="senior", state="DONE")
        self.assertIn("prog", self.session.handle("agents"))
        self.assertIn("files: 2", self.session.handle("status"))

    def test_report(self):
        self.bb.register_agent("prog", scope="impl")
        self.bb.send("prog", "x", kind=KIND_ARTIFACT, section="impl/f1")
        self.assertIn("run report", self.session.handle("report"))

    def test_hard_toggle(self):
        self.assertIn("on", self.session.handle("hard on"))
        self.assertTrue(self.session.hard)

    def test_provider_switch(self):
        s = ChatSession(self.bb, self.broker, lambda objective, hard=False: self.engine,
                        workspace=self.ws, provider_name="offline")
        self.assertIn("provider:", s.handle("provider"))
        self.assertIn("gemini", s.handle("provider gemini"))
        self.assertEqual(s.provider_name, "gemini")
        # a switch re-wires the broker and drops the engine built on the old provider
        self.assertIsNot(s.broker, self.broker)
        self.assertIsNone(s.engine)

    def test_provider_switch_rejects_an_unknown_name(self):
        out = self.session.handle("provider wizard")
        self.assertIn("unknown provider", out)
        self.assertEqual(self.session.provider_name, "custom")

    def test_provider_switch_back_to_offline_disables_live_progress(self):
        s = ChatSession(self.bb, self.broker, lambda objective, hard=False: self.engine,
                        workspace=self.ws, provider_name="gemini")
        s.handle("provider offline")
        self.assertEqual(s.provider_name, "offline")
        self.assertIsNone(s.progress)

    def test_build_hands_the_sessions_hooks_to_the_engine(self):
        seen = []
        self.session.progress = lambda a, t, f: seen.append(("done", a))
        self.session.progress_start = lambda a: seen.append(("start", a))
        self.session.on_event = lambda a, k, d: seen.append(("event", a))
        self.session.on_build_start = lambda o: seen.append(("build", o))
        self.session.sandbox = "local"
        self.session.handle("build a thing")
        self.assertIn(("build", "a thing"), seen)          # title hook fired
        self.assertIs(self.engine.on_activation, self.session.progress)
        self.assertIs(self.engine.on_agent_start, self.session.progress_start)
        self.assertIs(self.engine.on_event, self.session.on_event)
        self.assertEqual(self.engine.sandbox, "local")

    def test_help_quit_clear(self):
        self.assertIn("Navigate", self.session.handle("help"))
        self.assertEqual(self.session.handle("exit"), "__quit__")
        self.assertEqual(self.session.handle("clear"), "__clear__")


if __name__ == "__main__":
    unittest.main()
