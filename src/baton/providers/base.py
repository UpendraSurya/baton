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
import json
import os
import time
import urllib.error
import urllib.request

from baton.errors import BatonError

# Set this and NOTHING in this library can reach a paid endpoint. Checked here,
# at the one real network path, rather than per provider — a guard every author
# has to remember to add is a guard that eventually gets forgotten. Injected
# transports in tests never come through here, so the suite is unaffected.
FORBID_ENV = "BATON_FORBID_REAL_DISPATCH"

# Bounded, like every loop in this library: three attempts, then give up and let
# the runtime record a dispatch_failure. An unbounded retry on a 429 is how a
# "cheap" test run turns into an afternoon of silent backoff.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 4.0)
RETRY_STATUSES = (408, 429, 500, 502, 503, 504)


class ProviderError(BatonError):
    """The provider could not be reached, or answered with something unusable."""


def http_json(url, payload, headers=None, timeout=120, _sleep=time.sleep):
    """POST json, get json back. Retries only what is worth retrying.

    Returns the decoded response dict. Raises ProviderError on a non-retryable
    status, on exhausted retries, or on a body that is not JSON.
    """
    if os.environ.get(FORBID_ENV):
        raise ProviderError(
            f"{FORBID_ENV} is set — this process must not make a paid model call")

    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})

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


def cost_from_tokens(prices, model, in_tokens, out_tokens):
    """Rate cards are quoted per MILLION tokens. Divide by 1e6 exactly once.

    This function exists as one place to get that arithmetic right, because
    getting it wrong is silent: an undercounting cost meter does not error, it
    just quietly disables the budget ceiling that is supposed to protect you.
    """
    rate = prices.get(model)
    if rate is None:
        return 0.0
    return (in_tokens * rate["in"] + out_tokens * rate["out"]) / 1_000_000


def approx_tokens(text):
    """A rough count for providers that do not report usage. ~4 chars per token.

    Only ever a fallback. Anything that reports real usage must use the real
    numbers — the budget ceiling is only as honest as this figure.
    """
    return max(1, len(text or "") // 4)
