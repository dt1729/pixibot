"""Tests for the Claude Code CLI model.

Everything here runs with a runner double: no process, no subscription usage, no
network. The one test that really calls the CLI is marked `simulation` and is
excluded from the default suite (see pyproject.toml).
"""

import json
import os
import shutil
import unittest
from pathlib import Path

import pytest

from pixibot.claude_cli import (
    CLI_ENV,
    DISALLOWED_TOOLS,
    RESPONSE_SCHEMA,
    ClaudeCliModel,
    CliFailedError,
    CliUnavailableError,
    available,
    build_argv,
    find_cli,
    parse_envelope,
    purge_transcript,
    render_prompt,
    transcript_dir,
    windows_candidates,
)


def envelope(result, *, is_error=False, usage=None, subtype="success"):
    body = {"type": "result", "subtype": subtype, "is_error": is_error, "result": result}
    if usage is not None:
        body["usage"] = usage
    return json.dumps(body)


class ArgvTest(unittest.TestCase):
    def setUp(self):
        self.argv = build_argv("claude", system="be an agent", model="claude-sonnet-5",
                               mcp_config="/tmp/no_mcp.json")

    def test_it_is_a_print_run(self):
        self.assertEqual(self.argv[:2], ["claude", "-p"])

    def test_the_output_schema_is_enforced_by_the_cli(self):
        self.assertIn("--json-schema", self.argv)
        schema = json.loads(self.argv[self.argv.index("--json-schema") + 1])
        self.assertEqual(schema, RESPONSE_SCHEMA)

    def test_the_turn_budget_is_enough_for_an_agent_prompt(self):
        # 1 cannot answer; 2 ran out in a live build (see docs/incidents.md 007)
        self.assertGreaterEqual(int(self.argv[self.argv.index("--max-turns") + 1]), 4)

    def test_the_turn_budget_is_adjustable(self):
        argv = build_argv("claude", system="s", model="m", mcp_config="c", max_turns=9)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "9")

    def test_the_agents_prompt_replaces_claude_codes_own(self):
        self.assertEqual(self.argv[self.argv.index("--system-prompt") + 1], "be an agent")

    def test_tools_are_blocked_and_their_schemas_stripped(self):
        self.assertEqual(self.argv[self.argv.index("--allowedTools") + 1], "")
        disallowed = self.argv[self.argv.index("--disallowedTools") + 1]
        self.assertEqual(disallowed, DISALLOWED_TOOLS)
        for tool in ("mcp__*", "Bash", "Write", "WebFetch", "Task"):
            self.assertIn(tool, disallowed)

    def test_no_mcp_server_can_attach(self):
        self.assertIn("--strict-mcp-config", self.argv)
        self.assertEqual(self.argv[self.argv.index("--mcp-config") + 1], "/tmp/no_mcp.json")

    def test_permissions_are_not_skipped(self):
        self.assertEqual(self.argv[self.argv.index("--permission-mode") + 1], "default")
        self.assertNotIn("--dangerously-skip-permissions", self.argv)
        self.assertIn("--disable-slash-commands", self.argv)

    def test_the_model_is_passed_through(self):
        self.assertEqual(self.argv[self.argv.index("--model") + 1], "claude-sonnet-5")


class RenderPromptTest(unittest.TestCase):
    def test_tools_are_listed_with_their_inputs(self):
        tools = [{"name": "write_artifact", "description": "write a file",
                  "input_schema": {"properties": {"section": {"type": "string"}}}}]
        prompt = render_prompt([], tools)
        self.assertIn("write_artifact", prompt)
        self.assertIn("write a file", prompt)
        self.assertIn("section", prompt)

    def test_a_string_turn_is_labelled(self):
        self.assertIn("[USER] build it", render_prompt([{"role": "user", "content": "build it"}], []))

    def test_tool_use_and_results_survive_the_rendering(self):
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "writing"},
                {"type": "tool_use", "id": "t1", "name": "write_artifact",
                 "input": {"section": "a.py"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "wrote a.py"}]},
        ]
        prompt = render_prompt(messages, [])
        self.assertIn("called write_artifact", prompt)
        self.assertIn("a.py", prompt)
        self.assertIn("TOOL RESULT", prompt)
        self.assertIn("wrote a.py", prompt)

    def test_the_format_instruction_comes_last(self):
        prompt = render_prompt([{"role": "user", "content": "x"}], [])
        self.assertIn("tool_calls", prompt.rsplit("\n\n", 1)[-1])


