"""
The OpenAI-compatible provider (Mistral / Groq / Cerebras), tested entirely offline.

The transport is injected, so this suite needs no key, no network and no money —
the same rule the rest of the gate lives by. Everything vendor-shaped is asserted
here: payload shape, the auth header, where the token counts come from, what a
truncated response does, and the cost arithmetic.

The point of this file is not that Mistral works. It is that the PROVIDER SEAM
works for a second vendor — a one-provider library has no evidence its abstraction
is real.
"""
import contextlib
import os
import unittest

from baton.agent import AgentSpec
from baton.packet import Baton
from baton.providers import mistral, openai_compat
from baton.providers.base import ProviderError, UnmeteredModel, cost_from_tokens

AGENT = AgentSpec("cto", "# CTO", frozenset({"frontend_engineer"}))
BATON = Baton("t1", 1, "charter", "cto", "design it",
              open_questions=("must cite sources",))
BLOCK = '```handoff\n{"decision": "HANDOFF", "to": "frontend_engineer", "goal": "g"}\n```'


def reply(text=BLOCK, in_tok=1000, out_tok=200, finish="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok,
                      "total_tokens": in_tok + out_tok}}


@contextlib.contextmanager
def env(name, value):
    before = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before


class Recorder:
    """Stand-in transport. Records what the provider tried to send."""

    def __init__(self, response=None, raises=None):
        self.response = response if response is not None else reply()
        self.raises = raises
        self.calls = []

    def __call__(self, url, payload, headers=None, timeout=None):
        self.calls.append({"url": url, "payload": payload, "headers": headers,
                           "timeout": timeout})
        if self.raises:
            raise self.raises
        return self.response


class KeyResolution(unittest.TestCase):
    def test_explicit_key_wins(self):
        self.assertEqual("abc", mistral.resolve_key("abc"))

    def test_env_var_is_used_when_no_key_is_passed(self):
        with env("MISTRAL_API_KEY", "from-env"):
            self.assertEqual("from-env", mistral.resolve_key())

    def test_a_missing_key_says_exactly_what_to_set(self):
        with env("MISTRAL_API_KEY", None):
            with self.assertRaises(ProviderError) as cm:
                mistral.resolve_key()
        self.assertIn("MISTRAL_API_KEY", str(cm.exception))

    def test_the_key_travels_as_a_bearer_header_never_in_the_url(self):
        """A key in a query string lands in logs and proxy history."""
        rec = Recorder()
        mistral.provider(api_key="secret-key", transport=rec)(AGENT, BATON, "p")
        self.assertEqual("Bearer secret-key",
                         rec.calls[0]["headers"]["Authorization"])
        self.assertNotIn("secret-key", rec.calls[0]["url"])


class VendorRegistry(unittest.TestCase):
    def test_each_known_vendor_has_a_base_url_a_model_and_a_rate_card(self):
        for name, spec in openai_compat.VENDORS.items():
            self.assertTrue(spec["base_url"].startswith("https://"), name)
            self.assertIn(spec["model"], spec["prices"],
                          f"{name}: default model is not in its own rate card")

    def test_an_unknown_vendor_names_the_ones_that_exist(self):
        with self.assertRaises(ProviderError) as cm:
            openai_compat.provider(api_key="k", vendor="nope", model="m")
        self.assertIn("mistral", str(cm.exception))

    def test_an_unregistered_host_still_works_with_base_url_and_prices(self):
        """The registry is a convenience, not a gate. A vendor shipped tomorrow
        must be usable without editing this library."""
        rec = Recorder()
        d = openai_compat.provider(api_key="k", base_url="https://host/v1",
                                   model="m", prices={"m": {"in": 1.0, "out": 1.0}},
                                   transport=rec)
        d(AGENT, BATON, "p")
        self.assertEqual("https://host/v1/chat/completions", rec.calls[0]["url"])

    def test_groq_and_cerebras_reach_their_own_hosts(self):
        for vendor, host in (("groq", "api.groq.com"),
                             ("cerebras", "api.cerebras.ai")):
            rec = Recorder()
            openai_compat.provider(api_key="k", vendor=vendor, transport=rec)(
                AGENT, BATON, "p")
            self.assertIn(host, rec.calls[0]["url"])


