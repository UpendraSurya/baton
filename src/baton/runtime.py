"""
baton.runtime — the loop.

call dispatch -> parse the decision -> enforce the charter -> append the trace ->
repeat or terminate. Every run ends with exactly one reason from TERMINAL_REASONS;
there is no path out of here that returns "it just stopped".

The dispatch seam is one callable: dispatch(agent, baton, prompt) -> DispatchResult.
That is the entire contract between this kernel and whatever actually runs a model,
and it is why kernel/ imports nothing.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Protocol

from baton.agent import AgentSpec
from baton.charter import Charter
from baton.packet import ArtifactRef, Baton, Kind
from baton.contract import (parse_decision, render_prompt, repair_nudge,
                             validate_decision)
from baton.errors import (BatonError, CharterInvalid, IllegalTarget,
                           ParseFailure, RoleViolation)
from baton.trace import MemoryTrace, TraceSink

TERMINAL_REASONS: tuple[str, ...] = ("ratified", "budget_exhausted", "hops_exhausted", "stalled",
                    "dispatch_failure", "charter_violation", "reject_cap_reached",
                    "ratified_without_deliverable")

# A run that reaches this has lost a stop rule. It is not a stop rule itself — it
# is the alarm that says one is missing, and it raises rather than terminating so
# a deleted guard shows up as a loud failure instead of a plausible result.
_ABSOLUTE_MAX_HOPS: int = 200


class Dispatch(Protocol):
    """The entire contract between baton and whatever runs a model.

    Implement this — or just pass a plain function with this signature — and the
    runtime can drive any model, framework or remote service. It is why this
    package has no dependencies.
    """

    def __call__(self, agent: "AgentSpec", baton: Baton,
                 prompt: str) -> "DispatchResult": ...


@dataclass
class DispatchResult:
    """What one agent execution produced. `error` non-empty ends the run."""

    text: str = ""
    cost_usd: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0
    error: str = ""


@dataclass(frozen=True)
class Guards:
    """Every defence, individually switchable.

    Not test scaffolding: two of these guards deliberately overlap (the whitelist
    is rendered into the prompt AND validated after it), and overlapping guards
    cover for each other until you delete them one at a time. Being able to do
    that is a permanent capability of this kernel — see tests/test_anti_vacuity.py.
    """
    hops: bool = True
    budget: bool = True
    cycle: bool = True
    reject_cap: bool = True
    validate_target: bool = True
    render_legal_moves: bool = True
    require_artifacts: bool = True


@dataclass
class RunResult:
    """The outcome of a run. `terminal_reason` is always one of TERMINAL_REASONS."""

    trace_id: str
    terminal_reason: str
    hops: int
    spend_usd: float
    gate_summary: str = ""
    note: str = ""
    path: tuple[str, ...] = ()
    last_baton: Optional[Baton] = None
    trace: Optional[TraceSink] = None


@dataclass
class _State:
    hop: int = 0
    spend: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0
    pressure: bool = False
    path: list[str] = field(default_factory=list)
    pairs: list[tuple[str, str]] = field(default_factory=list)
    rejects: dict[str, int] = field(default_factory=dict)
    last_proposer: str = ""


# --- helpers -----------------------------------------------------------------

def _merge_artifacts(carried: tuple[ArtifactRef, ...],
                     produced: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    """Later artifacts win on the same path; earlier ones keep travelling.

    Carried artifacts are stripped to pointers and only the ones produced THIS
    hop keep their inline content. That is what keeps per-hop cost bounded by one
    deliverable rather than growing with every earlier turn — the whole reason
    the original design refused to carry contents at all.
    """
    merged = {a.path: a.without_content() for a in carried}
    for a in produced:
        if a.path:
            merged[a.path] = a
    return tuple(merged.values())


def _gate_baton(charter, trace_id, st, from_agent, carried, goal, artifacts=(),
                flags=()):
    return Baton(trace_id=trace_id, hop=st.hop, from_agent=from_agent,
                 to_agent=charter.gate_agent, goal=goal,
                 rationale="only the gate can end this run",
                 artifacts=_merge_artifacts(carried, artifacts),
                 open_questions=tuple(charter.acceptance_criteria),
                 flags=tuple(flags))


def _finish(trace: TraceSink, st: _State, reason: str, baton: Baton,
            gate_summary: str = "", note: str = "") -> RunResult:
    if reason not in TERMINAL_REASONS:
        raise BatonError(f"{reason!r} is not a terminal reason")
    trace.append({"event": "run_end", "terminal_reason": reason, "hops": st.hop,
                  "spend_usd": round(st.spend, 6), "path": list(st.path),
                  "gate_summary": gate_summary, "note": note})
    return RunResult(trace_id=trace.trace_id, terminal_reason=reason, hops=st.hop,
                     spend_usd=st.spend, gate_summary=gate_summary, note=note,
                     path=tuple(st.path), last_baton=baton, trace=trace)


def _hop(agent, baton, charter, st, dispatch, guards, trace):
    """One agent execution, with the repair ladder.

    Two attempts, then give up: a third blind retry is almost always wrong and
    always costs another call. Returns (decision, failure) where failure is ""
    on success, "dispatch_failure", or the exception class name.
    """
    nudge = ""
    failure = ""
    for attempt in (1, 2):
        prompt = render_prompt(agent, baton, charter, pressure=st.pressure,
                               include_legal_moves=guards.render_legal_moves,
                               nudge=nudge)
        res = dispatch(agent, baton, prompt)
        st.spend += res.cost_usd
        st.in_tokens += res.in_tokens
        st.out_tokens += res.out_tokens
        trace.append({"event": "dispatch", "hop": st.hop, "agent": agent.name,
                      "attempt": attempt, "cost_usd": res.cost_usd,
                      "in_tokens": res.in_tokens, "out_tokens": res.out_tokens,
                      "error": res.error, "output_chars": len(res.text or "")})
        if res.error:
            return None, "dispatch_failure"
        try:
            decision = parse_decision(res.text)
            decision = validate_decision(decision, agent, charter,
                                         enforce_target=guards.validate_target)
            return decision, ""
        except (ParseFailure, IllegalTarget, RoleViolation) as exc:
            failure = type(exc).__name__
            nudge = repair_nudge(str(exc), agent, charter)
            trace.append({"event": "repair", "hop": st.hop, "agent": agent.name,
                          "attempt": attempt, "reason": str(exc)})
    return None, failure


def _forced_gate_verdict(charter, agents, dispatch, st, baton, trace, guards,
                         reason, goal):
    """One final call to the gate so the run ends JUDGED, not merely stopped.

    Costs one more hop than max_hops on purpose: a bare timeout tells you nothing
    about whether the work was any good, and that is the question being asked.
    """
    gate = agents[charter.gate_agent]
    gate_baton = _gate_baton(charter, trace.trace_id, st,
                             baton.from_agent or "runtime", baton.artifacts,
                             goal=goal, flags=(reason,))
    st.hop += 1
    st.path.append(gate.name)
    trace.append({"event": "forced_gate", "hop": st.hop, "reason": reason})
    decision, failure = _hop(gate, gate_baton, charter, st, dispatch, guards, trace)
    if failure or decision.kind is not Kind.RATIFY:
        return _finish(trace, st, reason, gate_baton,
                       gate_summary=(decision.reason if decision else ""),
                       note=f"forced gate verdict after {reason}")
    return _finish(trace, st, "ratified", gate_baton, gate_summary=decision.summary,
                   note=f"ratified on a forced gate call ({reason})")


def _check_roster(charter: Charter, agents: Mapping[str, AgentSpec]) -> None:
    missing = set(charter.agent_pool) - set(agents)
    if missing:
        raise CharterInvalid(f"agent_pool names agents with no spec: {sorted(missing)}")
    for name, spec in agents.items():
        if not isinstance(spec, AgentSpec):
            raise CharterInvalid(f"{name!r} is not an AgentSpec")
    gate = agents[charter.gate_agent]
    if not gate.is_gate:
        raise CharterInvalid(
            f"{charter.gate_agent!r} has role {gate.role!r} — the gate agent must "
            "have role 'gate', or the run has no external verifier")


# --- the loop ----------------------------------------------------------------

def run(charter: Charter, agents: Mapping[str, AgentSpec], dispatch: Dispatch, *,
        trace: Optional[TraceSink] = None, guards: Optional[Guards] = None,
        trace_id: Optional[str] = None) -> RunResult:
    """Run a multi-agent task in which the agents choose who goes next.

    Args:
        charter: the constraint envelope — budget, hops, pool, acceptance criteria.
        agents: name -> AgentSpec for every agent in the charter's pool.
        dispatch: callable that actually runs one agent. See `Dispatch`.
        trace: where hops are recorded. Any `TraceSink`; defaults to in-memory.
        guards: which defences are active. Defaults to all of them.
        trace_id: correlation id; generated when omitted.

    Returns:
        RunResult, whose `terminal_reason` is always one of TERMINAL_REASONS.

    Raises:
        CharterInvalid: the charter or roster does not describe a runnable run.
        BatonError: the absolute hop backstop was reached, which means a stop
            rule was disabled.
    """
    charter.validate()
    _check_roster(charter, agents)
    guards = guards or Guards()
    trace = trace or MemoryTrace(trace_id=trace_id or uuid.uuid4().hex[:12])

    st = _State()
    baton = Baton(trace_id=trace.trace_id, hop=0, from_agent="charter",
                  to_agent=charter.entry_agent, goal=charter.brief,
                  rationale="run start",
                  open_questions=tuple(charter.acceptance_criteria))
    trace.append({"event": "run_start", "charter": charter.to_dict()})

    while True:
        if st.hop >= _ABSOLUTE_MAX_HOPS:
            raise BatonError(
                f"absolute backstop: {_ABSOLUTE_MAX_HOPS} hops with no terminal "
                "reason — a stop rule is missing or disabled")

        # Killer 1: never-done.
        if guards.hops and st.hop >= charter.max_hops:
            return _forced_gate_verdict(
                charter, agents, dispatch, st, baton, trace, guards,
                "hops_exhausted",
                "This run is out of hops. Assess what exists against the "
                "acceptance criteria and give a verdict.")

        # Killer 2: budget spiral. Hard halt — no forced gate call, because a
        # call made past the ceiling spends money the charter forbade.
        if guards.budget and st.spend >= charter.budget_ceiling_usd:
            return _finish(trace, st, "budget_exhausted", baton,
                           note=(f"spend ${st.spend:.2f} reached the "
                                 f"${charter.budget_ceiling_usd:.2f} ceiling"))

        agent = agents[baton.to_agent]
        st.hop += 1
        st.path.append(agent.name)

        decision, failure = _hop(agent, baton, charter, st, dispatch, guards, trace)

        if (guards.budget and not st.pressure
                and st.spend >= charter.pressure_threshold * charter.budget_ceiling_usd):
            st.pressure = True
            trace.append({"event": "pressure", "hop": st.hop,
                          "spend_usd": round(st.spend, 6),
                          "ceiling_usd": charter.budget_ceiling_usd})

        if failure == "dispatch_failure":
            return _finish(trace, st, "dispatch_failure", baton,
                           note=f"{agent.name} could not be dispatched")

        if failure:
            # Two bad attempts. The gate judges what exists rather than the
            # runtime guessing a third time.
            if agent.is_gate:
                return _finish(trace, st, "charter_violation", baton,
                               note="the gate's own output could not be routed")
            baton = _gate_baton(
                charter, trace.trace_id, st, agent.name, baton.artifacts,
                goal=(f"{agent.name} could not produce a routable decision "
                      f"({failure}). Assess what exists against the criteria."),
                flags=("malformed_routing",))
            continue

        trace.append({"event": "decision", "hop": st.hop, "agent": agent.name,
                      **decision.to_dict()})

        if decision.kind is Kind.RATIFY:
            # A ratification with nothing to point at is not a ratification.
            # Observed live: a worker's routing block failed to parse twice, the
            # runtime escalated to the gate, and the gate ratified an empty
            # artifact set with prose that only restated the acceptance criteria.
            # That run was recorded as DELIVERED. A gate asked to judge is
            # strongly disposed to approve, so the existence of a deliverable has
            # to be checked mechanically rather than asked about in a prompt.
            #
            # This terminates rather than rejecting onward: there is no honest
            # agent to send it back to (the proposer may never have produced a
            # decision at all), and "produced nothing" is a real outcome the
            # bench must be able to see, not an error to retry away.
            ratified = _merge_artifacts(baton.artifacts, decision.artifacts)
            if guards.require_artifacts and not ratified:
                return _finish(trace, st, "ratified_without_deliverable", baton,
                               gate_summary=decision.summary,
                               note=("the gate ratified with no artifact to "
                                     "point at; nothing was delivered"))
            return _finish(trace, st, "ratified", baton,
                           gate_summary=decision.summary)

        if decision.kind is Kind.REJECT:
            proposer = st.last_proposer or decision.to
            st.rejects[proposer] = st.rejects.get(proposer, 0) + 1
            # An uncapped REJECT is a second infinite loop hiding behind the
            # first. Past the cap the gate must ratify or route elsewhere.
            if (guards.reject_cap
                    and decision.to == proposer
                    and st.rejects[proposer] > charter.max_rejects_per_agent):
                return _finish(trace, st, "reject_cap_reached", baton,
                               note=(f"the gate rejected {proposer} "
                                     f"{st.rejects[proposer]} times; after "
                                     f"{charter.max_rejects_per_agent} it must "
                                     "ratify or route to a different agent"))
            baton = Baton(trace_id=trace.trace_id, hop=st.hop,
                          from_agent=agent.name, to_agent=decision.to,
                          goal=decision.reason or "address the gate's findings",
                          rationale="the gate rejected the proposed completion",
                          artifacts=_merge_artifacts(baton.artifacts,
                                                     decision.artifacts),
                          open_questions=tuple(charter.acceptance_criteria))
            continue

        if decision.kind is Kind.PROPOSE_DONE:
            st.last_proposer = agent.name
            baton = _gate_baton(
                charter, trace.trace_id, st, agent.name, baton.artifacts,
                goal=(f"{agent.name} proposes the brief is satisfied: "
                      f"{decision.summary}"),
                artifacts=decision.artifacts)
            continue

        # HANDOFF — killer 3: ping-pong. Escalate first, terminate second.
        pair = (agent.name, decision.to)
        st.pairs.append(pair)
        stall = ""
        if guards.cycle:
            seen = st.pairs.count(pair)
            if seen >= 4:
                return _finish(trace, st, "stalled", baton,
                               note=(f"{agent.name} -> {decision.to} repeated "
                                     f"{seen} times with no progress"))
            if seen == 3:
                trace.append({"event": "stall_escalate", "hop": st.hop,
                              "pair": list(pair)})
                baton = _gate_baton(
                    charter, trace.trace_id, st, agent.name, baton.artifacts,
                    goal=(f"{agent.name} and {decision.to} are looping. Assess "
                          "what exists and break the tie."),
                    artifacts=decision.artifacts, flags=("stalled_loop",))
                continue
            if seen == 2:
                stall = (f"you and {decision.to} have already exchanged this "
                         "baton once — name what is actually blocking progress "
                         "rather than handing it back")

        baton = Baton(trace_id=trace.trace_id, hop=st.hop, from_agent=agent.name,
                      to_agent=decision.to, goal=decision.goal,
                      rationale=decision.rationale,
                      artifacts=_merge_artifacts(baton.artifacts,
                                                 decision.artifacts),
                      open_questions=tuple(charter.acceptance_criteria),
                      stall_notice=stall)