class ParseEnvelopeTest(unittest.TestCase):
    def test_a_structured_result_becomes_a_response(self):
        resp = parse_envelope(envelope({"text": "done", "tool_calls": []}))
        self.assertEqual(resp.text, "done")
        self.assertEqual(resp.stop_reason, "end_turn")
        self.assertEqual(resp.tool_calls, [])

    def test_tool_calls_get_ids_and_set_the_stop_reason(self):
        resp = parse_envelope(envelope({"text": "", "tool_calls": [
            {"name": "write_artifact", "input": {"section": "a.py", "content": "x"}}]}))
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertEqual(resp.tool_calls[0].name, "write_artifact")
        self.assertEqual(resp.tool_calls[0].input["section"], "a.py")
        self.assertTrue(resp.tool_calls[0].id)        # the harness needs an id to pair

    def test_a_fenced_json_string_is_still_read(self):
        fenced = '```json\n{"text": "ok", "tool_calls": []}\n```'
        self.assertEqual(parse_envelope(envelope(fenced)).text, "ok")

    def test_usage_is_mapped_including_cache(self):
        resp = parse_envelope(envelope({"text": "", "tool_calls": []}, usage={
            "input_tokens": 10, "output_tokens": 50,
            "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 500}))
        self.assertEqual(resp.usage.output, 50)
        self.assertEqual(resp.usage.input, 1510)      # cache counts toward context
        self.assertEqual(resp.usage.cached_input, 1000)

    def test_a_cli_error_with_no_answer_raises_rather_than_looking_like_silence(self):
        with self.assertRaises(CliFailedError):
            parse_envelope(envelope(None, is_error=True, subtype="error_max_turns"))

    def test_an_error_that_still_produced_an_answer_keeps_the_answer(self):
        # error_max_turns after the work is done must not discard the work: in a
        # live build that turned a finished turn into a BLOCKED agent
        resp = parse_envelope(envelope(
            {"text": "wrote it", "tool_calls": [
                {"name": "write_artifact", "input": {"section": "a.py", "content": "x"}}]},
            is_error=True, subtype="error_max_turns"))
        self.assertEqual(resp.text, "wrote it")
        self.assertEqual(resp.tool_calls[0].name, "write_artifact")

    def test_output_that_is_not_json_raises(self):
        with self.assertRaises(CliFailedError):
            parse_envelope("command not found")

    def test_an_unparseable_result_yields_an_empty_answer_not_a_crash(self):
        resp = parse_envelope(envelope("I would rather not."))
        self.assertEqual(resp.text, "")
        self.assertEqual(resp.tool_calls, [])


class CliDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(CLI_ENV, None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop(CLI_ENV, None)
        else:
            os.environ[CLI_ENV] = self._saved

    def test_an_explicit_missing_path_raises(self):
        with self.assertRaises(CliUnavailableError):
            find_cli("/nope/claude")

    def test_the_environment_variable_is_read(self):
        os.environ[CLI_ENV] = shutil.which("python3")
        self.assertEqual(find_cli(), shutil.which("python3"))

    def test_a_bad_environment_variable_raises_rather_than_being_ignored(self):
        os.environ[CLI_ENV] = "/nope/claude"
        with self.assertRaises(CliUnavailableError):
            find_cli()

    def test_windows_candidates_are_globbed_not_guessed(self):
        # the WSL account name is not the Windows account name; every path this
        # returns must actually exist
        for path in windows_candidates():
            self.assertTrue(Path(path).exists(), path)

    def test_an_explicit_present_path_is_used(self):
        self.assertEqual(find_cli(shutil.which("python3")), shutil.which("python3"))

    def test_available_never_raises(self):
        self.assertIn(available("/nope/claude"), (True, False))
        self.assertFalse(available("/nope/claude"))


class TranscriptTest(unittest.TestCase):
    def test_the_transcript_directory_is_derived_from_the_cwd(self):
        self.assertIn("tmp", str(transcript_dir("/tmp/x")))
        self.assertTrue(str(transcript_dir("/tmp/x")).endswith("-tmp-x"))

    def test_purging_a_missing_transcript_is_silent(self):
        purge_transcript("/tmp/definitely-not-a-session")   # must not raise


class ModelRoundTripTest(unittest.TestCase):
    """The whole model, with the subprocess replaced."""

    def test_a_turn_renders_a_prompt_and_parses_the_answer(self):
        seen = {}

        def runner(argv, prompt):
            seen["argv"], seen["prompt"] = argv, prompt
            return envelope({"text": "wrote it", "tool_calls": [
                {"name": "write_artifact", "input": {"section": "a.py", "content": "x"}}]},
                usage={"input_tokens": 20, "output_tokens": 5})

        model = ClaudeCliModel("claude-sonnet-5", runner=runner)
        resp = model.generate(system="you are the programmer",
                              messages=[{"role": "user", "content": "write a.py"}],
                              tools=[{"name": "write_artifact", "description": "write",
                                      "input_schema": {"properties": {}}}])
        self.assertEqual(resp.text, "wrote it")
        self.assertEqual(resp.tool_calls[0].name, "write_artifact")
        self.assertEqual(resp.usage.total, 25)
        self.assertIn("write a.py", seen["prompt"])
        self.assertIn("you are the programmer", seen["argv"])

    def test_the_mcp_config_written_for_the_turn_is_empty(self):
        captured = {}

        def runner(argv, prompt):
            path = argv[argv.index("--mcp-config") + 1]
            captured["config"] = Path(path).read_text(encoding="utf-8")
            return envelope({"text": "ok", "tool_calls": []})

        ClaudeCliModel(runner=runner).generate(system="s", messages=[], tools=[])
        self.assertEqual(json.loads(captured["config"]), {"mcpServers": {}})

    def test_stream_generate_emits_the_whole_text_once(self):
        chunks = []
        model = ClaudeCliModel(runner=lambda a, p: envelope({"text": "hello", "tool_calls": []}))
        resp = model.stream_generate(system="s", messages=[], tools=[], on_delta=chunks.append)
        self.assertEqual(chunks, ["hello"])
        self.assertEqual(resp.text, "hello")


@pytest.mark.simulation
class SimulationTest(unittest.TestCase):
    """Really calls `claude -p`. Excluded from the default suite."""

    def setUp(self):
        if not available():
            self.skipTest("no Claude Code CLI on this machine")

    def test_a_real_turn_returns_a_tool_call_in_our_schema(self):
        model = ClaudeCliModel("claude-haiku-4-5")
        resp = model.generate(
            system=("You are the programmer agent on an automated build team. You act only "
                    "through the tools you are given."),
            messages=[{"role": "user", "content":
                       "Create reverse.py with a function reverse(s) returning the "
                       "reversed string. Write the file now."}],
            tools=[{"name": "write_artifact",
                    "description": "Create or overwrite a file in the workspace",
                    "input_schema": {"properties": {"section": {"type": "string"},
                                                    "content": {"type": "string"}}}}])
        self.assertEqual(resp.stop_reason, "tool_use", resp.text)
        self.assertEqual(resp.tool_calls[0].name, "write_artifact")
        self.assertIn("reverse", resp.tool_calls[0].input.get("content", ""))
        self.assertGreater(resp.usage.total, 0)

    def test_the_session_transcript_does_not_outlive_the_call(self):
        before = {p.name for p in (Path.home() / ".claude/projects").glob("*")}
        ClaudeCliModel("claude-haiku-4-5").generate(
            system="Answer with an empty tool list.", messages=[
                {"role": "user", "content": "Say ok and call nothing."}], tools=[])
        after = {p.name for p in (Path.home() / ".claude/projects").glob("*")}
        leaked = [n for n in after - before if "tmp" in n.lower()]
        self.assertEqual(leaked, [], f"transcript left behind: {leaked}")


if __name__ == "__main__":
    unittest.main()
