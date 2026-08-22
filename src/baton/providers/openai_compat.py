"""
baton.providers.openai_compat — every vendor that speaks /v1/chat/completions.

Mistral, Groq, Cerebras and Together all expose the same JSON POST that OpenAI
defined, so they do not each deserve a module. One payload shape, one response
shape, one rate-card lookup; the only thing that varies is a base URL, an env var
name and a price list. Those live in VENDORS below.

    from baton import run
    from baton.providers import openai_compat

    dispatch = openai_compat.provider(vendor="mistral", model="mistral-large-latest")
    result = run(charter, agents, dispatch)

A vendor not in the registry still works — pass base_url and prices yourself:

    openai_compat.provider(base_url="https://host/v1", model="m",
                           prices={"m": {"in": 0.1, "out": 0.4}}, api_key="k")

No SDK, no `requests`. urllib posts JSON, which is all any of these need, and
tests/test_isolation.py fails the build if that ever stops being true.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable, Mapping

from baton.runtime import Dispatch, DispatchResult

if TYPE_CHECKING:
    from baton.agent import AgentSpec
    from baton.packet import Baton

from baton.providers.base import (ProviderError, UnmeteredModel, approx_tokens,
                                  cost_from_tokens, http_json)

# USD per 1,000,000 tokens. Hand-maintained, and therefore ALWAYS behind reality —
# vendors ship models faster than this file is edited. An unpriced model raises
# UnmeteredModel rather than costing 0.0, so the lag shows up as a loud error at
# construction instead of a budget ceiling that silently never trips.
#
# On a free tier no money moves, but these still matter: they are the shadow meter
# the Charter's budget_ceiling_usd bounds the run with, and they keep a free-tier
# ledger comparable to a paid one.
MISTRAL_PRICES = {
    "mistral-large-latest":  {"in": 0.50, "out": 1.50},
    "mistral-medium-latest": {"in": 1.50, "out": 7.50},
    "mistral-small-latest":  {"in": 0.15, "out": 0.60},
    "ministral-8b-latest":   {"in": 0.15, "out": 0.15},
    "ministral-3b-latest":   {"in": 0.10, "out": 0.10},
    "codestral-latest":      {"in": 0.30, "out": 0.90},
}

GROQ_PRICES = {
    "openai/gpt-oss-120b": {"in": 0.15, "out": 0.75},
    "openai/gpt-oss-20b":  {"in": 0.10, "out": 0.50},
    "qwen/qwen3.6-27b":    {"in": 0.10, "out": 0.50},
}

CEREBRAS_PRICES = {
    "gpt-oss-120b":  {"in": 0.25, "out": 0.69},
    "gemma-4-31b":   {"in": 0.10, "out": 0.40},
}

# base_url, the env vars to read a key from, a default model, and the rate card.
VENDORS: dict[str, dict[str, Any]] = {
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "key_env": ("MISTRAL_API_KEY",),
        "model": "mistral-large-latest",
        "prices": MISTRAL_PRICES,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": ("GROQ_API_KEY",),
        "model": "openai/gpt-oss-120b",
        "prices": GROQ_PRICES,
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": ("CEREBRAS_API_KEY",),
        "model": "gpt-oss-120b",
        "prices": CEREBRAS_PRICES,
    },
}


def resolve_key(api_key: str | None = None,
                key_env: tuple[str, ...] = ("OPENAI_API_KEY",)) -> str:
    if api_key:
        return api_key
    for name in key_env:
        value = os.environ.get(name)
        if value:
            return value
    raise ProviderError(
        "no API key: pass api_key=... or set " + " / ".join(key_env))


def list_models(vendor: str | None = None, api_key: str | None = None, *,
                base_url: str | None = None,
                transport: Callable[[str, Mapping[str, str]], dict[str, Any]]
                | None = None) -> list[str]:
    """Model names this key can actually reach. Free, and the cheapest way to
    prove a key works before spending anything or burning a rate limit."""
    spec = VENDORS.get(vendor or "", {})
    url = (base_url or spec.get("base_url") or "").rstrip("/") + "/models"
    key = resolve_key(api_key, tuple(spec.get("key_env", ("OPENAI_API_KEY",))))
    fetch = transport or _get_json
    data = fetch(url, {"Authorization": f"Bearer {key}"})
    out = [m.get("id", "") for m in data.get("data", [])]
    return sorted(n for n in out if n)


def _get_json(url: str, headers: Mapping[str, str]) -> dict[str, Any]:
    import json as _json
    import urllib.request
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def _extract_text(data: Mapping[str, Any]) -> str:
    """Pull the assistant text out, and say plainly why it is missing if it is.

    A truncated or filtered response is not an empty string. Returning "" would
    send the runtime into a repair retry for a prompt that will never be
    answered — money spent to fix nothing.
    """
    choices = data.get("choices") or []
    if not choices:
        err = data.get("error")
        raise ProviderError(f"no choices returned (error={err!r})" if err
                            else "no choices returned")
    choice = choices[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    if not isinstance(text, str):
        # Some servers return content as a list of parts.
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if not text.strip():
        raise ProviderError(
            f"empty completion (finish_reason={choice.get('finish_reason')!r})")
    return text


def _usage(data: Mapping[str, Any], prompt: str, text: str) -> tuple[int, int]:
    u = data.get("usage") or {}
    in_tok = u.get("prompt_tokens")
    out_tok = u.get("completion_tokens")
    if in_tok is None:
        in_tok = approx_tokens(prompt)
    if out_tok is None:
        out_tok = approx_tokens(text)
    return int(in_tok), int(out_tok)


def provider(api_key: str | None = None, model: str | None = None, *,
             vendor: str | None = None, base_url: str | None = None,
             temperature: float = 0.2, max_output_tokens: int = 8192,
             timeout: float = 120,
             prices: Mapping[str, Mapping[str, float]] | None = None,
             transport: Callable[..., dict[str, Any]] = http_json,
             system_instruction: str | None = None) -> Dispatch:
    """Build the dispatch callable `baton.run` needs.

    transport is injectable so the whole provider can be tested offline — the
    test suite must never need a network or a key.
    """
    spec = VENDORS.get(vendor or "", {})
    if vendor and not spec:
        raise ProviderError(
            f"unknown vendor {vendor!r}. Known: {', '.join(sorted(VENDORS))}. "
            "For anything else pass base_url= and prices= directly.")

    url_base = base_url or spec.get("base_url")
    if not url_base:
        raise ProviderError("no base_url: pass vendor= or base_url=")

    model_id = model or spec.get("model")
    if not model_id:
        raise ProviderError("no model: pass model=")

    rate_card = prices if prices is not None else spec.get("prices", {})
    # Fail at construction, not on hop 7 of a run that has already spent money.
    if model_id not in rate_card:
        raise UnmeteredModel(
            f"no price for {model_id!r}. Known: {', '.join(sorted(rate_card))}. "
            f"Pass prices={{{model_id!r}: {{'in': X, 'out': Y}}}} (USD per 1M "
            "tokens) — without it the budget ceiling cannot bound the run")

    key = resolve_key(api_key, tuple(spec.get("key_env", ("OPENAI_API_KEY",))))
    url = url_base.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}

    def dispatch(agent: AgentSpec, baton: Baton, prompt: str) -> DispatchResult:
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            # No response_format=json_object. The contract asks for prose and
            # THEN a fenced routing block; forcing JSON makes that shape
            # impossible. Same reasoning as the Gemini provider.
        }

        try:
            data = transport(url, payload, headers=headers, timeout=timeout)
            text = _extract_text(data)
        except ProviderError as exc:
            # A transport failure is not a parse failure, and a 429 is not a
            # negative result. Report it as an error so the runtime ends the run
            # and bench/run.py's stop rule can tell a dead quota from bad routing.
            return DispatchResult(text="", error=str(exc))

        in_tok, out_tok = _usage(data, prompt, text)
        return DispatchResult(
            text=text,
            cost_usd=cost_from_tokens(rate_card, model_id, in_tok, out_tok),
            in_tokens=in_tok, out_tokens=out_tok)

    dispatch.model_id = model_id
    dispatch.provider_name = vendor or "openai_compat"
    return dispatch
