"""
kernel.contract — turning free text into a route, and back.

An LLM returns prose. Everything about making that a routing decision lives here:
what the agent is told it may do (render), what we pull back out (parse), and
what we refuse (validate).

Two guards deliberately overlap: the legal moves are rendered INTO the prompt so
hallucinated names are rare, and validated AFTER so they are impossible. That is
the overlapping-guards pattern that has produced vacuous tests before, so both
guards are individually switchable — `include_legal_moves` and `enforce_target` —
and tests/test_anti_vacuity.py deletes each one alone to prove it is load-bearing.
"""
import json
import re

from kernel.baton import ArtifactRef, Decision, Kind
from kernel.errors import IllegalTarget, ParseFailure, RoleViolation

PRESSURE_LINE = ("> **Budget pressure:** this run is near its ceiling — prefer "
                 "completing the work over delegating it further.")

WORKER_VERBS = (Kind.HANDOFF, Kind.PROPOSE_DONE)
GATE_VERBS = (Kind.RATIFY, Kind.REJECT)

_FENCED = re.compile(r"```(?:handoff|json)?[ \t]*\r?\n(.*?)```", re.S | re.I)
_BARE_OBJECT = re.compile(r"\{.*\}", re.S)
_SMART = {"“": '"', "”": '"', "‘": "'", "’": "'"}
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


# --- rendering ---------------------------------------------------------------

def render_routing_contract(agent, charter, *, include_legal_moves=True):
    """The block appended to every prompt. Generated from THIS agent's whitelist.

    include_legal_moves=False removes guard A (the rendered whitelist) and exists
    so the anti-vacuity suite can measure what that guard is worth on its own.
    """
    out = ["## Routing contract — you MUST end your output with one fenced block", ""]
    if agent.is_gate:
        out += [
            "You are the GATE. You are the only role that can end this run.",
            "Judge the work against the acceptance criteria above — nothing else.",
            "",
            "```handoff",
            '{"decision": "RATIFY", "summary": "why every criterion is met"}',
            "```",
            "",
            "or, to send the work back:",
            "",
            "```handoff",
            '{"decision": "REJECT", "to": "<agent>", "reason": "what is missing"}',
            "```",
        ]
        if include_legal_moves:
            moves = charter.legal_moves_for(agent)
            out += ["", "You may reject to exactly one of: "
                        + (", ".join(moves) or "(nobody)")]
        return "\n".join(out)

    if include_legal_moves:
        moves = charter.legal_moves_for(agent)
        out += ["You may hand to exactly one of: " + (", ".join(moves) or "(nobody)"),
                ""]
    out += [
        "```handoff",
        '{"decision": "HANDOFF", "to": "<agent>",',
        ' "goal": "what the receiver must achieve",',
        ' "rationale": "why them, why now",',
        ' "artifacts": [{"path": "workspace/spec.md", "description": "one line",',
        '                "preview": "your own 2-3 line summary of the contents"}]}',
        "```",
        "",
        "or, if you believe the brief is satisfied:",
        "",
        "```handoff",
        '{"decision": "PROPOSE_DONE", "summary": "how each criterion is met",',
        ' "artifacts": [{"path": "...", "description": "...", "preview": "..."}]}',
        "```",
        "",
        "You cannot end the run yourself. PROPOSE_DONE sends the work to the gate.",
    ]
    return "\n".join(out)


def render_prompt(agent, baton, charter, *, pressure=False,
                  include_legal_moves=True, nudge=""):
    """persona -> baton -> charter bounds -> routing contract -> (nudge).

    Never the conversation history: artifact paths and previews travel, contents
    do not. That is what keeps per-hop cost flat instead of quadratic.
    """
    parts = [agent.instructions.strip(), "", baton.render(), "",
             "## Run constraints", "",
             "**Acceptance criteria** (the gate judges against exactly these):"]
    parts += [f"- {c}" for c in charter.acceptance_criteria]
    parts += ["", f"Security tier: {charter.security_tier}. "
                  f"Hop {baton.hop} of at most {charter.max_hops}."]
    if pressure:
        parts += ["", PRESSURE_LINE]
    parts += ["", render_routing_contract(agent, charter,
                                          include_legal_moves=include_legal_moves)]
    if nudge:
        parts += ["", nudge]
    return "\n".join(parts)


