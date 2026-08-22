"""
adapters.company_os.dispatch — the callable the kernel is handed.

The Company OS run_agent() loads the persona itself and prepends it to the brief.
The kernel has already rendered the persona into the prompt, so the adapter takes
it back out; otherwise every real call pays for the persona twice.

The Company OS import is LAZY and lives inside the function. That keeps the free
tier-1 suite runnable on a machine where the fork is not checked out, and it
keeps this module importable by verify.sh's refusal probe.
"""
import os
import pathlib
import sys

from baton.errors import BatonError
from baton.providers.base import cost_from_tokens
from baton.runtime import DispatchResult

from baton.adapters.company_os.registry import repo_path

FORBID = "BATON_FORBID_REAL_DISPATCH"
DEFAULT_MODEL = "claude-sonnet-4-6"

# agent_registry.json stores SHORT aliases ("sonnet"); MODEL_PRICES is keyed by
# full ids ("claude-sonnet-4-6"). Without this mapping every call prices at $0.00
# and the budget ceiling silently stops existing — measured, not theorised: a
# stub-mode probe of a real charter reported spend $0.000000 across 4 dispatches.
ALIAS_TO_MODEL = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}


def resolve_model(name):
    """Short registry alias -> the full id both the rate card and `claude` expect."""
    return ALIAS_TO_MODEL.get(name, name)


def strip_persona(prompt, agent):
    """Remove the leading persona block the kernel rendered. No-op if absent."""
    persona = (getattr(agent, "instructions", "") or "").strip()
    if persona and prompt.lstrip().startswith(persona):
        return prompt.lstrip()[len(persona):].lstrip()
    return prompt


class HostUnavailable(BatonError):
    """The Company OS checkout this adapter binds is not on this machine."""


def _add_repo_to_path():
    repo = repo_path()
    # This adapter ships in the wheel as a worked example of binding a host, so
    # most installed users will not have the checkout. Say so here, in one line,
    # instead of letting `from core.contracts import ...` fail three frames down
    # with a ModuleNotFoundError that names neither this adapter nor the fix.
    if not (repo / "engine").is_dir():
        raise HostUnavailable(
            f"Company OS is not at {repo}. baton.adapters.company_os is a "
            "worked example of binding a host application, not a general "
            "adapter — it needs that checkout on disk. Point it elsewhere with "
            "BATON_COMPANY_OS_REPO=/path/to/company-os, or use "
            "baton.providers.gemini / .mistral to talk to a model directly.")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    return repo


def provider(*, stub=False, model_id=None, repo_dir=None):
    """Build the dispatch callable baton.run wants, mirroring baton.providers.*.

    stub=True uses Company OS's own canned-output mode: it loads the real persona
    and exercises this entire seam WITHOUT a model call, so the integration can be
    proven for $0 before a cent is spent on it.
    """
    def dispatch(agent, baton, prompt):
        return real_dispatch(agent, baton, prompt, model_id=model_id,
                             repo_dir=repo_dir, stub=stub)
    dispatch.provider_name = "company_os"
    return dispatch


def real_dispatch(agent, baton, prompt, *, model_id=None, repo_dir=None, stub=False):
    """Run one agent. stub=True is free; stub=False costs money."""
    if not stub and os.environ.get(FORBID):
        raise RuntimeError(
            f"{FORBID} is set — this process is running under the free tier-1 "
            "gate and must not make a real model call")

    _add_repo_to_path()
    from core.contracts import NodeInput            # noqa: E402  (lazy on purpose)
    from engine import dispatch as engine_dispatch  # noqa: E402

    model = resolve_model(model_id or agent.model_id or DEFAULT_MODEL)
    node_input = NodeInput(project_id=baton.trace_id, node=agent.name,
                           brief=strip_persona(prompt, agent), model_id=model,
                           acceptance_criteria=list(baton.open_questions))
    try:
        out = engine_dispatch.run_agent(agent.name, {"model_id": model},
                                        node_input, stub=stub,
                                        repo_dir=repo_dir)
    except Exception as exc:                        # transport, timeout, rate limit
        return DispatchResult(text="", error=f"{type(exc).__name__}: {exc}")

    text = deliverable_text(out)
    failed = getattr(out.status, "value", str(out.status)) == "failed"
    return DispatchResult(text=text,
                          cost_usd=cost_usd(
                              model, out.in_tokens, out.out_tokens,
                              cache_read=getattr(out, "cache_read_tokens", 0),
                              cache_write=getattr(out, "cache_write_tokens", 0)),
                          in_tokens=out.in_tokens, out_tokens=out.out_tokens,
                          model_id=model,   # RESOLVED, not the registry alias
                          error=out.notes if failed else "")



