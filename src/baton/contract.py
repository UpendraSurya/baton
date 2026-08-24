"""
baton.contract — turning free text into a route, and back.

An LLM returns prose. Everything about making that a routing decision lives here:
what the agent is told it may do (render), what we pull back out (parse), and
what we refuse (validate).

Two guards deliberately overlap: the legal moves are rendered INTO the prompt so
hallucinated names are rare, and validated AFTER so they are impossible. That is
the overlapping-guards pattern that has produced vacuous tests before, so both
guards are individually switchable — `include_legal_moves` and `enforce_target` —
and tests/test_anti_vacuity.py deletes each one alone to prove it is load-bearing.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Sequence

from baton.packet import ArtifactRef, Baton, Decision, Kind
from baton.errors import IllegalTarget, ParseFailure, RoleViolation

if TYPE_CHECKING:
    from baton.agent import AgentSpec
    from baton.charter import Charter

PRESSURE_LINE: str = ("> **Budget pressure:** this run is near its ceiling — prefer "
                 "completing the work over delegating it further.")

WORKER_VERBS = (Kind.HANDOFF, Kind.PROPOSE_DONE)
GATE_VERBS = (Kind.RATIFY, Kind.REJECT)

_FENCED = re.compile(r"```(?:handoff|json)?[ \t]*\r?\n(.*?)```", re.S | re.I)
_BARE_OBJECT = re.compile(r"\{.*\}", re.S)
_SMART = {"“": '"', "”": '"', "‘": "'", "’": "'"}
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


# --- rendering ---------------------------------------------------------------

def render_routing_contract(agent: AgentSpec, charter: Charter, *,
                            include_legal_moves: bool = True) -> str:
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
            # Every placeholder stays INSIDE quotes. The first version of this
            # example wrote `"coverage": [<one path per criterion>]`, which is
            # not valid JSON — and models copied it back verbatim, so the gate
            # failed to route on 67% of dispatches with Claude and 75% with
            # mistral-small. A worked example in a prompt is INSTRUCTION: if the
            # example does not parse, neither will the answer.
            '{"decision": "RATIFY", "summary": "why every criterion is met",\n'
            ' "coverage": ["<artifact path satisfying criterion 1>",\n'
            '              "<artifact path satisfying criterion 2>"]}',
            "```",
            "",
            "`coverage` needs ONE entry per acceptance criterion above, in the "
            "same order, each an artifact path that already exists. Repeat a "
            "path if it satisfies more than one. You cannot ratify work you "
            "cannot point at.",
            "",
            # Without this line the only way to express a gap is a short array,
            # which loses WHICH criterion is unanswered the moment the gap is
            # anywhere but the end.
            "If nothing satisfies one of them, put `null` in that position and "
            "keep the rest in place. Do NOT shorten the list or shift entries "
            "up — position is how a criterion is identified.",
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
        ' "artifacts": [{"path": "<path or label for what you produced>",',
        '                "description": "one line",',
        '                "preview": "your own 2-3 line summary",',
        # One line. The two-line version put a RAW NEWLINE inside a JSON string,
        # so the example workers were shown had never been parseable — and the
        # runtime then counted their copies of it as the model's failure.
        '                "content": "the work product itself, when the reader cannot open the path"}]}',
        "```",
        "",
        "or, if you believe the brief is satisfied:",
        "",
        "```handoff",
        '{"decision": "PROPOSE_DONE", "summary": "how each criterion is met",',
        ' "artifacts": [{"path": "<what you produced>", "description": "...",',
        '                "preview": "...", "content": "the deliverable itself"}]}',
        "```",
        "",
        "PROPOSE_DONE REQUIRES at least one artifact carrying the actual work.",
        "Do not cite a path you did not write. If you have no file system, put",
        "the deliverable in `content` — the gate can only judge what it can see.",
        "",
        "You cannot end the run yourself. PROPOSE_DONE sends the work to the gate.",
    ]
    return "\n".join(out)


def render_prompt(agent: AgentSpec, baton: Baton, charter: Charter, *,
                  pressure: bool = False, include_legal_moves: bool = True,
                  nudge: str = "") -> str:
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


def repair_nudge(reason: str, agent: AgentSpec, charter: Charter) -> str:
    """One terse correction. Nothing else is re-sent — a full re-render doubles
    the cost of a hop that has already failed once."""
    moves = ", ".join(charter.legal_moves_for(agent)) or "(nobody)"
    return ("## Your last output could not be routed\n\n"
            f"Reason: {reason}\n\n"
            f"Reply with ONLY the fenced ```handoff block. Legal targets: {moves}")


# --- parsing -----------------------------------------------------------------

def _clean(raw: str) -> str:
    for bad, good in _SMART.items():
        raw = raw.replace(bad, good)
    return _TRAILING_COMMA.sub(r"\1", raw).strip()


def _candidates(text: str) -> list[str]:
    """Every plausible decision payload, in document order. Fenced blocks first;
    a bare trailing object only if no fence produced anything."""
    found = [m.group(1) for m in _FENCED.finditer(text)]
    if found:
        return found
    m = _BARE_OBJECT.search(text)
    return [m.group(0)] if m else []


def _artifacts(raw: Sequence[Any] | None) -> tuple[ArtifactRef, ...]:
    out = []
    for item in raw or ():
        if isinstance(item, dict):
            out.append(ArtifactRef(path=str(item.get("path", "")).strip(),
                                   description=str(item.get("description", "")).strip(),
                                   preview=str(item.get("preview", "")).strip(),
                                   content=str(item.get("content", ""))))
        elif isinstance(item, str):
            # the design note's own example shows "path — description"; models copy it
            path, sep, desc = item.partition("—")
            if not sep:
                path, sep, desc = item.partition(" - ")
            out.append(ArtifactRef(path=path.strip(), description=desc.strip()))
    return tuple(out)


def parse_decision(text: str) -> Decision:
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
                        # Blank entries are KEPT. Coverage is positional, so
                        # dropping a hole shifts every later entry up by one and
                        # the array goes on looking plausible while citing the
                        # wrong artifact for every criterion past the gap. `null`
                        # and "" both mean "nothing answers this one".
                        coverage=tuple(
                            "" if c is None else str(c).strip()
                            for c in (payload.get("coverage") or [])),
                        reason=str(payload.get("reason", "")).strip(),
                        artifacts=_artifacts(payload.get("artifacts")))
    raise ParseFailure(last_error)


# --- validating --------------------------------------------------------------

def validate_decision(decision: Decision, agent: AgentSpec, charter: Charter,
                      *, enforce_target: bool = True) -> Decision:
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

    if decision.kind is Kind.PROPOSE_DONE and not decision.artifacts:
        # Observed live: a worker proposed done with no artifact at all, and the
        # gate ratified its *claim* about a deliverable that existed nowhere.
        # A proposal with no evidence attached is unverifiable by construction.
        raise IllegalTarget(
            "PROPOSE_DONE with no artifacts: attach the work product itself "
            "(path + content, or path + preview if the reader can open it) — "
            "the gate cannot ratify a claim it has no way to check")

    if decision.kind in (Kind.HANDOFF, Kind.REJECT) and enforce_target:
        moves = charter.legal_moves_for(agent)
        if decision.to not in moves:
            raise IllegalTarget(
                f"{decision.to!r} is not reachable from {agent.name}; "
                f"legal targets are: {', '.join(moves) or '(nobody)'}")
    return decision
