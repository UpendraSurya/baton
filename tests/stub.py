"""
The scripted 'LLM'. Free, deterministic, offline — and prompt-sensitive, which is
the property that makes the guard-deletion tests in test_anti_vacuity.py mean
something. A stub that ignored the prompt would happily pass while the routing
contract rendered nothing at all.

Responses are drawn from a per-agent list. When a list runs out the LAST entry is
reused, so scripting an infinite ping-pong is one line.
"""
import json
import pathlib
import re

from kernel.baton import ArtifactRef
from kernel.runtime import DispatchResult

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
REAL_PROSE = json.loads((FIXTURES / "real_agent_prose.json").read_text())

_LEGAL_RE = re.compile(r"You may (?:hand|reject) to exactly one of: (.+)")


# --- response builders -------------------------------------------------------

def _block(payload):
    return "```handoff\n" + json.dumps(payload) + "\n```"


def handoff(to, goal="continue the work", rationale="next logical step",
            artifacts=None):
    payload = {"decision": "HANDOFF", "to": to, "goal": goal,
               "rationale": rationale}
    if artifacts:
        payload["artifacts"] = [a.to_dict() if isinstance(a, ArtifactRef) else a
                                for a in artifacts]
    return _block(payload)


def propose_done(summary="every acceptance criterion is met", artifacts=None):
    payload = {"decision": "PROPOSE_DONE", "summary": summary}
    if artifacts:
        payload["artifacts"] = [a.to_dict() if isinstance(a, ArtifactRef) else a
                                for a in artifacts]
    return _block(payload)


def ratify(summary="checked every criterion; ships"):
    return _block({"decision": "RATIFY", "summary": summary})


def reject(to, reason="criterion 1 is not evidenced"):
    return _block({"decision": "REJECT", "to": to, "reason": reason})


def prose(i=0):
    """Real recorded agent output from the ~/unlimited corpus."""
    return REAL_PROSE[i % len(REAL_PROSE)]["text"]


def prose_then(block, i=0):
    """What a real hop looks like: the work, then the routing block."""
    return prose(i) + "\n\n" + block


def garbage(i=0):
    """Real agent output that simply forgot the routing block — the single most
    common way a dynamic run derails."""
    return prose(i)


class _Boom:
    pass


def boom():
    """Marker for a dispatch that failed outright (timeout, rate limit)."""
    return _Boom()


def follow_contract(fallback="senior_vibe_officer"):
    """A well-behaved model: hands to the FIRST target the prompt says is legal.

    If the prompt offers no list — i.e. guard A has been deleted — it invents a
    plausible-sounding agent, which is exactly what real models do."""
    def respond(agent, baton, prompt):
        m = _LEGAL_RE.search(prompt)
        if not m:
            return handoff(fallback, goal="guessing, the prompt named no targets")
        first = m.group(1).split(",")[0].strip()
        return handoff(first, goal="the first target the contract offered")
    return respond


# --- the dispatcher ----------------------------------------------------------

class ScriptedDispatch:
    """script: {agent_name: [response, ...]}. A response is a str, a callable
    (agent, baton, prompt) -> str, or boom()."""

    def __init__(self, script, *, cost_per_call=0.0, default=None):
        self.script = {k: list(v) for k, v in script.items()}
        self.cost_per_call = cost_per_call
        self.default = default
        self.calls = []
        self._cursor = {}

    def _next(self, name):
        seq = self.script.get(name)
        if not seq:
            if self.default is None:
                raise AssertionError(
                    f"the run dispatched {name!r}, which the script does not cover")
            return self.default
        i = self._cursor.get(name, 0)
        self._cursor[name] = i + 1
        return seq[min(i, len(seq) - 1)]        # last entry repeats forever

    def __call__(self, agent, baton, prompt):
        self.calls.append((agent.name, prompt))
        item = self._next(agent.name)
        if isinstance(item, _Boom):
            return DispatchResult(text="", error="dispatch failed: timeout")
        text = item(agent, baton, prompt) if callable(item) else item
        cost = self.cost_per_call
        if isinstance(cost, dict):
            cost = cost.get(agent.name, 0.0)
        return DispatchResult(text=text, cost_usd=cost,
                              in_tokens=len(prompt) // 4, out_tokens=len(text) // 4)

    def prompts_for(self, name):
        return [p for n, p in self.calls if n == name]

    def call_count(self, name=None):
        return len([1 for n, _ in self.calls if name is None or n == name])