class RequestShape(unittest.TestCase):
    def test_the_prompt_is_sent_verbatim_as_a_user_message(self):
        rec = Recorder()
        mistral.provider(api_key="k", transport=rec)(AGENT, BATON, "THE WHOLE PROMPT")
        msgs = rec.calls[0]["payload"]["messages"]
        self.assertEqual([{"role": "user", "content": "THE WHOLE PROMPT"}], msgs)

    def test_the_model_name_is_in_the_body_not_the_url(self):
        rec = Recorder()
        mistral.provider(api_key="k", model="mistral-small-latest",
                         transport=rec)(AGENT, BATON, "p")
        self.assertEqual("mistral-small-latest", rec.calls[0]["payload"]["model"])
        self.assertTrue(rec.calls[0]["url"].endswith("/chat/completions"))

    def test_temperature_and_limit_are_carried(self):
        rec = Recorder()
        mistral.provider(api_key="k", temperature=0.7, max_output_tokens=4096,
                         transport=rec)(AGENT, BATON, "p")
        self.assertEqual(0.7, rec.calls[0]["payload"]["temperature"])
        self.assertEqual(4096, rec.calls[0]["payload"]["max_tokens"])

    def test_json_response_format_is_NOT_forced(self):
        """The contract asks for prose and THEN a fenced routing block. Forcing
        a JSON-only response would make that shape impossible."""
        rec = Recorder()
        mistral.provider(api_key="k", transport=rec)(AGENT, BATON, "p")
        self.assertNotIn("response_format", rec.calls[0]["payload"])

    def test_system_instruction_is_optional_and_omitted_by_default(self):
        rec = Recorder()
        mistral.provider(api_key="k", transport=rec)(AGENT, BATON, "p")
        self.assertEqual(1, len(rec.calls[0]["payload"]["messages"]))
        rec2 = Recorder()
        mistral.provider(api_key="k", transport=rec2,
                         system_instruction="be terse")(AGENT, BATON, "p")
        self.assertEqual("system", rec2.calls[0]["payload"]["messages"][0]["role"])


