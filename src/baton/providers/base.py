"""
baton.providers.base — shared plumbing for every provider.

Providers speak HTTP through `urllib` from the standard library. They never import
a vendor SDK, because `pip install baton-kernel` pulling in nothing is the product
promise, and tests/test_isolation.py fails the build if that changes.

A provider is a factory: it takes credentials and settings, and returns the single
`dispatch(agent, baton, prompt) -> DispatchResult` callable that `baton.run` wants.
Everything vendor-specific — URL shape, payload shape, where the token counts live,
the rate card — is confined to one small module per vendor.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request

from baton.errors import BatonError

# Set this and NOTHING in this library can reach a paid endpoint. Checked here,
# at the one real network path, rather than per provider — a guard every author
# has to remember to add is a guard that eventually gets forgotten. Injected
# transports in tests never come through here, so the suite is unaffected.
FORBID_ENV: str = "BATON_FORBID_REAL_DISPATCH"

# Bounded, like every loop in this library: three attempts, then give up and let
# the runtime record a dispatch_failure. An unbounded retry on a 429 is how a
# "cheap" test run turns into an afternoon of silent backoff.
MAX_ATTEMPTS: int = 3
BACKOFF_SECONDS = (1.0, 4.0)
RETRY_STATUSES = (408, 429, 500, 502, 503, 504)

def _user_agent() -> str:
    from baton import __version__
    return f"baton-kernel/{__version__} (+https://pypi.org/project/baton-kernel)"


USER_AGENT = _user_agent()


class ProviderError(BatonError):
    """The provider could not be reached, or answered with something unusable."""


def http_json(url: str, payload: dict[str, Any],
              headers: Mapping[str, str] | None = None, timeout: float = 120,
              _sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """POST json, get json back. Retries only what is worth retrying.

    Returns the decoded response dict. Raises ProviderError on a non-retryable
    status, on exhausted retries, or on a body that is not JSON.
    """
    if os.environ.get(FORBID_ENV):
        raise ProviderError(
            f"{FORBID_ENV} is set — this process must not make a paid model call")

    body = json.dumps(payload).encode("utf-8")
    # A User-Agent is not politeness, it is reachability. urllib sends
    # `Python-urllib/3.x` by default, and that string is on enough blocklists
    # that a vendor behind Cloudflare rejects the request with 403 code 1010
    # before it reaches their API — which is exactly how Groq presented as
    # broken until a consumer traced it. A library whose whole transport story
    # is "stdlib urllib, no SDK" has to identify itself, or "no dependencies"
    # quietly means "does not work with some vendors".
    hdrs = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    hdrs.update(headers or {})          # a host application may override it

    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except ValueError as exc:
                raise ProviderError(f"response was not JSON: {exc}: {raw[:200]}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {exc.code}: {detail}"
            if exc.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                raise ProviderError(last)
        except urllib.error.URLError as exc:
            last = f"network error: {exc.reason}"
            if attempt == MAX_ATTEMPTS:
                raise ProviderError(last)
        except TimeoutError:
            last = f"timed out after {timeout}s"
            if attempt == MAX_ATTEMPTS:
                raise ProviderError(last)
        _sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])

    raise ProviderError(last or "exhausted retries")


class UnmeteredModel(ProviderError):
    """No rate card for this model, so cost cannot be measured.

    Raised rather than returning 0.0. A model priced at zero looks free to the
    budget ceiling, so the ceiling never trips and the run has no cost bound at
    all — a silent failure that only shows up on the bill.
    """


def cost_from_tokens(prices: Mapping[str, Mapping[str, float]], model: str,
                     in_tokens: int, out_tokens: int) -> float:
    """Rate cards are quoted per MILLION tokens. Divide by 1e6 exactly once.

    One place to get that arithmetic right, because getting it wrong is silent:
    an undercounting cost meter does not error, it just quietly disables the
    budget ceiling that is supposed to protect you.
    """
    rate = prices.get(model)
    if rate is None:
        raise UnmeteredModel(
            f"no price for {model!r}: pass prices={{{model!r}: "
            "{'in': <usd per 1M in>, 'out': <usd per 1M out>}} — without a rate "
            "card the budget ceiling cannot bound this run")
    return (in_tokens * rate["in"] + out_tokens * rate["out"]) / 1_000_000


def approx_tokens(text: str | None) -> int:
    """A rough count for providers that do not report usage. ~4 chars per token.

    Only ever a fallback. Anything that reports real usage must use the real
    numbers — the budget ceiling is only as honest as this figure.
    """
    return max(1, len(text or "") // 4)
