"""Tests for checkpoint derivation and the human review loop.

Derivation is pure (projection in, checkpoints out). The loop is exercised
through Engine.build with a scripted reviewer and the offline mock models, so no
key and no terminal are involved.
"""

import contextlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pixibot.checkpoints import HUMAN_GATE, Checkpoint, Review, due, parse
from pixibot.engine import Engine
from pixibot.lib import mockrun
from pixibot.model import MockModel, ModelResponse, ToolCall

from .support import WithBlackboard


class ParseTest(unittest.TestCase):
    def test_reads_id_demo_after_and_gate(self):
        cps = parse({"checkpoints": [
            {"id": "cp1", "after": ["test-f1"], "demo": "run tests", "gate": HUMAN_GATE}]})
        self.assertEqual(len(cps), 1)
        self.assertEqual(cps[0], Checkpoint("cp1", "run tests", ("test-f1",), HUMAN_GATE))
        self.assertTrue(cps[0].human)

    def test_no_checkpoints_is_an_empty_tuple(self):
        self.assertEqual(parse({}), ())
        self.assertEqual(parse({"checkpoints": None}), ())

    def test_missing_fields_are_filled_rather_than_raising(self):
        cp = parse({"checkpoints": [{}]})[0]
        self.assertEqual(cp.id, "cp1")            # numbered by position
        self.assertIn("no demo", cp.demo)         # says so instead of inventing one
        self.assertEqual(cp.after, ())
        self.assertTrue(cp.human)                 # a human gate is the default

    def test_a_single_string_after_is_accepted(self):
        self.assertEqual(parse({"checkpoints": [{"after": "prog"}]})[0].after, ("prog",))

    def test_a_mechanical_only_checkpoint_is_not_human(self):
        self.assertFalse(parse({"checkpoints": [{"gate": "tests"}]})[0].human)

    def test_junk_entries_are_skipped(self):
        self.assertEqual(parse({"checkpoints": ["nonsense", 7]}), ())


class DueTest(unittest.TestCase):
    def setUp(self):
        self.cps = (Checkpoint("cp1", "d", ("a",)), Checkpoint("cp2", "d", ("a", "b")))

    def test_only_checkpoints_whose_agents_finished(self):
        self.assertEqual([c.id for c in due(self.cps, {"a"})], ["cp1"])
        self.assertEqual([c.id for c in due(self.cps, {"a", "b"})], ["cp1", "cp2"])

    def test_nothing_is_due_before_any_agent_finishes(self):
        self.assertEqual(due(self.cps, set()), ())

    def test_passed_checkpoints_are_not_offered_again(self):
        self.assertEqual([c.id for c in due(self.cps, {"a", "b"}, already_passed=["cp1"])],
                         ["cp2"])

    def test_a_checkpoint_with_no_trigger_is_due_at_quiescence(self):
        self.assertEqual(len(due((Checkpoint("end", "demo"),), set())), 1)

    def test_a_trigger_less_checkpoint_never_pauses_the_run(self):
        # the scheduler asks with only_triggered: a checkpoint with no `after`
        # must not stop the team before it has finished
        cps = (Checkpoint("end", "demo"), Checkpoint("cp1", "demo", ("a",)))
        self.assertEqual([c.id for c in due(cps, {"a"}, only_triggered=True)], ["cp1"])
        self.assertEqual([c.id for c in due(cps, {"a"})], ["end", "cp1"])


def early_checkpoint_tpm():
    """A TPM whose plan puts the only checkpoint on the first agent of three."""
    projection = {
        "plan_summary": "three agents in a chain, reviewed after the first",
        "breakdown": [{"id": "f1", "desc": "thing", "depends_on": []}],
        "blackboard_schema": {"sections": ["design", "impl", "tests"]},
        "agents": [
            {"id": "arch", "role": "topology",
             "budget": {"depth": "senior", "scope": "design"},
             "blackboard": {"reads": [], "writes": ["design"]}},
            {"id": "prog", "role": "programmer", "depends_on": ["arch"],
             "budget": {"depth": "senior", "scope": "impl"},
             "blackboard": {"reads": ["design"], "writes": ["impl"]}},
            {"id": "test", "role": "tester", "depends_on": ["prog"],
             "budget": {"depth": "senior", "scope": "tests"},
             "blackboard": {"reads": ["impl"], "writes": ["tests"]}},
        ],
        "checkpoints": [{"id": "cp-arch", "after": ["arch"], "demo": "read the design",
                         "gate": "user_feedback"}],
    }
    return MockModel([ModelResponse(text=json.dumps(projection))] * 4)


def scripted_factory():
    """Each agent writes its own section once, then stops."""
    def factory(spec):
        section = spec["blackboard"]["writes"][0]
        return MockModel([
            ModelResponse(tool_calls=[ToolCall("t", "write_artifact",
                                               {"section": section, "content": "x"})],
                          stop_reason="tool_use"),
            ModelResponse(text="done", stop_reason="end_turn"),
        ])
    return factory


