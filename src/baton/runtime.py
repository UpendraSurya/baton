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

import threading
import time

import uuid
from dataclasses import dataclass, field, fields, replace
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
                    "ratified_without_deliverable",
                    "ratified_without_coverage", "time_exhausted")

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
    # Which model actually ran. Evidence, not a requirement: a provider that
    # leaves it empty still runs, but its spend cannot be reconciled against a
    # bill afterwards, and a host ledger has nothing honest to file it under.
    model_id: str = ""


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
    require_coverage: bool = True
    require_substance: bool = True
    # The 'one file cannot answer every independent requirement' rule.
    # It is a heuristic about gates that GUESS, and it is a false negative
    # on a gate that MEASURED — one that ran the acceptance criteria and
    # reported what passed. Separate from require_coverage so a measuring
    # gate can drop it without also dropping the checks that are still
    # true. Reported from outside by migration-stress.
    require_independent_work: bool = True
    wall_clock: bool = True
    preflight: bool = True

    def without(self, *names: str) -> "Guards":
        """A copy of this Guards with the named guard(s) switched off.

        `dataclasses.replace(guards, x=False)` already does this, but it asks
        every caller to import dataclasses and know it applies here, for the
        single most common thing anything does with a Guards instance it did
        not just construct from scratch: turn one more thing off without
        losing whatever else was already configured on it. Found by real use —
        tests/test_anti_vacuity.py and test_every_guard_is_falsifiable.py both
        hand-construct one-guard-off instances throughout instead.
        """
        valid = {f.name for f in fields(self)}
        unknown = sorted(set(names) - valid)
        if unknown:
            raise ValueError(f"not a guard on {type(self).__name__}: {unknown}")
        return replace(self, **{name: False for name in names})


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
    # What was ratified. Empty for every non-ratified terminal reason, so a
    # caller cannot mistake a half-filled matrix for a delivery.
    #
    # `last_baton.artifacts` is NOT the same set: that is what ARRIVED at the
    # gate, before the gate attached its own and before carried artifacts were
    # stripped to pointers. A consumer rebuilding the runtime's own conclusion
    # by scanning the trace is the tell that a return value was missing —
    # reported from outside by a project consuming the wheel.
    coverage: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
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
    # Why the last dispatch failed, verbatim. Without it the terminal note can
    # only say "could not be dispatched", which is the same sentence for a
    # provider 500, a bad API key and a KeyError in the caller's own adapter.
    dispatch_error: str = ""
    # monotonic, so a clock change mid-run cannot extend or collapse a deadline
    started_at: float = field(default_factory=time.monotonic)


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
            gate_summary: str = "", note: str = "",
            coverage: tuple[str, ...] = (),
            artifacts: tuple[ArtifactRef, ...] = ()) -> RunResult:
    if reason not in TERMINAL_REASONS:
        raise BatonError(f"{reason!r} is not a terminal reason")
    trace.append({"event": "run_end", "terminal_reason": reason, "hops": st.hop,
                  "spend_usd": round(st.spend, 6), "path": list(st.path),
                  "gate_summary": gate_summary, "note": note})
    return RunResult(trace_id=trace.trace_id, terminal_reason=reason, hops=st.hop,
                     spend_usd=st.spend, gate_summary=gate_summary, note=note,
                     path=tuple(st.path), last_baton=baton, trace=trace,
                     coverage=tuple(coverage), artifacts=tuple(artifacts))


def _is_stub(a):
    """An artifact whose CLAIM outweighs its CONTENT.

    Self-calibrating on purpose: no magic byte count, just the description
    being longer than the work it describes. Observed on ForgeLine —
    `Dockerfile.stripe`, described as "Production-ready Dockerfile ... with
    idempotency and signature verification", contained
    "# Dockerfile.stripe content shown above" and nothing else.

    An artifact with NO content is not a stub. path + description is the
    legitimate "open it yourself" form for something the reader can fetch;
    only content that under-delivers its own description counts.
    """
    size = a.content_chars or len((a.content or "").strip())
    if not size:
        return False           # a pure pointer is legitimate, not a stub
    return size < max(80, len(a.description or ""))