def deliverable_text(out):
    """The agent's FULL output, not Company OS's digest of it.

    run_agent writes the whole response to `artifact.ref` and puts an
    `_extract_summary()` digest — 200 characters when the agent wrote no
    `SUMMARY:` line — in `artifact.summary`. That digest is the right thing for
    Company OS's own DAG, where the next node wants a precis. It is the wrong
    thing here: the handoff block is the LAST thing an agent writes, so the
    digest truncates the routing decision away every single time.

    Measured on the first live run of this seam (2026-08-22): 4051 output
    tokens produced, 213 characters handed to the kernel, "no fenced handoff
    block found" on every attempt, `charter_violation` after 181s and $0.59 of
    metered spend. Stub mode could never have caught it — a stub returns a
    placeholder with no handoff block either, so the failure looked identical
    to the one you would expect from a stub.

    Fallbacks are ordered by how much is known: the file, then the digest, then
    the notes. A degraded hop is worth more than a dispatch failure.
    """
    artifact = getattr(out, "artifact", None)
    if artifact is not None:
        # Artifact.ref is "path / git ref". Only a path is worth opening, and a
        # git ref has no separator in it.
        ref = (getattr(artifact, "ref", "") or "").strip()
        if ref and os.sep in ref:
            try:
                full = pathlib.Path(ref).read_text(encoding="utf-8").strip()
            except OSError:
                full = ""            # host cleaned the workspace, or wrong machine
            if full:                 # a zero-byte deliverable is a failed write
                return full
        summary = (getattr(artifact, "summary", "") or "").strip()
        if summary:
            return summary
    return (getattr(out, "notes", "") or "").strip()


# Anthropic bills a cached read at ~0.1x the input rate and a cache WRITE at
# ~1.25x. Pricing all three counters at 1.0x is what made the first real shadow
# pair halt at budget_exhausted on hop one: a repo-reading agent carries a huge
# cached prompt, and at full rate that alone cleared a $2 ceiling.
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25


def cost_usd(model_id, in_tokens, out_tokens, *, cache_read=0, cache_write=0):
    """Company OS's rate card, priced through BATON's meter.

    `in_tokens` is the TRUE total input, cache included — that is what belongs
    in a ledger. Pricing then splits it: the cached portion is subtracted out
    and re-priced at its own rate, so the cache is never double counted.

    Deliberately not config.cost_usd(), whose contract is "unknown models cost 0
    (logged, never crash)". A model that prices at zero never trips the ceiling,
    so the run has no cost bound at all. cost_from_tokens raises UnmeteredModel
    instead — loud beats free.
    """
    _add_repo_to_path()
    from core import config                         # noqa: E402
    model = resolve_model(model_id)
    cached = max(0, int(cache_read)) + max(0, int(cache_write))
    uncached = max(0, int(in_tokens) - cached)
    # Priced as three separate calls against the same card, so the arithmetic
    # lives in exactly one place (cost_from_tokens) and stays per-million-safe.
    return (cost_from_tokens(config.MODEL_PRICES, model, uncached, out_tokens)
            + cost_from_tokens(config.MODEL_PRICES, model,
                               round(cache_read * CACHE_READ_RATE), 0)
            + cost_from_tokens(config.MODEL_PRICES, model,
                               round(cache_write * CACHE_WRITE_RATE), 0))
