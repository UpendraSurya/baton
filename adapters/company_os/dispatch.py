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
import sys

from kernel.runtime import DispatchResult

from adapters.company_os.registry import repo_path

FORBID = "BATON_FORBID_REAL_DISPATCH"
DEFAULT_MODEL = "claude-sonnet-4-6"


def strip_persona(prompt, agent):
    """Remove the leading persona block the kernel rendered. No-op if absent."""
    persona = (getattr(agent, "instructions", "") or "").strip()
    if persona and prompt.lstrip().startswith(persona):
        return prompt.lstrip()[len(persona):].lstrip()
    return prompt


def _add_repo_to_path():
    repo = repo_path()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    return repo


def real_dispatch(agent, baton, prompt, *, model_id=None, repo_dir=None):
    """Run one agent for real. Costs money."""
    if os.environ.get(FORBID):
        raise RuntimeError(
            f"{FORBID} is set — this process is running under the free tier-1 "
            "gate and must not make a real model call")

    _add_repo_to_path()
    from core.contracts import NodeInput            # noqa: E402  (lazy on purpose)
    from engine import dispatch as engine_dispatch  # noqa: E402

    model = model_id or agent.model_id or DEFAULT_MODEL
    node_input = NodeInput(project_id=baton.trace_id, node=agent.name,
                           brief=strip_persona(prompt, agent), model_id=model,
                           acceptance_criteria=list(baton.open_questions))
    try:
        out = engine_dispatch.run_agent(agent.name, {"model_id": model},
                                        node_input, stub=False,
                                        repo_dir=repo_dir)
    except Exception as exc:                        # transport, timeout, rate limit
        return DispatchResult(text="", error=f"{type(exc).__name__}: {exc}")

    text = (out.artifact.summary if out.artifact else "") or out.notes or ""
    failed = getattr(out.status, "value", str(out.status)) == "failed"
    return DispatchResult(text=text,
                          cost_usd=cost_usd(model, out.in_tokens, out.out_tokens),
                          in_tokens=out.in_tokens, out_tokens=out.out_tokens,
                          error=out.notes if failed else "")


def cost_usd(model_id, in_tokens, out_tokens):
    """Company OS's own rate card, so baton costs are comparable to recorded runs."""
    _add_repo_to_path()
    from core import config                         # noqa: E402
    return config.cost_usd(model_id, in_tokens, out_tokens)