def repair_nudge(reason, agent, charter):
    """One terse correction. Nothing else is re-sent — a full re-render doubles
    the cost of a hop that has already failed once."""
    moves = ", ".join(charter.legal_moves_for(agent)) or "(nobody)"
    return ("## Your last output could not be routed\n\n"
            f"Reason: {reason}\n\n"
            f"Reply with ONLY the fenced ```handoff block. Legal targets: {moves}")


# --- parsing -----------------------------------------------------------------

def _clean(raw):
    for bad, good in _SMART.items():
        raw = raw.replace(bad, good)
    return _TRAILING_COMMA.sub(r"\1", raw).strip()


def _candidates(text):
    """Every plausible decision payload, in document order. Fenced blocks first;
    a bare trailing object only if no fence produced anything."""
    found = [m.group(1) for m in _FENCED.finditer(text)]
    if found:
        return found
    m = _BARE_OBJECT.search(text)
    return [m.group(0)] if m else []


def _artifacts(raw):
    out = []
    for item in raw or ():
        if isinstance(item, dict):
            out.append(ArtifactRef(path=str(item.get("path", "")).strip(),
                                   description=str(item.get("description", "")).strip(),
                                   preview=str(item.get("preview", "")).strip()))
        elif isinstance(item, str):
            # the design note's own example shows "path — description"; models copy it
            path, sep, desc = item.partition("—")
            if not sep:
                path, sep, desc = item.partition(" - ")
            out.append(ArtifactRef(path=path.strip(), description=desc.strip()))
    return tuple(out)


def parse_decision(text):
    """The LAST parseable block wins — agents show their working, then commit."""
    if not text or not text.strip():
        raise ParseFailure("agent produced no output")
    last_error = "no fenced handoff block found"
    for raw in reversed(_candidates(text)):
        try:
            payload = json.loads(_clean(raw))
        except (ValueError, TypeError) as exc:
            last_error = f"block was not valid JSON ({exc})"
            continue
        if not isinstance(payload, dict) or "decision" not in payload:
            last_error = "block has no 'decision' field"
            continue
        verb = str(payload["decision"]).strip().upper()
        try:
            kind = Kind(verb)
        except ValueError:
            raise ParseFailure(
                f"{verb!r} is not a verb — use HANDOFF, PROPOSE_DONE, RATIFY or REJECT")
        return Decision(kind=kind,
                        to=str(payload.get("to", "")).strip(),
                        goal=str(payload.get("goal", "")).strip(),
                        rationale=str(payload.get("rationale", "")).strip(),
                        summary=str(payload.get("summary", "")).strip(),
                        reason=str(payload.get("reason", "")).strip(),
                        artifacts=_artifacts(payload.get("artifacts")))
    raise ParseFailure(last_error)


# --- validating --------------------------------------------------------------

def validate_decision(decision, agent, charter, *, enforce_target=True):
    """Return the decision, or raise. enforce_target=False removes guard B (the
    post-parse whitelist check) and exists so the anti-vacuity suite can measure
    it alone. It never disables the ROLE check — that is a different guard, and
    letting one switch turn off two is how guards start covering for each other.
    """
    allowed = GATE_VERBS if agent.is_gate else WORKER_VERBS
    if decision.kind not in allowed:
        raise RoleViolation(
            f"{agent.name} has role {agent.role!r} and may only use "
            f"{', '.join(k.value for k in allowed)}; it used {decision.kind.value}")

    if decision.kind is Kind.HANDOFF and not decision.goal.strip():
        raise IllegalTarget("HANDOFF without a goal is a dropped baton")

    if decision.kind in (Kind.HANDOFF, Kind.REJECT) and enforce_target:
        moves = charter.legal_moves_for(agent)
        if decision.to not in moves:
            raise IllegalTarget(
                f"{decision.to!r} is not reachable from {agent.name}; "
                f"legal targets are: {', '.join(moves) or '(nobody)'}")
    return decision
