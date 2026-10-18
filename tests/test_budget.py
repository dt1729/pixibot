"""The compute budget: counted per turn, applied per agent and per run.

DESIGN.md decision 14 - the user sets ceilings, the TPM allocates within them -
was unimplemented until M46: `budget_ceilings.compute` was parsed, validated,
prompted about and never read.
"""

import unittest

from pixibot.lib.agent import AgentSpec, ReasoningAgent, RunContext
from pixibot.lib.context_manager import STATE_BUDGET_EXHAUSTED, STATE_DONE
from pixibot.lib.factory import agent_spec
from pixibot.model import MockModel, ModelResponse, TokenUsage, ToolCall

from .support import WithBlackboard


def costed(text="ok", *, tokens=100, stop="end_turn", calls=()):
    """A scripted response that reports what it cost."""
    return ModelResponse(text=text, tool_calls=list(calls), stop_reason=stop,
                         usage=TokenUsage(input=tokens // 2, output=tokens // 2))


class TokenUsageTest(unittest.TestCase):
    def test_total_is_input_plus_output(self):
        self.assertEqual(TokenUsage(input=30, output=12).total, 42)

    def test_usage_adds(self):
        total = TokenUsage(1, 2, 3) + TokenUsage(10, 20, 30)
        self.assertEqual((total.input, total.output, total.cached_input), (11, 22, 33))

    def test_cached_input_is_counted_in_input_too(self):
        self.assertEqual(TokenUsage(input=100, output=0, cached_input=90).total, 100)

    def test_billable_excludes_the_cached_prefix(self):
        # a ceiling exists to stop spend, and a cache read costs about a tenth;
        # on the CLI path the cached prefix is ~20k of harness prompt per turn
        usage = TokenUsage(input=21000, output=500, cached_input=20000)
        self.assertEqual(usage.total, 21500)
        self.assertEqual(usage.billable, 1500)

    def test_billable_never_goes_negative(self):
        self.assertEqual(TokenUsage(input=5, output=1, cached_input=99).billable, 1)

    def test_an_unreported_turn_is_zero_not_an_error(self):
        self.assertEqual(ModelResponse(text="x").usage.total, 0)


class CeilingParsingTest(unittest.TestCase):
    def spec(self, compute):
        raw = {"id": "a", "role": "programmer", "budget": {"depth": "senior"}}
        if compute is not None:
            raw["budget"]["compute"] = compute
        return agent_spec(raw, MockModel([]))

    def test_an_integer_ceiling_is_used(self):
        self.assertEqual(self.spec(40000).compute_budget, 40000)

    def test_a_string_ceiling_is_read(self):
        self.assertEqual(self.spec("40000").compute_budget, 40000)

    def test_a_k_suffix_is_read(self):
        self.assertEqual(self.spec("40k").compute_budget, 40000)

    def test_a_missing_ceiling_is_none_not_zero(self):
        # an absent budget means the plan did not set one, not "spend nothing"
        self.assertIsNone(self.spec(None).compute_budget)

    def test_nonsense_is_none(self):
        self.assertIsNone(self.spec("lots").compute_budget)
        self.assertIsNone(self.spec(0).compute_budget)
        self.assertIsNone(self.spec(-5).compute_budget)


class CeilingSharedParserTest(unittest.TestCase):
    """One parser for both ceilings: a run ceiling and an agent ceiling must
    never read the same value differently (it was duplicated until M50)."""

    def test_the_engine_and_the_factory_read_the_same_values(self):
        from pixibot.engine import Engine
        from pixibot.schema import compute_ceiling
        for raw, expected in ((40000, 40000), ("40000", 40000), ("40k", 40000),
                              ("  40K ", 40000), (None, None), ("lots", None),
                              (0, None), (-5, None)):
            with self.subTest(raw=raw):
                run_side = Engine._ceiling_of({"budget_ceilings": {"compute": raw}})
                agent_side = compute_ceiling(raw)
                self.assertEqual(run_side, expected)
                self.assertEqual(agent_side, expected)
                self.assertEqual(run_side, agent_side)


class AgentBudgetTest(WithBlackboard, unittest.TestCase):
    run_id = "bud"

    def agent(self, *, budget, script):
        spec = AgentSpec("dev", MockModel(script), role="reviewer",
                         compute_budget=budget, max_iters=10)
        return ReasoningAgent(spec, RunContext())

    def activate(self, agent, name="dev"):
        self.bb.register_agent(name)
        self.bb.send("user", "go", to=name)
        return agent.runner(self.bb, name, self.bb.poll_inbox(name))

    def test_an_agent_counts_what_its_turns_cost(self):
        agent = self.agent(budget=None, script=[costed(tokens=120)])
        self.activate(agent)
        self.assertEqual(agent.spent.total, 120)

    def test_a_cached_prefix_does_not_consume_the_ceiling(self):
        """The CLI path sends ~20k of harness prompt per turn, cached. Charging
        an agent for it made a 40k ceiling buy two turns (incident 007)."""
        cheap = ModelResponse(text="ok", stop_reason="end_turn",
                              usage=TokenUsage(input=20500, output=200,
                                               cached_input=20000))
        agent = self.agent(budget=2000, script=[cheap, cheap, cheap])
        self.activate(agent)
        self.assertFalse(agent.over_budget, "cache reads must not spend the ceiling")
        self.assertEqual(agent.spent.billable, 700)
        self.assertGreater(agent.spent.total, 20000)

    def test_an_agent_with_no_ceiling_is_never_over_budget(self):
        agent = self.agent(budget=None, script=[costed(tokens=10**6)])
        self.activate(agent)
        self.assertFalse(agent.over_budget)

    def test_reaching_the_ceiling_ends_the_activation_as_budget_exhausted(self):
        tool = ToolCall("t", "send_message", {"to": "tpm", "content": "hi"})
        script = [costed(tokens=600, stop="tool_use", calls=[tool]),
                  costed(tokens=600, stop="tool_use", calls=[tool]),
                  costed(tokens=600)]
        agent = self.agent(budget=1000, script=script)
        state = self.activate(agent)
        self.assertEqual(state, STATE_BUDGET_EXHAUSTED)
        self.assertTrue(agent.over_budget)

    def test_the_loop_stops_at_the_ceiling_rather_than_at_max_iters(self):
        tool = ToolCall("t", "send_message", {"to": "tpm", "content": "hi"})
        turns = []

        def gen(messages):
            turns.append(1)
            return costed(tokens=400, stop="tool_use", calls=[tool])

        agent = self.agent(budget=1000, script=[gen] * 10)
        self.activate(agent)
        self.assertLessEqual(len(turns), 4)       # 3 turns reach 1200 > 1000
        self.assertGreaterEqual(len(turns), 2)

    def test_a_spent_agent_opens_no_model_turn_when_re_activated(self):
        turns = []

        def gen(messages):
            turns.append(1)
            return costed(tokens=2000)

        agent = self.agent(budget=1000, script=[gen] * 5)
        self.activate(agent)
        spent_after_first = len(turns)
        state = self.activate(agent)               # woken again
        self.assertEqual(state, STATE_BUDGET_EXHAUSTED)
        self.assertEqual(len(turns), spent_after_first, "it must not call the model again")

    def test_an_agent_carries_the_one_tool_surface_directly(self):
        """Toolset was an injection point with one implementation and no
        substitution; M50 deleted it. The surface is still there."""
        agent = self.agent(budget=None, script=[costed()])
        self.assertFalse(hasattr(agent, "tools"))
        self.assertIn("write_artifact", agent.tool_impls)
        self.assertEqual([d["name"] for d in agent.tool_defs][:2],
                         ["write_artifact", "read_file"])

    def test_an_agent_within_budget_finishes_normally(self):
        agent = self.agent(budget=10000, script=[costed(tokens=100)])
        self.assertEqual(self.activate(agent), STATE_DONE)
        self.assertFalse(agent.over_budget)


if __name__ == "__main__":
    unittest.main()
