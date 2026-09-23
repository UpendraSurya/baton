"""
baton.swarm — stigmergic routing memory: handoff trails that runs reinforce
and time erodes.

`reputation` scores AGENTS. A router's actual choice is an EDGE — "from here,
hand to whom?" — and the right answer to that can differ by where the baton
currently is. This module keeps a trail per edge the way an ant colony keeps
pheromone on a path (Dorigo's Ant System; AntNet applied it to packet routing):

    every observed run:   trail  <-  (1 - evaporation) * trail
    a RATIFIED run:       trail  +=  deposit / hops     on each edge it took
    any other run:        trail  <-  (1 - penalty) * trail   on each edge it took

Three properties fall out, and they are the reason to use this over a
counter:

1. **Shorter successful routes lay denser trails.** The deposit is shared over
   the run's hops, so a 3-hop delivery reinforces its edges more than a 9-hop
   delivery reinforces its own.
2. **Old evidence fades, failing evidence fades faster.** Evaporation is per
   observed run and `penalty` erodes the edges a failed run took, so a route
   that stopped working stops being recommended — without anyone deciding
   when to forget. Trails are bounded above by `deposit / evaporation`.
3. **Exploration never dies.** `weights()` floors every legal candidate at an
   ABSOLUTE trail of `explore * deposit`, so an unreinforced edge keeps a
   nonzero chance, and as a stale route's trail sinks toward that floor the
   odds flatten back toward uniform and the colony re-explores. (The floor is
   the Max-Min Ant System's tau_min. A floor relative to the strongest trail
   would be scale-invariant, and evaporation would then never move a
   probability at all.)

    from baton import swarm

    colony = swarm.Colony()
    for records in past_traces:            # oldest first: order is evaporation
        colony.observe(records)
    agents = colony.annotate(agents)       # a trail note in each persona
    result = run(charter, agents, dispatch)
    colony.observe_result(result)          # the colony learns from this run too

The honesty lines are the same as `reputation`'s, because a trail is a claim
about a route:

* **Advisory only.** Nothing here widens or narrows `can_hand_to`; the kernel's
  whitelist, validation and stop rules are untouched. A colony can make a
  router better informed. It cannot make a run less safe.
* **Counts travel with the signal.** `note()` states "k of n runs ratified",
  never an adjective, and stays silent on an edge under `min_runs`.
* **An unfinished run is not a failed run.** A trace with no `run_end` record
  has no outcome and is skipped, not scored as a loss.
* **Unproven.** Whether trail notes change delivery rates on real work is an
  open question of exactly the kind docs/measuring-dynamic-routing.md
  describes. This is an instrument for that experiment, not its result.

Zero dependencies. `to_dict()` / `from_dict()` let the colony live wherever
the caller keeps state — the kernel has no opinion.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Optional

from baton.agent import AgentSpec

# One observed run erodes every trail by this share. 0.1 means a route's
# evidence halves in about seven runs without reinforcement.
DEFAULT_EVAPORATION: float = 0.1

# What one ratified run lays down, shared across its hops.
DEFAULT_DEPOSIT: float = 1.0

# A failed run erodes the edges it took by this share, on top of evaporation.
# 0 is the classic Ant System: failure is only the absence of reinforcement.
DEFAULT_PENALTY: float = 0.3

# Every legal candidate is weighed as if it had at least `explore * deposit` of
# trail, so no whitelisted agent is ever routed to with probability zero.
DEFAULT_EXPLORE: float = 0.05

# Same bar as reputation: under this many runs through an edge, a ratification
# rate is arithmetic, not evidence.
DEFAULT_MIN_RUNS: int = 3

# How many edges a note names before it stops being read.
DEFAULT_NOTE_LIMIT: int = 4

# Below this a trail is indistinguishable from never having existed, and is
# dropped so a long-lived colony does not grow without bound.
_PRUNE_BELOW: float = 1e-9

# The routing decisions the runtime accepted, and so the edges a run took.
_ROUTING = ("HANDOFF", "PROPOSE_DONE", "REJECT")


@dataclass(frozen=True)
class Trail:
    """One edge's evidence. `strength` is the recency-weighted signal a router
    samples on; `runs` and `ratified` are the raw counts a reader can check it
    against, and they never decay."""

    src: str
    dst: str
    strength: float
    runs: int
    ratified: int

    @property
    def rate(self) -> float:
        """Share of observed runs through this edge that were ratified."""
        return self.ratified / self.runs if self.runs else 0.0

    def to_dict(self) -> dict[str, object]:
        return {"src": self.src, "dst": self.dst, "strength": self.strength,
                "runs": self.runs, "ratified": self.ratified}


def edges_of(records: Iterable[Mapping[str, object]]) -> tuple[list[tuple[str, str]], Optional[str], int]:
    """(edges taken, terminal reason or None, hops) for one run's trace records.

    An edge is a routing decision the runtime accepted: HANDOFF and REJECT go
    to `to`; PROPOSE_DONE goes to the charter's gate. Runtime-made moves — a
    malformed decision escalated, a forced gate call — are not an agent's
    choice and lay no trail.
    """
    gate = ""
    edges: list[tuple[str, str]] = []
    reason: Optional[str] = None
    hops = 0
    for r in records:
        event = r.get("event")
        if event == "run_start":
            charter = r.get("charter")
            if isinstance(charter, Mapping):
                g = charter.get("gate_agent")
                gate = g if isinstance(g, str) else ""
        elif event == "decision" and r.get("decision") in _ROUTING:
            src = r.get("agent")
            dst = gate if r.get("decision") == "PROPOSE_DONE" else r.get("to")
            if isinstance(src, str) and isinstance(dst, str) and src and dst:
                edges.append((src, dst))
        elif event == "run_end":
            t = r.get("terminal_reason")
            reason = t if isinstance(t, str) else None
            h = r.get("hops")
            hops = h if isinstance(h, int) else 0
    return edges, reason, hops


class Colony:
    """Per-edge trails over a team of agents, updated one observed run at a time.

    Mutable on purpose: a colony is the environment runs write into. Everything
    it knows is in `to_dict()`, so persisting or forking one is a JSON dump.
    """

    def __init__(self, *, evaporation: float = DEFAULT_EVAPORATION,
                 deposit: float = DEFAULT_DEPOSIT,
                 penalty: float = DEFAULT_PENALTY,
                 min_runs: int = DEFAULT_MIN_RUNS) -> None:
        if not 0 < evaporation <= 1:
            raise ValueError("evaporation must be in (0, 1]; at 0 a colony never "
                             "forgets and cannot follow a change")
        if deposit <= 0:
            raise ValueError("deposit must be > 0")
        if not 0 <= penalty <= 1:
            raise ValueError("penalty must be in [0, 1]")
        if min_runs < 1:
            raise ValueError("min_runs must be at least 1")
        self.evaporation = float(evaporation)
        self.deposit = float(deposit)
        self.penalty = float(penalty)
        self.min_runs = int(min_runs)
        self.observed = 0          # runs that changed the trails
        self.skipped = 0           # traces with no outcome, deliberately ignored
        self._strength: dict[tuple[str, str], float] = {}
        self._runs: dict[tuple[str, str], int] = {}
        self._ratified: dict[tuple[str, str], int] = {}

    def __repr__(self) -> str:
        return (f"Colony({len(self._runs)} edges, {self.observed} runs, "
                f"evaporation={self.evaporation:g})")

    # -- learning -------------------------------------------------------------
    def observe(self, records: Iterable[Mapping[str, object]]) -> bool:
        """Fold one run's trace records in. Returns False, and changes nothing,
        when the trace has no outcome to learn from.

        Call in chronological order: evaporation makes order meaningful.
        """
        edges, reason, hops = edges_of(records)
        if reason is None:
            self.skipped += 1
            return False
        keep = 1.0 - self.evaporation
        for edge in list(self._strength):
            s = self._strength[edge] * keep
            if s < _PRUNE_BELOW:
                del self._strength[edge]
            else:
                self._strength[edge] = s
        ok = reason == "ratified"
        share = self.deposit / max(1, hops, len(edges))
        for edge in set(edges):
            self._runs[edge] = self._runs.get(edge, 0) + 1
            if ok:
                self._ratified[edge] = self._ratified.get(edge, 0) + 1
                self._strength[edge] = self._strength.get(edge, 0.0) + share
            elif edge in self._strength:
                self._strength[edge] *= 1.0 - self.penalty
        self.observed += 1
        return True

    def observe_result(self, result: object) -> bool:
        """`observe()` for a `RunResult`, read through its trace."""
        trace = getattr(result, "trace", None)
        if trace is None:
            raise ValueError("this RunResult carries no trace; there is nothing "
                             "to learn the route from")
        return self.observe(trace.records())

    # -- reading --------------------------------------------------------------
    def trail(self, src: str, dst: str) -> Optional[Trail]:
        """The evidence for one edge, or None if no observed run took it."""
        edge = (src, dst)
        if edge not in self._runs:
            return None
        return Trail(src, dst, self._strength.get(edge, 0.0),
                     self._runs[edge], self._ratified.get(edge, 0))

    def trails(self, src: Optional[str] = None) -> tuple[Trail, ...]:
        """Every known edge (from `src`, if given), strongest first. Ties break
        on names so the order is stable across runs."""
        out = [t for e in self._runs if src is None or e[0] == src
               for t in [self.trail(*e)] if t is not None]
        return tuple(sorted(out, key=lambda t: (-t.strength, t.src, t.dst)))

    def weights(self, src: str, candidates: Iterable[str], *,
                alpha: float = 1.0, beta: float = 0.0,
                heuristic: Optional[Callable[[str], float]] = None,
                explore: float = DEFAULT_EXPLORE) -> dict[str, float]:
        """The Ant System transition rule over the legal candidates:

            p(dst)  ∝  max(trail, explore * deposit) ** alpha  *  heuristic(dst) ** beta

        Returns probabilities summing to 1 over exactly `candidates`. With no
        trail above the floor every one is equally likely — a colony with no
        evidence has no opinion. `heuristic` is the ACO "visibility" term;
        `from_reputation()` builds one from rework rates.
        """
        names = sorted(set(candidates))
        if not names:
            raise ValueError("no candidates to weigh")
        if not 0 < explore <= 1:
            raise ValueError("explore must be in (0, 1]; at 0 an unreinforced "
                             "edge can never be tried again")
        if alpha < 0 or beta < 0:
            raise ValueError("alpha and beta must be >= 0")
        if beta and heuristic is None:
            raise ValueError("beta > 0 needs a heuristic to weigh")
        floor = explore * self.deposit
        raw: dict[str, float] = {}
        for n in names:
            tau = max(self._strength.get((src, n), 0.0), floor)
            eta = 1.0
            if beta:
                eta = float(heuristic(n))              # type: ignore[misc]
                if not eta > 0:
                    raise ValueError(f"heuristic({n!r}) = {eta}; it must be > 0 "
                                     "or that agent can never be chosen")
            raw[n] = tau ** alpha * eta ** beta
        total = sum(raw.values())
        return {n: w / total for n, w in raw.items()}

    def choose(self, src: str, candidates: Iterable[str], rng: random.Random,
               **kw: object) -> str:
        """Sample one candidate from `weights()`. `rng` is required so a run
        that routes on the colony can be replayed exactly."""
        w = self.weights(src, candidates, **kw)             # type: ignore[arg-type]
        names = list(w)
        return rng.choices(names, weights=[w[n] for n in names], k=1)[0]

    def note(self, src: str, candidates: Iterable[str], *,
             limit: int = DEFAULT_NOTE_LIMIT) -> str:
        """The line that reaches the model: counts, no adjectives. Empty when no
        edge from `src` to a candidate has `min_runs` of history, so a prompt
        gains tokens only where it gains information."""
        wanted = set(candidates)
        listed = [t for t in self.trails(src)
                  if t.dst in wanted and t.runs >= self.min_runs][:limit]
        if not listed:
            return ""
        body = "; ".join(f"handing to {t.dst} ratified in {t.ratified} of "
                         f"{t.runs} past runs" for t in listed)
        return ("\n\n> **Route history from here** (from prior runs, recent runs "
                "weighted first): " + body
                + ". Weigh this when choosing who to hand to.")

    def annotate(self, agents: Mapping[str, AgentSpec]) -> dict[str, AgentSpec]:
        """A copy of `agents` with each one's trail note appended to its
        instructions. `can_hand_to` is never touched — the colony informs the
        choice, the whitelist still bounds it."""
        out: dict[str, AgentSpec] = {}
        for name, spec in agents.items():
            line = self.note(spec.name, spec.can_hand_to)
            out[name] = (replace(spec, instructions=spec.instructions + line)
                         if line else spec)
        return out

    # -- persistence ----------------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {"evaporation": self.evaporation, "deposit": self.deposit,
                "penalty": self.penalty, "min_runs": self.min_runs,
                "observed": self.observed,
                "skipped": self.skipped,
                "trails": [t.to_dict() for t in self.trails()]}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Colony":
        colony = cls(evaporation=float(data["evaporation"]),      # type: ignore[arg-type]
                     deposit=float(data["deposit"]),              # type: ignore[arg-type]
                     penalty=float(data.get("penalty", 0.0)),     # type: ignore[arg-type]
                     min_runs=int(data["min_runs"]))              # type: ignore[call-overload]
        colony.observed = int(data.get("observed", 0))            # type: ignore[call-overload]
        colony.skipped = int(data.get("skipped", 0))              # type: ignore[call-overload]
        for t in data.get("trails", ()):                          # type: ignore[attr-defined]
            edge = (str(t["src"]), str(t["dst"]))
            colony._runs[edge] = int(t["runs"])
            if int(t["ratified"]):
                colony._ratified[edge] = int(t["ratified"])
            if float(t["strength"]) > 0:
                colony._strength[edge] = float(t["strength"])
        return colony


def from_traces(traces: Iterable[Iterable[Mapping[str, object]]], **kw: object) -> Colony:
    """A Colony built from past runs' `Trace.records()`, oldest first."""
    colony = Colony(**kw)                                         # type: ignore[arg-type]
    for records in traces:
        colony.observe(records)
    return colony


def from_reputation(rep: Mapping[str, object], *, unknown: float) -> Callable[[str], float]:
    """An ACO heuristic from a `Reputation`: 1 - rework rate, floored above zero
    so a bad record lowers an agent's odds without removing it.

    `unknown` has no default: an agent with no track record has no honest
    score, and the caller — not the library — decides what to assume.
    """
    if not 0 < unknown <= 1:
        raise ValueError("unknown must be in (0, 1]")

    def eta(name: str) -> float:
        record = rep.get(name)
        if record is None:
            return unknown
        return max(1.0 - float(getattr(record, "rate")), 0.01)
    return eta


__all__ = ["Colony", "Trail", "edges_of", "from_traces", "from_reputation",
           "DEFAULT_EVAPORATION", "DEFAULT_DEPOSIT", "DEFAULT_PENALTY",
           "DEFAULT_EXPLORE",
           "DEFAULT_MIN_RUNS", "DEFAULT_NOTE_LIMIT"]