def _ratify_problem(decision, artifacts, charter, guards):
    """Why this RATIFY must not stand — or None.

    Existence was never the property that mattered. A gate asked "is this
    good?" says yes; a gate asked "which file satisfies criterion 3?" has to
    point at something, and the runtime can check whether it exists.
    """
    if guards.require_artifacts and not artifacts:
        return ("ratified_without_deliverable",
                "the gate ratified with no artifact to point at; "
                "nothing was delivered")

    if guards.require_substance and artifacts:
        stubs = [a.path for a in artifacts if _is_stub(a)]
        if len(stubs) * 2 > len(artifacts):
            return ("ratified_without_deliverable",
                    f"{len(stubs)} of {len(artifacts)} artifacts are stubs — "
                    f"the description outweighs the content in "
                    f"{', '.join(stubs[:4])}; a claim is not a deliverable")

    if guards.require_coverage:
        criteria = charter.acceptance_criteria
        cover = decision.coverage
        # Two ways for a criterion to have nothing behind it, and they mean the
        # same thing: a blank entry in the array, or no entry at all because the
        # array stopped short. Naming WHICH criterion is the whole diagnosis —
        # a short array alone can only say "the tail", and if the gap was in the
        # middle that answer is wrong. Both consumers of 0.1.0 had to invent a
        # sentinel string to get this back.
        unanswered = [c for i, c in enumerate(criteria)
                      if i >= len(cover) or not cover[i]]
        if unanswered:
            shown = "; ".join(f"{c!r}" for c in unanswered[:3])
            more = f" (+{len(unanswered) - 3} more)" if len(unanswered) > 3 else ""
            return ("ratified_without_coverage",
                    f"the gate ratified {len(criteria)} acceptance criteria "
                    f"while naming work for {len(criteria) - len(unanswered)}. "
                    f"Nothing is cited for: {shown}{more}")
        paths = {a.path for a in artifacts}
        missing = [c for c in cover[:len(criteria)] if c not in paths]
        if missing:
            return ("ratified_without_coverage",
                    f"the gate cited artifacts that do not exist: "
                    f"{', '.join(missing)}")
        # Repeating a path is fine when two criteria share a deliverable. ONE
        # file answering many independent requirements is the citation being
        # gamed — observed on ForgeLine, where a single topology_library.json
        # was cited for a web app, a 3D viewer, an ETL pipeline, an ML model,
        # billing and GDPR alike.
        cited = set(cover[:len(criteria)])
        if guards.require_independent_work and len(criteria) >= 3 and len(cited) == 1:
            return ("ratified_without_coverage",
                    f"the gate cited one artifact ({cited.pop()}) for all "
                    f"{len(criteria)} criteria; independent requirements need "
                    "independent work")
    return None


def _dispatch_bounded(dispatch, agent, baton, prompt, charter, guards):
    """Call the dispatch, but never wait forever for it.

    A provider's own timeout cannot be trusted to be the stop rule: the run this
    was written for set `urlopen(timeout=30)` and still sat blocked for 25
    minutes. A stop rule that depends on the thing being stopped is not one.

    The call runs on a DAEMON thread so a hung provider cannot hold the process
    open after the run gives up on it. The thread is genuinely abandoned, not
    cancelled — Python cannot interrupt a blocked socket read — so it may still
    be holding a connection when we return. That is the honest trade: a leaked
    thread the OS will reap at exit, against a run that never ends.
    """
    if not guards.wall_clock:
        return _caught(dispatch, agent, baton, prompt), False

    box = {}

    def call():
        try:
            box["res"] = dispatch(agent, baton, prompt)
        except BaseException as exc:                  # noqa: BLE001 — re-raised below
            box["exc"] = exc

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(charter.max_dispatch_seconds)
    if t.is_alive():
        return None, True
    if "exc" in box:
        exc = box["exc"]
        if isinstance(exc, Exception):
            return _as_failure(exc), False
        raise exc
    return box["res"], False


