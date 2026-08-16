"""
The Gemini provider, tested entirely offline.

The transport is injected, so this suite needs no key, no network and no money —
the same rule the rest of the gate lives by. Everything vendor-shaped is asserted
here: payload shape, where the token counts come from, what a blocked response
does, and the cost arithmetic.
"""
import contextlib
import os
import unittest

from baton.agent import AgentSpec
from baton.packet import Baton
from baton.providers import gemini
from baton.providers.base import (MAX_ATTEMPTS, ProviderError, approx_tokens,
                                  cost_from_tokens, http_json)

AGENT = AgentSpec("cto", "# CTO", frozenset({"frontend_engineer"}))
BATON = Baton("t1", 1, "charter", "cto", "design it",
              open_questions=("must cite sources",))
BLOCK = '```handoff\n{"decision": "HANDOFF", "to": "frontend_engineer", "goal": "g"}\n```'


def reply(text=BLOCK, in_tok=1000, out_tok=200, finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": text}]},
                            "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": in_tok,
                              "candidatesTokenCount": out_tok,
                              "totalTokenCount": in_tok + out_tok}}


@contextlib.contextmanager
def spending_allowed():
    """verify.sh exports BATON_FORBID_REAL_DISPATCH, which is exactly what stops
    the gate spending money. The retry tests need the real http_json path, so
    they lift the flag around a MOCKED urlopen — no request ever leaves."""
    from baton.providers.base import FORBID_ENV
    before = os.environ.pop(FORBID_ENV, None)
    try:
        yield
    finally:
        if before is not None:
            os.environ[FORBID_ENV] = before


class Recorder:
    """Stand-in transport. Records what the provider tried to send."""

    def __init__(self, response=None, raises=None):
        self.response = response if response is not None else reply()
        self.raises = raises
        self.calls = []

    def __call__(self, url, payload, timeout=None):
        self.calls.append({"url": url, "payload": payload, "timeout": timeout})
        if self.raises:
            raise self.raises
        return self.response


