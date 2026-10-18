"""Tests for shell completion — no terminal, no textual, no workspace on disk."""

import unittest

from pixibot.lib.completion import Sources, common_prefix, complete, path_candidates

COMMANDS = frozenset({"build", "build-from", "cat", "cd", "ls", "ask", "tell", "think",
                      "status", "report"})

TREE = {
    "": ["app.py", "tests/", "README.md"],
    "tests": ["test_app.py", "test_util.py"],
    "src": ["main.cpp"],
}


def sources(cwd="", agents=()):
    return Sources(commands=COMMANDS, cwd=cwd,
                   agent_ids=lambda: list(agents),
                   listdir=lambda path: TREE.get(path.strip("/")))


class CommonPrefixTest(unittest.TestCase):
    def test_shared_prefix(self):
        self.assertEqual(common_prefix(["build", "builder", "build-from"]), "build")

    def test_single_string_is_its_own_prefix(self):
        self.assertEqual(common_prefix(["status"]), "status")

    def test_empty(self):
        self.assertEqual(common_prefix([]), "")

    def test_nothing_in_common(self):
        self.assertEqual(common_prefix(["ls", "cat"]), "")


class CommandWordTest(unittest.TestCase):
    def test_unique_command_completes_with_a_space(self):
        self.assertEqual(complete("stat", sources()), ("status ", ["status"]))

    def test_several_candidates_apply_the_shared_prefix(self):
        text, cands = complete("bu", sources())
        self.assertEqual(text, "build")
        self.assertEqual(cands, ["build", "build-from"])

    def test_no_candidate_leaves_the_text_alone(self):
        self.assertEqual(complete("zzz", sources()), ("zzz", []))

    def test_empty_input_offers_everything(self):
        _, cands = complete("", sources())
        self.assertEqual(len(cands), len(COMMANDS))


class PathArgumentTest(unittest.TestCase):
    def test_path_command_completes_from_the_workspace_root(self):
        text, cands = complete("cat app", sources())
        self.assertEqual(text, "cat app.py ")
        self.assertEqual(cands, ["app.py"])

    def test_completion_is_relative_to_the_shell_cwd(self):
        text, cands = complete("cat test_a", sources(cwd="tests"))
        self.assertEqual(text, "cat test_app.py ")
        self.assertEqual(cands, ["test_app.py"])

    def test_a_directory_fragment_keeps_its_prefix(self):
        text, cands = complete("cat tests/test_", sources())
        self.assertEqual(cands, ["tests/test_app.py", "tests/test_util.py"])
        self.assertEqual(text, "cat tests/test_")   # shared prefix, nothing new to add

    def test_unknown_directory_yields_nothing(self):
        self.assertEqual(complete("cd nope/", sources()), ("cd nope/", []))

    def test_path_candidates_directly(self):
        self.assertEqual(path_candidates("READ", sources()), ["README.md"])


class AgentArgumentTest(unittest.TestCase):
    def test_agent_command_completes_live_agent_ids(self):
        src = sources(agents=("architect", "programmer", "tester"))
        text, cands = complete("tell prog", src)
        self.assertEqual(text, "tell programmer ")
        self.assertEqual(cands, ["programmer"])

    def test_no_agents_yet_completes_nothing(self):
        self.assertEqual(complete("ask arch", sources()), ("ask arch", []))

    def test_a_command_with_no_argument_source_completes_nothing(self):
        self.assertEqual(complete("status extra", sources()), ("status extra", []))


if __name__ == "__main__":
    unittest.main()