def _as_failure(exc: Exception) -> DispatchResult:
    """An exception out of a dispatch is a transport failure, not a crash.

    `Dispatch`'s docstring invites "just pass a plain function with this
    signature", so the callable is USER code and will raise user exceptions —
    an adapter's KeyError, a vendor SDK's own type, an unexpected schema. Those
    used to propagate out of run(), which broke the one guarantee the whole
    library rests on: no RunResult, no terminal reason, no run_end record. A
    run that dies is not a run that ended.

    BaseException (KeyboardInterrupt, SystemExit) is deliberately NOT caught.
    An operator pressing ctrl-C is not a transport failure, and a runtime that
    eats it is worse than one that stops.
    """
    return DispatchResult(error=f"{type(exc).__name__}: {exc}")


def _caught(dispatch, agent, baton, prompt) -> DispatchResult:
    try:
        return dispatch(agent, baton, prompt)
    except Exception as exc:                          # noqa: BLE001 — see _as_failure
        return _as_failure(exc)


def _without_exhausted(gate, st, charter):
    """The gate, minus every agent it has already rejected to its cap.

    The cap used to TERMINATE the run. That enforced nothing about the gate's
    choice — it just killed a project because one agent had been sent back
    twice, while every other staffed agent sat untouched. Observed on the
    ForgeLine brief: 32 agents staffed, 8 ever dispatched, dead at the cap.

    Narrowing the whitelist is the harness fix. `legal_moves_for` is the single
    place both the rendered prompt and the post-parse validation read, so the
    gate is never OFFERED an exhausted agent and cannot name one. Re-prompting
    would have fixed one run; this fixes the class.

    If everyone is exhausted the gate keeps an empty target list, which leaves
    it exactly one legal act — ratify — and the cap still terminates a gate
    that refuses. Demoted to a backstop, not deleted.
    """
    exhausted = {a for a, n in st.rejects.items()
                 if n >= charter.max_rejects_per_agent}
    if not exhausted:
        return gate
    return replace(gate, can_hand_to=frozenset(gate.can_hand_to) - exhausted)


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
        res, timed_out = _dispatch_bounded(dispatch, agent, baton, prompt,
                                           charter, guards)
        if timed_out:
            trace.append({"event": "dispatch_timeout", "hop": st.hop,
                          "agent": agent.name, "attempt": attempt,
                          "limit_s": charter.max_dispatch_seconds})
            return None, "dispatch_timeout"
        st.spend += res.cost_usd
        st.in_tokens += res.in_tokens
        st.out_tokens += res.out_tokens
        trace.append({"event": "dispatch", "hop": st.hop, "agent": agent.name,
                      "attempt": attempt, "cost_usd": res.cost_usd,
                      "in_tokens": res.in_tokens, "out_tokens": res.out_tokens,
                      "model_id": res.model_id,
                      "error": res.error, "output_chars": len(res.text or "")})
        if res.error:
            st.dispatch_error = res.error
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
        trace_id: Optional[str] = None, usd_per_call: float = 0.0) -> RunResult:
    """Run a multi-agent task in which the agents choose who goes next.

    Args:
        charter: the constraint envelope — budget, hops, pool, acceptance criteria.
        agents: name -> AgentSpec for every agent in the charter's pool.
        dispatch: callable that actually runs one agent. See `Dispatch`.
        trace: where hops are recorded. Any `TraceSink`; defaults to in-memory.
        guards: which defences are active. Defaults to all of them.
        trace_id: correlation id; generated when omitted.
        usd_per_call: what ONE model call costs you. Supply it and the run is
            pre-flighted against the ceiling and REFUSED before a cent is
            spent if the charter was never survivable. Omitted, the runtime
            has no basis for that judgement and does not invent one.

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
    # Pre-flight. A ceiling breached on hop 1 is a CONFIGURATION error, not a
    # run outcome, so it is refused here beside the other CharterInvalid checks
    # rather than discovered by spending a call. Only possible when the caller
    # says what a call costs — the runtime will not invent a number to refuse on.
    if guards.preflight and usd_per_call > 0:
        from baton import plan as _plan
        _plan.estimate(charter, agents, usd_per_call=usd_per_call).raise_if_infeasible()
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

        # Killer 5: a run that never comes back. Checked here as well as per
        # dispatch, because many merely-slow hops add up to the same outcome.
        if guards.wall_clock:
            ran = time.monotonic() - st.started_at
            if ran >= charter.max_wall_seconds:
                return _finish(trace, st, "time_exhausted", baton,
                               # :g not :.1f — a 0.15s deadline printed as
                               # "0.1s" is a wrong number in an error message
                               note=(f"ran {ran:.1f}s, past the "
                                     f"{charter.max_wall_seconds:g}s deadline"))

        # Killer 2: budget spiral. Hard halt — no forced gate call, because a
        # call made past the ceiling spends money the charter forbade.
        if guards.budget and st.spend >= charter.budget_ceiling_usd:
            return _finish(trace, st, "budget_exhausted", baton,
                           note=(f"spend ${st.spend:.2f} reached the "
                                 f"${charter.budget_ceiling_usd:.2f} ceiling"))

        agent = agents[baton.to_agent]
        if agent.is_gate and guards.reject_cap:
            agent = _without_exhausted(agent, st, charter)
        st.hop += 1
        st.path.append(agent.name)

        decision, failure = _hop(agent, baton, charter, st, dispatch, guards, trace)

        if (guards.budget and not st.pressure
                and st.spend >= charter.pressure_threshold * charter.budget_ceiling_usd):
            st.pressure = True
            trace.append({"event": "pressure", "hop": st.hop,
                          "spend_usd": round(st.spend, 6),
                          "ceiling_usd": charter.budget_ceiling_usd})

        if failure == "dispatch_timeout":
            return _finish(trace, st, "dispatch_failure", baton,
                           note=(f"{agent.name} did not answer within "
                                 f"{charter.max_dispatch_seconds}s — abandoned"))
        if failure == "dispatch_failure":
            detail = f": {st.dispatch_error}" if st.dispatch_error else ""
            return _finish(trace, st, "dispatch_failure", baton,
                           note=f"{agent.name} could not be dispatched{detail}")

        if failure:
            # Two bad attempts. The gate judges what exists rather than the
            # runtime guessing a third time.
            if agent.is_gate:
                # Distinguish "the gate wrote nonsense" from "the gate kept
                # naming an agent it had already exhausted". Both arrive here as
                # an unroutable decision, but only the second has a cause worth
                # reporting, and calling it charter_violation hides the reason
                # the run actually ended.
                spent = sorted(a for a, n in st.rejects.items()
                               if n >= charter.max_rejects_per_agent)
                if guards.reject_cap and spent:
                    return _finish(trace, st, "reject_cap_reached", baton,
                                   note=(f"the gate rejected {', '.join(spent)} "
                                         f"to the cap of "
                                         f"{charter.max_rejects_per_agent} and "
                                         "would not route anywhere else"))
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
            problem = _ratify_problem(decision, ratified, charter, guards)
            if problem:
                reason, note = problem
                return _finish(trace, st, reason, baton,
                               gate_summary=decision.summary, note=note)
            return _finish(trace, st, "ratified", baton,
                           gate_summary=decision.summary,
                           coverage=decision.coverage, artifacts=ratified)

        if decision.kind is Kind.REJECT:
            proposer = st.last_proposer or decision.to
            st.rejects[proposer] = st.rejects.get(proposer, 0) + 1
            # An uncapped REJECT is a second infinite loop hiding behind the
            # first. Past the cap the gate must ratify or route elsewhere.
            # Reaching here past the cap means the gate had no un-exhausted
            # target left and still refused to ratify.
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
            # nothing_found reaches the gate as a flag, not silently folded
            # into the goal text — a gate scanning `baton.flags` for how a run
            # got here should see this the same way it sees "stalled_loop" or
            # "malformed_routing", not have to parse it back out of prose.
            flags = ("nothing_found",) if decision.nothing_found else ()
            baton = _gate_baton(
                charter, trace.trace_id, st, agent.name, baton.artifacts,
                goal=(f"{agent.name} proposes the brief is satisfied: "
                      f"{decision.summary}"),
                artifacts=decision.artifacts, flags=flags)
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