class KeyResolution(unittest.TestCase):
    def test_explicit_key_wins(self):
        self.assertEqual("abc", gemini.resolve_key("abc"))

    def test_env_var_is_used_when_no_key_is_passed(self):
        import os
        before = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "from-env"
        try:
            self.assertEqual("from-env", gemini.resolve_key())
        finally:
            if before is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = before

    def test_a_missing_key_says_exactly_what_to_set(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in gemini.KEY_ENV_VARS}
        try:
            with self.assertRaises(ProviderError) as cm:
                gemini.resolve_key()
            self.assertIn("GEMINI_API_KEY", str(cm.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class RequestShape(unittest.TestCase):
    def test_the_prompt_is_sent_verbatim_as_a_user_part(self):
        rec = Recorder()
        d = gemini.provider(api_key="k", transport=rec)
        d(AGENT, BATON, "THE WHOLE PROMPT")
        parts = rec.calls[0]["payload"]["contents"][0]["parts"]
        self.assertEqual("THE WHOLE PROMPT", parts[0]["text"])

    def test_the_model_name_is_in_the_url(self):
        rec = Recorder()
        gemini.provider(api_key="k", model="gemini-2.0-flash", transport=rec)(
            AGENT, BATON, "p")
        self.assertIn("gemini-2.0-flash:generateContent", rec.calls[0]["url"])

    def test_generation_config_carries_temperature_and_limit(self):
        rec = Recorder()
        gemini.provider(api_key="k", temperature=0.7, max_output_tokens=4096,
                        transport=rec)(AGENT, BATON, "p")
        cfg = rec.calls[0]["payload"]["generationConfig"]
        self.assertEqual(0.7, cfg["temperature"])
        self.assertEqual(4096, cfg["maxOutputTokens"])

    def test_json_mime_type_is_NOT_forced(self):
        """The contract asks for prose and THEN a fenced routing block. Forcing
        a JSON-only response would make that shape impossible."""
        rec = Recorder()
        gemini.provider(api_key="k", transport=rec)(AGENT, BATON, "p")
        self.assertNotIn("responseMimeType",
                         rec.calls[0]["payload"]["generationConfig"])

    def test_system_instruction_is_optional_and_omitted_by_default(self):
        rec = Recorder()
        gemini.provider(api_key="k", transport=rec)(AGENT, BATON, "p")
        self.assertNotIn("systemInstruction", rec.calls[0]["payload"])
        rec2 = Recorder()
        gemini.provider(api_key="k", transport=rec2,
                        system_instruction="be terse")(AGENT, BATON, "p")
        self.assertIn("systemInstruction", rec2.calls[0]["payload"])


class ResponseHandling(unittest.TestCase):
    def test_text_comes_back_in_the_dispatch_result(self):
        d = gemini.provider(api_key="k", transport=Recorder(reply(BLOCK)))
        self.assertEqual(BLOCK, d(AGENT, BATON, "p").text)

    def test_multi_part_responses_are_concatenated(self):
        data = {"candidates": [{"content": {"parts": [{"text": "aa"}, {"text": "bb"}]}}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}}
        d = gemini.provider(api_key="k", transport=Recorder(data))
        self.assertEqual("aabb", d(AGENT, BATON, "p").text)

    def test_real_token_counts_are_used_not_estimates(self):
        d = gemini.provider(api_key="k", transport=Recorder(reply(in_tok=12345,
                                                                 out_tok=678)))
        r = d(AGENT, BATON, "p")
        self.assertEqual(12345, r.in_tokens)
        self.assertEqual(678, r.out_tokens)

    def test_missing_usage_falls_back_to_an_estimate(self):
        data = {"candidates": [{"content": {"parts": [{"text": "hello there"}]}}]}
        d = gemini.provider(api_key="k", transport=Recorder(data))
        r = d(AGENT, BATON, "a longer prompt than the reply")
        self.assertGreater(r.in_tokens, 0)
        self.assertGreater(r.out_tokens, 0)

    def test_a_blocked_prompt_becomes_a_dispatch_error_not_empty_text(self):
        """An empty string would send the runtime into a repair retry for a
        prompt that will never be answered — money spent to fix nothing."""
        data = {"promptFeedback": {"blockReason": "SAFETY"}}
        d = gemini.provider(api_key="k", transport=Recorder(data))
        r = d(AGENT, BATON, "p")
        self.assertEqual("", r.text)
        self.assertIn("SAFETY", r.error)

    def test_an_empty_completion_reports_the_finish_reason(self):
        data = {"candidates": [{"content": {"parts": [{"text": "  "}]},
                                "finishReason": "MAX_TOKENS"}]}
        d = gemini.provider(api_key="k", transport=Recorder(data))
        self.assertIn("MAX_TOKENS", d(AGENT, BATON, "p").error)

    def test_a_transport_failure_becomes_a_dispatch_error(self):
        d = gemini.provider(api_key="k",
                            transport=Recorder(raises=ProviderError("HTTP 503")))
        r = d(AGENT, BATON, "p")
        self.assertEqual("", r.text)
        self.assertIn("503", r.error)


class CostArithmetic(unittest.TestCase):
    """A cost meter that reads low does not raise — it silently disables the
    budget ceiling. This vault has been burned by exactly that before."""

    def test_prices_are_per_million_tokens(self):
        prices = {"m": {"in": 1.00, "out": 10.00}}
        self.assertAlmostEqual(0.001, cost_from_tokens(prices, "m", 1_000, 0))
        self.assertAlmostEqual(0.010, cost_from_tokens(prices, "m", 0, 1_000))

    def test_a_million_input_tokens_costs_exactly_the_quoted_rate(self):
        prices = {"m": {"in": 0.30, "out": 2.50}}
        self.assertAlmostEqual(0.30, cost_from_tokens(prices, "m", 1_000_000, 0))
        self.assertAlmostEqual(2.50, cost_from_tokens(prices, "m", 0, 1_000_000))

    def test_the_meter_is_not_off_by_a_thousand_in_either_direction(self):
        """Guards the specific failure mode: a 1000x error in either direction
        passes every 'is it positive' check while making the ceiling useless."""
        usd = cost_from_tokens(gemini.PRICES, "gemini-2.5-flash", 1_000_000,
                               1_000_000)
        self.assertGreater(usd, 0.10, "meter reads impossibly low")
        self.assertLess(usd, 100.0, "meter reads impossibly high")

    def test_an_unknown_model_costs_zero_rather_than_crashing(self):
        self.assertEqual(0.0, cost_from_tokens(gemini.PRICES, "gemini-99", 10, 10))

    def test_the_dispatch_result_carries_a_real_cost(self):
        d = gemini.provider(api_key="k", model="gemini-2.5-flash",
                            transport=Recorder(reply(in_tok=1_000_000,
                                                     out_tok=1_000_000)))
        self.assertAlmostEqual(2.80, d(AGENT, BATON, "p").cost_usd, places=6)

    def test_a_caller_can_override_the_rate_card(self):
        d = gemini.provider(api_key="k", model="whatever",
                            prices={"whatever": {"in": 1.0, "out": 1.0}},
                            transport=Recorder(reply(in_tok=1_000_000, out_tok=0)))
        self.assertAlmostEqual(1.0, d(AGENT, BATON, "p").cost_usd, places=6)


class RetryPolicy(unittest.TestCase):
    def test_retries_are_bounded(self):
        self.assertLessEqual(MAX_ATTEMPTS, 3,
                             "an unbounded retry is how a cheap run becomes an "
                             "afternoon of silent backoff")

    def test_a_429_is_retried_then_gives_up(self):
        import urllib.error
        import io
        calls = []

        def always_429(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {},
                                         io.BytesIO(b"slow down"))

        import unittest.mock as mock
        with spending_allowed(), mock.patch("urllib.request.urlopen", always_429):
            with self.assertRaises(ProviderError) as cm:
                http_json("https://example.invalid", {"a": 1}, _sleep=lambda s: None)
        self.assertEqual(MAX_ATTEMPTS, len(calls))
        self.assertIn("429", str(cm.exception))

    def test_a_400_is_not_retried(self):
        """A malformed request will be malformed the second time too."""
        import urllib.error
        import io
        calls = []

        def always_400(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 400, "bad request", {},
                                         io.BytesIO(b"invalid argument"))

        import unittest.mock as mock
        with spending_allowed(), mock.patch("urllib.request.urlopen", always_400):
            with self.assertRaises(ProviderError):
                http_json("https://example.invalid", {"a": 1}, _sleep=lambda s: None)
        self.assertEqual(1, len(calls))


class ListModels(unittest.TestCase):
    def test_only_models_that_can_generate_are_returned(self):
        payload = {"models": [
            {"name": "models/gemini-2.5-flash",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004",
             "supportedGenerationMethods": ["embedContent"]}]}
        names = gemini.list_models(api_key="k", transport=lambda url: payload)
        self.assertEqual(["gemini-2.5-flash"], names)


class EndToEndThroughTheRuntime(unittest.TestCase):
    """The provider satisfies the dispatch seam `baton.run` expects."""

    def test_a_full_run_drives_on_gemini_shaped_responses(self):
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

        scripted = ['```handoff\n{"decision": "PROPOSE_DONE", "summary": "done"}\n```',
                    '```handoff\n{"decision": "RATIFY", "summary": "verified"}\n```']

        class Sequence:
            def __init__(self):
                self.n = 0

            def __call__(self, url, payload, timeout=None):
                text = scripted[min(self.n, len(scripted) - 1)]
                self.n += 1
                return reply(text, in_tok=500, out_tok=100)

        d = gemini.provider(api_key="k", model="gemini-2.5-flash",
                            transport=Sequence())
        r = run(ch, agents, d)
        self.assertEqual("ratified", r.terminal_reason)
        self.assertGreater(r.spend_usd, 0, "the run recorded no cost at all")


if __name__ == "__main__":
    unittest.main()


class TheGateCannotSpend(unittest.TestCase):
    """verify.sh sets BATON_FORBID_REAL_DISPATCH. Prove that flag actually stops
    a paid call, rather than trusting that no test happened to make one."""

    def test_the_real_http_path_refuses_under_the_flag(self):
        import os
        from baton.providers.base import FORBID_ENV
        before = os.environ.get(FORBID_ENV)
        os.environ[FORBID_ENV] = "1"
        try:
            with self.assertRaises(ProviderError) as cm:
                http_json("https://generativelanguage.googleapis.com/whatever",
                          {"a": 1})
            self.assertIn(FORBID_ENV, str(cm.exception))
        finally:
            if before is None:
                os.environ.pop(FORBID_ENV, None)
            else:
                os.environ[FORBID_ENV] = before

    def test_an_injected_transport_is_unaffected_by_the_flag(self):
        """Otherwise the flag would make the whole offline suite unrunnable."""
        import os
        from baton.providers.base import FORBID_ENV
        before = os.environ.get(FORBID_ENV)
        os.environ[FORBID_ENV] = "1"
        try:
            d = gemini.provider(api_key="k", transport=Recorder())
            self.assertEqual(BLOCK, d(AGENT, BATON, "p").text)
        finally:
            if before is None:
                os.environ.pop(FORBID_ENV, None)
            else:
                os.environ[FORBID_ENV] = before
