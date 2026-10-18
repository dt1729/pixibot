"""Tests for provider resolution — exercised through providers.get/detect only.

No API key and no network: every assertion reads the *constructed* model object,
which is the guarantee the contract makes (clients are lazy).
"""

import unittest

from pixibot import providers
from pixibot.model import MockModel


def spec(depth=None):
    return {"id": "a1", "role": "programmer", "budget": {"depth": depth} if depth else {}}


class ProviderResolutionTest(unittest.TestCase):
    def test_every_name_is_constructible(self):
        for name in providers.NAMES:
            p = providers.get(name)
            self.assertEqual(p.name, name)
            self.assertTrue(p.label)

    def test_unknown_name_raises(self):
        with self.assertRaises(providers.UnknownProviderError):
            providers.get("nope")

    def test_offline_needs_no_environment_and_has_no_spokesbot(self):
        p = providers.get("offline")
        self.assertFalse(p.live)
        self.assertIsNone(p.spokesbot())
        self.assertIsInstance(p.model_factory()(spec("senior")), MockModel)
        self.assertIsInstance(p.tpm_model("a url shortener"), MockModel)

    def test_anthropic_depth_tiers_and_effort(self):
        factory = providers.get("anthropic").model_factory()
        junior = factory(spec("junior"))
        principal = factory(spec("principal"))
        self.assertEqual(junior.model_id, "claude-sonnet-5")
        self.assertEqual(junior.effort, "medium")
        self.assertEqual(principal.model_id, "claude-opus-4-8")
        self.assertEqual(principal.effort, "xhigh")

    def test_hard_development_routes_principal_to_the_strongest_model(self):
        factory = providers.get("anthropic").model_factory(hard=True)
        principal = factory(spec("principal"))
        self.assertEqual(principal.model_id, "claude-fable-5")
        self.assertEqual(principal.effort, "xhigh")
        # hard raises effort at every tier, but only principal changes model
        junior = factory(spec("junior"))
        self.assertEqual(junior.model_id, "claude-sonnet-5")
        self.assertEqual(junior.effort, "xhigh")

    def test_openai_compatible_provider_carries_base_url_and_key_var(self):
        model = providers.get("gemini").model_factory(hard=True)(spec("principal"))
        self.assertEqual(model.model_id, "gemini-2.5-pro")
        self.assertIn("generativelanguage", model.base_url)
        self.assertEqual(model.api_key_env, "GEMINI_API_KEY")

    def test_missing_or_unknown_depth_falls_back_to_senior(self):
        factory = providers.get("anthropic").model_factory()
        self.assertEqual(factory(spec()).model_id, "claude-opus-4-8")
        self.assertEqual(factory(spec("wizard")).model_id, "claude-opus-4-8")
        self.assertEqual(factory({"id": "a1"}).model_id, "claude-opus-4-8")

    def test_spokesbot_is_the_cheap_uncached_model(self):
        bot = providers.get("anthropic").spokesbot()
        self.assertEqual(bot.model_id, "claude-haiku-4-5")
        self.assertFalse(bot.use_cache)      # the snapshot changes every call
        self.assertFalse(bot.use_thinking)   # Haiku rejects adaptive thinking


class ClaudeCliProviderTest(unittest.TestCase):
    """The CLI provider spends a subscription, so it is chosen, never detected."""

    def test_it_is_a_known_provider_and_is_live(self):
        p = providers.get("claude-cli")
        self.assertEqual(p.name, "claude-cli")
        self.assertTrue(p.live)
        self.assertIn("subscription", p.label)

    def test_depth_picks_a_cheaper_model_than_the_api_tiers(self):
        factory = providers.get("claude-cli").model_factory()
        self.assertEqual(factory(spec("junior")).model_id, "claude-haiku-4-5")
        self.assertEqual(factory(spec("senior")).model_id, "claude-sonnet-5")
        self.assertEqual(factory(spec("principal")).model_id, "claude-opus-5")

    def test_hard_development_routes_principal_to_fable(self):
        factory = providers.get("claude-cli").model_factory(hard=True)
        self.assertEqual(factory(spec("principal")).model_id, "claude-fable-5-1")

    def test_the_spokesbot_is_the_cheap_model(self):
        self.assertEqual(providers.get("claude-cli").spokesbot().model_id,
                         "claude-haiku-4-5")

    def test_building_it_starts_no_process(self):
        # construction must not shell out: an unconfigured machine still imports
        providers.get("claude-cli").tpm_model("anything")


class DetectTest(unittest.TestCase):
    def test_no_keys_is_offline(self):
        self.assertEqual(providers.detect({}), "offline")

    def test_the_cli_provider_is_never_detected(self):
        # it spends the operator's subscription: that is a person's decision
        for env in ({}, {"ANTHROPIC_API_KEY": "x"}, {"GEMINI_API_KEY": "x"}):
            self.assertNotEqual(providers.detect(env), "claude-cli")

    def test_each_key_selects_its_provider(self):
        self.assertEqual(providers.detect({"ANTHROPIC_API_KEY": "x"}), "anthropic")
        self.assertEqual(providers.detect({"GEMINI_API_KEY": "x"}), "gemini")
        self.assertEqual(providers.detect({"OPENROUTER_API_KEY": "x"}), "openrouter")

    def test_cheapest_key_wins_when_several_are_present(self):
        env = {"ANTHROPIC_API_KEY": "x", "OPENROUTER_API_KEY": "x", "GEMINI_API_KEY": "x"}
        self.assertEqual(providers.detect(env), "gemini")
        del env["GEMINI_API_KEY"]
        self.assertEqual(providers.detect(env), "openrouter")
        del env["OPENROUTER_API_KEY"]
        self.assertEqual(providers.detect(env), "anthropic")

    def test_empty_key_value_does_not_count_as_configured(self):
        self.assertEqual(providers.detect({"GEMINI_API_KEY": ""}), "offline")


if __name__ == "__main__":
    unittest.main()