class ReviewLoopTest(WithBlackboard, unittest.TestCase):
    run_id = "cp"

    def setUp(self):
        super().setUp()
        self.engine = Engine(self.bb, mockrun.mock_tpm_model("thing"),
                             mockrun.mock_model_factory())
        self.wsdir = tempfile.mkdtemp()
        self.engine.workspace_base = self.wsdir
        self.addCleanup(shutil.rmtree, self.wsdir, True)
        self.addCleanup(self.bb.close)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                Path(self.path + suffix).unlink()

    def test_no_reviewer_means_no_pause(self):
        res = self.engine.build_objective("thing")
        self.assertNotIn("gate", res)
        self.assertEqual(self.engine.passed_checkpoints, [])

    def test_an_accepted_demo_passes_the_checkpoint(self):
        seen = []

        def reviewer(cp, gate_summary):
            seen.append((cp.id, cp.demo, gate_summary))
            return Review.accept()

        self.engine.build_objective("thing", reviewer=reviewer)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "cp1")
        self.assertEqual(seen[0][1], "run tests")      # the TPM's own words
        self.assertEqual(self.engine.passed_checkpoints, ["cp1"])

    def test_feedback_sends_the_run_back_then_accepts(self):
        verdicts = [Review.revise("add API docs"), Review.accept()]
        calls = []

        def reviewer(cp, gate_summary):
            calls.append(cp.id)
            return verdicts.pop(0)

        self.engine.build_objective("thing", reviewer=reviewer)
        self.assertEqual(calls, ["cp1", "cp1"])        # asked again after revising
        self.assertEqual(self.engine.passed_checkpoints, ["cp1"])
        # the revision actually ran: the mock TPM adds a docs agent on revise
        self.assertIn("docs-f1", [a["agent_id"] for a in self.bb.list_agents()])

    def test_endless_feedback_is_bounded(self):
        calls = []

        def reviewer(cp, gate_summary):
            calls.append(cp.id)
            return Review.revise("more")

        self.engine.max_review_rounds = 2
        self.engine.build_objective("thing", reviewer=reviewer)
        self.assertEqual(len(calls), 3)                # rounds + the final ask
        self.assertEqual(self.engine.passed_checkpoints, [])   # never accepted

    def test_the_demo_arrives_before_the_downstream_agents_run(self):
        """The M39 defect: every checkpoint fired after quiescence, so a demo
        arrived once the whole team had run and spent its budget.

        The checkpoint here triggers on the FIRST agent in the chain, so a
        mid-run pause and an end-of-run pause give different answers.
        """
        engine = Engine(self.bb, early_checkpoint_tpm(), scripted_factory())
        engine.workspace_base = self.wsdir
        seen = []

        def reviewer(cp, gate_summary):
            seen.append({a["agent_id"]: a.get("state") for a in self.bb.list_agents()})
            return Review.accept()

        engine.build_objective("thing", reviewer=reviewer)

        self.assertEqual(len(seen), 1, "the checkpoint must be reviewed exactly once")
        states = seen[0]
        self.assertEqual(states.get("arch"), "DONE")      # the trigger agent ran
        self.assertNotEqual(states.get("prog"), "DONE")   # the rest had not
        self.assertNotEqual(states.get("test"), "DONE")
        # and the run continued afterwards
        after = {a["agent_id"]: a.get("state") for a in self.bb.list_agents()}
        self.assertEqual(after.get("prog"), "DONE")
        self.assertEqual(after.get("test"), "DONE")
        self.assertEqual(engine.passed_checkpoints, ["cp-arch"])

    def test_a_checkpoint_is_settled_once_even_when_rounds_run_out(self):
        self.engine.max_review_rounds = 1
        calls = []

        def reviewer(cp, gate_summary):
            calls.append(cp.id)
            return Review.revise("again")

        self.engine.build_objective("thing", reviewer=reviewer)
        self.assertEqual(calls, ["cp1", "cp1"])          # rounds + the final ask
        self.assertEqual(self.engine.passed_checkpoints, [])
        self.assertIn("cp1", self.engine._settled)       # settled, so the loop ends

    def test_the_verdict_is_recorded_on_the_blackboard(self):
        self.engine.build_objective("thing", reviewer=lambda cp, g: Review.accept())
        payloads = [e.payload for e in self.bb.history(from_agent="user")]
        self.assertTrue(any("checkpoint cp1" in p and "accepted" in p for p in payloads))

    def test_pending_checkpoints_before_a_build_is_empty(self):
        self.assertEqual(Engine(self.bb, mockrun.mock_tpm_model("x"),
                                mockrun.mock_model_factory()).pending_checkpoints(), ())


if __name__ == "__main__":
    unittest.main()
