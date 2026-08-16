"""
baton.providers.gemini — Google Gemini, over plain HTTPS.

No `google-genai`, no `google-generativeai`, no transitive dependency tree. The
Gemini REST API is a JSON POST, and `urllib` posts JSON, so that is all this is.

    from baton import run
    from baton.providers import gemini

    dispatch = gemini.provider(api_key="...", model="gemini-2.5-flash")
    result = run(charter, agents, dispatch)

Or let it read GEMINI_API_KEY / GOOGLE_API_KEY from the environment:

    dispatch = gemini.provider()

Call `gemini.list_models(api_key)` first if you want to confirm the key works and
see exactly which model names your key can reach. That call is free.
"""
import os

from baton.runtime import DispatchResult

from baton.providers.base import (ProviderError, approx_tokens, cost_from_tokens,
                                  http_json)

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
LIST_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

MODEL = "gemini-2.5-flash"
KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# USD per 1,000,000 tokens. VERIFY THESE against the live rate card before you
# trust a budget ceiling built on them — an undercounting meter silently disables
# the ceiling rather than erroring. Override with prices={...} if they drift.
PRICES = {
    "gemini-2.5-pro":     {"in": 1.25, "out": 10.00},
    "gemini-2.5-flash":   {"in": 0.30, "out": 2.50},
    "gemini-2.5-flash-lite": {"in": 0.10, "out": 0.40},
    "gemini-2.0-flash":   {"in": 0.10, "out": 0.40},
    "gemini-1.5-pro":     {"in": 1.25, "out": 5.00},
    "gemini-1.5-flash":   {"in": 0.075, "out": 0.30},
}


def resolve_key(api_key=None):
    if api_key:
        return api_key
    for name in KEY_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    raise ProviderError(
        "no Gemini API key: pass api_key=... or set "
        + " / ".join(KEY_ENV_VARS))


def list_models(api_key=None, transport=None):
    """Names your key can actually reach. Free, and the cheapest way to prove a
    key works before spending anything on generation."""
    key = resolve_key(api_key)
    fetch = transport or _get_json
    data = fetch(f"{LIST_ENDPOINT}?key={key}")
    out = []
    for m in data.get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            out.append(m.get("name", "").removeprefix("models/"))
    return sorted(n for n in out if n)


def _get_json(url):
    import json as _json
    import urllib.request
    with urllib.request.urlopen(url, timeout=30) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def _extract_text(data):
    """Pull the model's text out, and say plainly why it is missing if it is.

    A blocked or truncated response is not an empty string — treating it as one
    would send the runtime into a repair retry for a prompt that will never be
    answered, which costs money and fixes nothing.
    """
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback", {})
        blocked = feedback.get("blockReason")
        raise ProviderError(f"no candidates returned (blockReason={blocked!r})"
                            if blocked else "no candidates returned")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise ProviderError(
            f"empty completion (finishReason={cand.get('finishReason')!r})")
    return text


def _usage(data, prompt, text):
    u = data.get("usageMetadata") or {}
    in_tok = u.get("promptTokenCount")
    out_tok = u.get("candidatesTokenCount")
    if in_tok is None:
        in_tok = approx_tokens(prompt)
    if out_tok is None:
        out_tok = approx_tokens(text)
    return int(in_tok), int(out_tok)


def provider(api_key=None, model=MODEL, *, temperature=0.2, max_output_tokens=8192,
             timeout=120, prices=None, transport=http_json, system_instruction=None):
    """Build the dispatch callable `baton.run` needs.

    transport is injectable so the whole provider can be tested offline — the
    test suite must never need a network or a key.
    """
    key = resolve_key(api_key)
    rate_card = prices if prices is not None else PRICES
    url = ENDPOINT.format(model=model) + f"?key={key}"

    def dispatch(agent, baton, prompt):
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                # The routing block is JSON inside prose; leave the model free to
                # write prose. Forcing response_mime_type=application/json here
                # would break the "work, then the routing block" shape the
                # contract asks for.
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        try:
            data = transport(url, payload, timeout=timeout)
            text = _extract_text(data)
        except ProviderError as exc:
            # A transport failure is not a parse failure. Report it as an error
            # so the runtime ends the run instead of burning a repair retry.
            return DispatchResult(text="", error=str(exc))

        in_tok, out_tok = _usage(data, prompt, text)
        return DispatchResult(
            text=text,
            cost_usd=cost_from_tokens(rate_card, model, in_tok, out_tok),
            in_tokens=in_tok, out_tokens=out_tok)

    dispatch.model_id = model
    dispatch.provider_name = "gemini"
    return dispatch
