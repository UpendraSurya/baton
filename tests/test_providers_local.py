"""
Edge/local execution: baton's `openai_compat` provider against a local,
OpenAI-compatible server (Ollama, LM Studio, vLLM) instead of a hosted vendor.

Entirely offline, like every other provider test — the transport is injected,
so this proves the REQUEST SHAPE is identical to a hosted call, not that a real
local server accepts it. It cannot prove that: no gate in this repo may reach a
network, and no local server is assumed to be running. A human must run the
live half at least once — run `examples/smoke_local.py` — before the
"this works against Ollama" claim goes in the README as more than "the request
shape is unchanged."

No new provider module. `openai_compat.provider(base_url=..., prices=...)`
already accepts an arbitrary host — this file is the missing evidence that the
existing arbitrary-host path is actually exercised for a *local* one, not just
`https://host/v1` in the abstract (see test_providers_openai_compat.py's
`test_an_unregistered_host_still_works_with_base_url_and_prices`, which never
uses a loopback address or a zero-priced model).
"""
import unittest

from baton.agent import AgentSpec
from baton.packet import Baton
from baton.providers import openai_compat
from baton.providers.base import cost_from_tokens

AGENT = AgentSpec("cto", "# CTO", frozenset({"frontend_engineer"}))
BATON = Baton("t1", 1, "charter", "cto", "design it")
BLOCK = '```handoff\n{"decision": "HANDOFF", "to": "frontend_engineer", "goal": "g"}\n```'


def reply(text=BLOCK, in_tok=100, out_tok=20):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok,
                      "total_tokens": in_tok + out_tok}}


class Recorder:
    """Same stand-in transport every other provider test uses. No local server
    is contacted — this never leaves the process."""

    def __init__(self, response=None):
        self.response = response if response is not None else reply()
        self.calls = []

    def __call__(self, url, payload, headers=None, timeout=None):
        self.calls.append({"url": url, "payload": payload, "headers": headers,
                           "timeout": timeout})
        return self.response


# Local OpenAI-compatible servers commonly bind these; neither is contacted here.
OLLAMA_URL = "http://localhost:11434/v1"
LMSTUDIO_URL = "http://localhost:1234/v1"


class LocalHostIsNotSpecialCased(unittest.TestCase):
    """provider() must not silently require https:// — that would make the
    'this already works locally' claim false rather than merely untested."""

    def test_a_loopback_http_url_is_accepted(self):
        rec = Recorder()
        d = openai_compat.provider(
            api_key="not-needed", base_url=OLLAMA_URL, model="llama3.1",
            prices={"llama3.1": {"in": 0.0, "out": 0.0}}, transport=rec)
        d(AGENT, BATON, "p")
        self.assertEqual(f"{OLLAMA_URL}/chat/completions", rec.calls[0]["url"])

    def test_request_shape_matches_a_hosted_vendor_call(self):
        """The whole point of `base_url=` is that a local server is not a
        second code path. Same payload keys as the Mistral/Groq tests."""
        rec = Recorder()
        d = openai_compat.provider(
            api_key="not-needed", base_url=LMSTUDIO_URL, model="qwen2.5-7b",
            prices={"qwen2.5-7b": {"in": 0.0, "out": 0.0}}, transport=rec)
        d(AGENT, BATON, "THE WHOLE PROMPT")
        payload = rec.calls[0]["payload"]
        self.assertEqual("qwen2.5-7b", payload["model"])
        self.assertEqual([{"role": "user", "content": "THE WHOLE PROMPT"}],
                         payload["messages"])
        self.assertNotIn("response_format", payload)


class FreeIsExplicitNotUnmetered(unittest.TestCase):
    """A local model has no rate card. The library's own rule (CHANGELOG:
    "cost_from_tokens raises UnmeteredModel ... never returns 0.0") means a
    caller MUST say the model costs nothing on purpose — omitting it entirely
    raises, it does not quietly cost $0. This is the one piece of friction a
    local setup adds over a hosted one, documented by making it pass here."""

    def test_an_explicit_zero_price_computes_zero_and_does_not_raise(self):
        cost = cost_from_tokens({"llama3.1": {"in": 0.0, "out": 0.0}},
                                "llama3.1", in_tokens=10_000, out_tokens=2_000)
        self.assertEqual(0.0, cost)

    def test_dispatch_against_a_free_local_model_reports_zero_spend(self):
        rec = Recorder(response=reply(in_tok=5000, out_tok=800))
        d = openai_compat.provider(
            api_key="not-needed", base_url=OLLAMA_URL, model="llama3.1",
            prices={"llama3.1": {"in": 0.0, "out": 0.0}}, transport=rec)
        result = d(AGENT, BATON, "p")
        self.assertEqual(0.0, result.cost_usd)
        self.assertEqual(5000, result.in_tokens)
        self.assertEqual(800, result.out_tokens)


if __name__ == "__main__":
    unittest.main()
