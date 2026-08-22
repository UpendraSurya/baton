"""
baton.providers.mistral — Mistral La Plateforme, over plain HTTPS.

Mistral speaks the OpenAI /v1/chat/completions shape, so the work lives in
`baton.providers.openai_compat`; this module is the named front door.

    from baton import run
    from baton.providers import mistral

    dispatch = mistral.provider()                      # reads MISTRAL_API_KEY
    dispatch = mistral.provider(model="mistral-small-latest")
    result = run(charter, agents, dispatch)

Get a key at https://console.mistral.ai — the free "Experiment" tier needs phone
verification, no card. Call `mistral.list_models()` first to confirm the key works
and see which models it reaches; that call is free and costs no rate limit worth
counting.

FREE TIER, checked 2026-08-21: 1 request/second, 500K tokens/minute, 1B tokens per
month PER MODEL. The free tier requires opting in to having your prompts used for
training — do not send anything through it you would not publish.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

from baton.providers import openai_compat
from baton.providers.openai_compat import MISTRAL_PRICES as PRICES

if TYPE_CHECKING:
    from baton.runtime import Dispatch

BASE_URL = openai_compat.VENDORS["mistral"]["base_url"]
MODEL: str = openai_compat.VENDORS["mistral"]["model"]
KEY_ENV_VARS = openai_compat.VENDORS["mistral"]["key_env"]


def resolve_key(api_key: str | None = None) -> str:
    return openai_compat.resolve_key(api_key, KEY_ENV_VARS)


def list_models(api_key: str | None = None,
                transport: Callable[..., dict[str, Any]] | None = None) -> list[str]:
    """Names your key can actually reach. Free — the cheapest way to prove a key
    works before spending a request on generation."""
    return openai_compat.list_models("mistral", api_key, transport=transport)


def provider(api_key: str | None = None, model: str = MODEL, *,
             temperature: float = 0.2, max_output_tokens: int = 8192,
             timeout: float = 120,
             prices: Mapping[str, Mapping[str, float]] | None = None,
             transport: Callable[..., dict[str, Any]] | None = None,
             system_instruction: str | None = None) -> Dispatch:
    """Build the dispatch callable `baton.run` needs."""
    kwargs: dict[str, Any] = dict(
        vendor="mistral", model=model, api_key=api_key,
        temperature=temperature, max_output_tokens=max_output_tokens,
        timeout=timeout, prices=prices, system_instruction=system_instruction)
    if transport is not None:
        kwargs["transport"] = transport
    return openai_compat.provider(**kwargs)
