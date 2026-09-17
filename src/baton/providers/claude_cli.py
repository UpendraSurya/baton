"""
baton.providers.claude_cli — dispatch through the Claude Code CLI subscription,
not a metered API.

Every other provider in this package posts to a paid /v1/chat/completions
endpoint and meters cost in real dollars. This one shells out to `claude -p`
(Claude Code headless) and rides whatever subscription is already logged in on
this machine — the same mechanism Company OS's `engine/dispatch.py` uses to run
its 42 agents. There is no per-token dollar bill, so `cost_usd` is honestly
reported as 0.0 — but that is NOT the same as "free with no cost implication":
every call still consumes real subscription usage/session quota. Callers that
treat a 0.0-cost ledger as literally costless are reading it wrong.

    from baton.providers import claude_cli
    dispatch = claude_cli.provider(model="sonnet")
    result = run(charter, agents, dispatch)

No SDK, no `requests` — `subprocess` and the `claude` binary already on PATH.
tests/test_isolation.py still passes: this module imports nothing outside the
standard library.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Callable

from baton.providers.base import FORBID_ENV, ProviderError, approx_tokens
from baton.runtime import Dispatch, DispatchResult

if TYPE_CHECKING:
    from baton.agent import AgentSpec
    from baton.packet import Baton

DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT = 600.0  # matches Company OS's DISPATCH_TIMEOUT_S — a real
                         # agent turn, not an HTTP call, can legitimately take
                         # minutes.

# A `claude -p` call that hit the subscription's own session/usage limit
# returns a short notice as if it were a completed answer, not an HTTP error.
# Treating that text as a real deliverable would let a quota wall masquerade as
# a routing outcome — exactly the failure mode bench/run.py's own docstring
# warns about for every other provider. Bounded by length so a real deliverable
# that merely *discusses* rate limiting is never misflagged.
_RATE_LIMIT_MARKERS = ("hit your session limit", "hit your usage limit",
                       "session limit · resets", "usage limit reached",
                       "approaching your usage limit")


def _is_rate_limited(text: str) -> bool:
    if not text:
        return False
    low = text.strip().lower()
    if len(low) > 600:
        return False
    return any(m in low for m in _RATE_LIMIT_MARKERS)


def _parse_claude_json(stdout: str, prompt: str) -> tuple[str, int, int]:
    """Pull (text, in_tokens, out_tokens) from `claude --output-format json`.
    Falls back to raw text + a chars/4 estimate if the shape is unexpected —
    the CLI's JSON envelope is not a documented, versioned contract."""
    est_in = approx_tokens(prompt)
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        text = stdout.strip()
        return text, est_in, approx_tokens(text)
    text = data.get("result") or data.get("text") or ""
    usage = data.get("usage") or {}
    in_tok = usage.get("input_tokens") or usage.get("prompt_tokens") or est_in
    out_tok = usage.get("output_tokens") or approx_tokens(text)
    return text, int(in_tok), int(out_tok)


def provider(model: str = DEFAULT_MODEL, *, claude_bin: str | None = None,
            timeout: float = DEFAULT_TIMEOUT,
            runner: Callable[[list[str], float], subprocess.CompletedProcess]
            | None = None) -> Dispatch:
    """Build the dispatch callable `baton.run` needs.

    `runner` is injectable so the whole provider can be tested offline — the
    test suite must never shell out to a real `claude` binary, same discipline
    every other provider's `transport=` gives it.
    """
    exe = claude_bin or shutil.which("claude") or os.path.expanduser(
        "~/.local/bin/claude")

    def _run(argv: list[str], _timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=_timeout)

    call = runner or _run

    def dispatch(agent: "AgentSpec", baton: "Baton", prompt: str) -> DispatchResult:
        # Same one real network/process path every other provider guards —
        # BATON_FORBID_REAL_DISPATCH must block this too, even though it never
        # touches http_json. A gate that forgets this path spends subscription
        # quota it promised never to spend.
        if os.environ.get(FORBID_ENV):
            return DispatchResult(
                error=f"{FORBID_ENV} is set — this process must not shell out "
                      "to a real claude -p call")

        argv = [exe, "-p", prompt, "--model", model, "--output-format", "json"]
        try:
            proc = call(argv, timeout)
        except FileNotFoundError:
            return DispatchResult(error=f"claude CLI not found at {exe!r}")
        except subprocess.TimeoutExpired:
            return DispatchResult(error=f"claude -p timed out after {timeout}s")

        text, in_tok, out_tok = _parse_claude_json(proc.stdout, prompt)

        if proc.returncode != 0 and not text:
            return DispatchResult(
                error=f"claude -p exit {proc.returncode}: "
                      f"{(proc.stderr or '')[:300]}")

        if _is_rate_limited(text) or _is_rate_limited(proc.stderr or ""):
            return DispatchResult(
                error=f"subscription rate/session limit: {text.strip()[:200]}")

        if not text.strip():
            return DispatchResult(error="empty completion from claude -p")

        # Honest 0.0 — see module docstring. This is the one provider in the
        # package where "no dollar cost" is true and "no cost" is not.
        return DispatchResult(text=text, cost_usd=0.0, in_tokens=in_tok,
                              out_tokens=out_tok, model_id=model)

    dispatch.model_id = model
    dispatch.provider_name = "claude_cli"
    return dispatch