class ResponseHandling(unittest.TestCase):
    def test_text_comes_back_in_the_dispatch_result(self):
        d = mistral.provider(api_key="k", transport=Recorder(reply(BLOCK)))
        self.assertEqual(BLOCK, d(AGENT, BATON, "p").text)

    def test_list_shaped_content_is_concatenated(self):
        data = {"choices": [{"message": {"content": [{"text": "aa"},
                                                     {"text": "bb"}]}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        d = mistral.provider(api_key="k", transport=Recorder(data))
        self.assertEqual("aabb", d(AGENT, BATON, "p").text)

    def test_real_token_counts_are_used_not_estimates(self):
        d = mistral.provider(api_key="k",
                             transport=Recorder(reply(in_tok=12345, out_tok=678)))
        r = d(AGENT, BATON, "p")
        self.assertEqual(12345, r.in_tokens)
        self.assertEqual(678, r.out_tokens)

    def test_missing_usage_falls_back_to_an_estimate(self):
        data = {"choices": [{"message": {"content": "hello there"}}]}
        d = mistral.provider(api_key="k", transport=Recorder(data))
        r = d(AGENT, BATON, "a longer prompt than the reply")
        self.assertGreater(r.in_tokens, 0)
        self.assertGreater(r.out_tokens, 0)

    def test_no_choices_becomes_a_dispatch_error_not_empty_text(self):
        d = mistral.provider(api_key="k",
                             transport=Recorder({"error": {"message": "filtered"}}))
        r = d(AGENT, BATON, "p")
        self.assertEqual("", r.text)
        self.assertIn("filtered", r.error)

    def test_a_truncated_completion_reports_the_finish_reason(self):
        """finish_reason='length' with empty content must not look like a model
        that chose to say nothing — the runtime would burn a repair retry."""
        data = {"choices": [{"message": {"content": "  "},
                             "finish_reason": "length"}]}
        d = mistral.provider(api_key="k", transport=Recorder(data))
        self.assertIn("length", d(AGENT, BATON, "p").error)

    def test_a_null_content_is_an_error_not_an_empty_answer(self):
        data = {"choices": [{"message": {"content": None},
                             "finish_reason": "tool_calls"}]}
        d = mistral.provider(api_key="k", transport=Recorder(data))
        self.assertIn("tool_calls", d(AGENT, BATON, "p").error)

    def test_a_429_becomes_a_dispatch_error_carrying_the_status(self):
        """The whole reason this provider exists is a rate limit. bench/run.py
        stops after three of these; it can only do that if the status survives
        into the ledger instead of being flattened into a generic failure."""
        d = mistral.provider(api_key="k",
                             transport=Recorder(raises=ProviderError("HTTP 429: slow down")))
        r = d(AGENT, BATON, "p")
        self.assertEqual("", r.text)
        self.assertIn("429", r.error)


class CostArithmetic(unittest.TestCase):
    """A cost meter that reads low does not raise — it silently disables the
    budget ceiling. On a free tier no money moves, but the ceiling still has to
    bound the run, and the ledger still has to compare to the paid batches."""

    def test_a_million_tokens_costs_exactly_the_quoted_rate(self):
        self.assertAlmostEqual(0.50, cost_from_tokens(
            mistral.PRICES, "mistral-large-latest", 1_000_000, 0))
        self.assertAlmostEqual(1.50, cost_from_tokens(
            mistral.PRICES, "mistral-large-latest", 0, 1_000_000))

    def test_the_meter_is_not_off_by_a_thousand_in_either_direction(self):
        usd = cost_from_tokens(mistral.PRICES, "mistral-large-latest",
                               1_000_000, 1_000_000)
        self.assertGreater(usd, 0.10, "meter reads impossibly low")
        self.assertLess(usd, 100.0, "meter reads impossibly high")

    def test_an_unpriced_model_raises_instead_of_costing_zero(self):
        with self.assertRaises(UnmeteredModel):
            cost_from_tokens(mistral.PRICES, "mistral-enormous-9", 10, 10)

    def test_building_a_provider_on_an_unpriced_model_fails_immediately(self):
        """At construction, not on hop 7 of a run already underway. Mistral ships
        models faster than this rate card is edited, exactly like Gemini."""
        with self.assertRaises(UnmeteredModel) as cm:
            mistral.provider(api_key="k", model="mistral-enormous-9")
        self.assertIn("prices=", str(cm.exception))

    def test_a_free_tier_run_still_records_a_nonzero_shadow_cost(self):
        """Mistral's Experiment tier bills nothing. If that arrived in the ledger
        as 0.0 the budget ceiling would never trip and the bench rows would not
        compare to the Gemini batches."""
        d = mistral.provider(api_key="k",
                             transport=Recorder(reply(in_tok=1_000_000, out_tok=0)))
        self.assertAlmostEqual(0.50, d(AGENT, BATON, "p").cost_usd, places=6)

    def test_supplying_a_rate_card_unblocks_a_new_model(self):
        d = mistral.provider(api_key="k", model="mistral-enormous-9",
                             prices={"mistral-enormous-9": {"in": 1.0, "out": 2.0}},
                             transport=Recorder(reply(in_tok=1_000_000, out_tok=0)))
        self.assertAlmostEqual(1.0, d(AGENT, BATON, "p").cost_usd, places=6)


class ListModels(unittest.TestCase):
    def test_ids_are_returned_sorted(self):
        payload = {"data": [{"id": "mistral-small-latest"},
                            {"id": "mistral-large-latest"}]}
        names = mistral.list_models(api_key="k",
                                    transport=lambda url, headers: payload)
        self.assertEqual(["mistral-large-latest", "mistral-small-latest"], names)


class EndToEndThroughTheRuntime(unittest.TestCase):
    """The provider satisfies the dispatch seam `baton.run` expects — the same
    assertion the Gemini suite makes, which is what proves the seam is a seam."""

    def test_a_full_run_drives_on_openai_shaped_responses(self):
        from baton.agent import GATE
        from baton.charter import Charter
        from baton.runtime import run

        agents = {
            "cto": AgentSpec("cto", "# CTO", frozenset({"gate_agent"})),
            "gate_agent": AgentSpec("gate_agent", "# GATE", frozenset({"cto"}),
                                    role=GATE)}
        ch = Charter(brief="b", entry_agent="cto", gate_agent="gate_agent",
                     agent_pool=frozenset(agents), acceptance_criteria=("x",),
                     budget_ceiling_usd=100.0, max_hops=5)

        # PROPOSE_DONE carries the work product: a ratify with no artifact
        # is not a delivery, and the runtime now says so.
        scripted = ['```handoff\n{"decision": "PROPOSE_DONE", "summary": "done", "artifacts": [{"path": "out/result.json", "description": "the deliverable"}]}\n```',
                    '```handoff\n{"decision": "RATIFY", "summary": "verified", "coverage": ["out/result.json"]}\n```']

        class Sequence:
            def __init__(self):
                self.n = 0

            def __call__(self, url, payload, headers=None, timeout=None):
                text = scripted[min(self.n, len(scripted) - 1)]
                self.n += 1
                return reply(text, in_tok=500, out_tok=100)

        d = mistral.provider(api_key="k", transport=Sequence())
        r = run(ch, agents, d)
        self.assertEqual("ratified", r.terminal_reason)
        self.assertGreater(r.spend_usd, 0, "the run recorded no cost at all")


class TheGateCannotSpend(unittest.TestCase):
    def test_the_real_http_path_refuses_under_the_flag(self):
        from baton.providers.base import FORBID_ENV, http_json
        with env(FORBID_ENV, "1"):
            with self.assertRaises(ProviderError) as cm:
                http_json("https://api.mistral.ai/v1/chat/completions", {"a": 1})
        self.assertIn(FORBID_ENV, str(cm.exception))

    def test_an_injected_transport_is_unaffected_by_the_flag(self):
        from baton.providers.base import FORBID_ENV
        with env(FORBID_ENV, "1"):
            d = mistral.provider(api_key="k", transport=Recorder())
            self.assertEqual(BLOCK, d(AGENT, BATON, "p").text)


if __name__ == "__main__":
    unittest.main()
