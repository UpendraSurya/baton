"""
baton.reputation — per-agent reliability, computed from what happened.

A dynamic router picks the next agent from a whitelist. Nothing in the kernel
tells it that one of those agents has needed rework on half its past tasks and
another has never needed any. This module turns recorded history into that
sentence, and the `reputation` bench arm is the experiment asking whether it
changes anything.

    from baton import reputation

    rep = reputation.from_traces(traces)            # your own past runs
    print(rep.note(agent.can_hand_to))              # paste into the persona

Two rules hold the honesty line, because a reputation is a claim about somebody:

1. **Sample size travels with the rate.** `AgentRecord` carries `runs`, and
   agents under `min_runs` are dropped rather than reported with a confident
   number computed from three data points.
2. **A clean record is not mentioned.** `note()` lists only agents that have
   actually needed rework. "tester has needed rework on 0% of past tasks" is
   noise re-sent on every hop, and reading it as praise is exactly the
   inference the evidence does not support.

Zero dependencies, and no opinion about where history is stored: `from_tallies`
takes two counts, so a caller whose corpus lives in SQLite, a warehouse or a CSV
does the counting and keeps its schema out of the kernel.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

# Under this many runs a rate is arithmetic, not evidence.
DEFAULT_MIN_RUNS: int = 3

# How many agents a note names before it stops being read.
DEFAULT_NOTE_LIMIT: int = 4


@dataclass(frozen=True)
class AgentRecord:
    """One agent's track record. `runs` is here so a caller can see that a 50%
    rate is 1-in-2 rather than 39-in-78."""

    agent: str
    runs: int
    revisions: int

    @property
    def rate(self) -> float:
        """Share of this agent's work that came back for rework. Lower is better."""
        return self.revisions / self.runs

    def to_dict(self) -> dict[str, object]:
        return {"agent": self.agent, "runs": self.runs,
                "revisions": self.revisions, "rate": self.rate}


class Reputation(Mapping[str, AgentRecord]):
    """A read-only mapping of agent name -> AgentRecord, plus the two views a
    router actually uses: `ranked()` and `note()`."""

    def __init__(self, records: Mapping[str, AgentRecord]) -> None:
        self._records = dict(records)

    # -- mapping --------------------------------------------------------------
    def __getitem__(self, agent: str) -> AgentRecord:
        return self._records[agent]

    def __iter__(self) -> Iterator[str]:
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"Reputation({len(self._records)} agents)"

    # -- views ----------------------------------------------------------------
    def rate(self, agent: str, default: float | None = None) -> float | None:
        """The agent's rework rate, or `default` when there is not enough
        history to make a claim. Never invents a 0.0 for an unknown agent."""
        record = self._records.get(agent)
        return default if record is None else record.rate

    def ranked(self, names: Iterable[str] | None = None) -> tuple[AgentRecord, ...]:
        """Worst first. Ties break on name so the order is stable across runs —
        an unstable prompt is an uncontrolled variable in any experiment."""
        pool = self._records.values() if names is None else [
            self._records[n] for n in names if n in self._records]
        return tuple(sorted(pool, key=lambda r: (-r.rate, r.agent)))

    def note(self, names: Iterable[str], *, limit: int = DEFAULT_NOTE_LIMIT) -> str:
        """The line that reaches the model: numbers, no adjectives. Empty when
        no agent on this team has needed rework, so a prompt gains tokens only
        where it gains information."""
        listed = [r for r in self.ranked(names) if r.revisions][:limit]
        if not listed:
            return ""
        body = "; ".join(f"{r.agent} has needed rework on {r.rate:.0%} of past tasks"
                         for r in listed)
        return ("\n\n> **Track record on this team** (from prior runs): " + body
                + ". Weigh this when choosing who to hand to.")

    def to_dict(self) -> dict[str, dict[str, object]]:
        return {name: record.to_dict() for name, record in self._records.items()}


def from_tallies(runs: Mapping[str, int], revisions: Mapping[str, int], *,
                 min_runs: int = DEFAULT_MIN_RUNS) -> Reputation:
    """Build a Reputation from two counts: pieces of work produced per agent,
    and pieces of that work that came back for rework.

    Raises ValueError if `revisions` names an agent that `runs` does not: that
    means the two tallies were counted over different populations, and dropping
    the orphan silently would understate the team's rework."""
    if min_runs < 1:
        raise ValueError("min_runs must be at least 1; a rate over zero runs "
                         "is a division by zero, not a clean record")
    orphans = sorted(set(revisions) - set(runs))
    if orphans:
        raise ValueError(
            f"revisions recorded for agents with no runs: {', '.join(orphans)} — "
            "the two tallies were counted over different populations")
    return Reputation({
        agent: AgentRecord(agent, n, revisions.get(agent, 0))
        for agent, n in runs.items() if n >= min_runs})


def from_traces(traces: Iterable[Iterable[Mapping[str, object]]], *,
                min_runs: int = DEFAULT_MIN_RUNS) -> Reputation:
    """Build a Reputation from baton's own trace records — `Trace.records()`
    from past runs, one list per run.

    A `dispatch` is one piece of work. A `REJECT` counts against the agent it
    was sent back to, never against the gate that issued it: the gate rejecting
    is the gate doing its job. A `HANDOFF` is not rework — it is the routing
    this library exists to allow."""
    runs: dict[str, int] = {}
    revisions: dict[str, int] = {}
    for records in traces:
        for record in records:
            event = record.get("event")
            if event == "dispatch":
                agent = record.get("agent")
                if isinstance(agent, str):
                    runs[agent] = runs.get(agent, 0) + 1
            elif event == "decision" and record.get("decision") == "REJECT":
                sent_back_to = record.get("to")
                if isinstance(sent_back_to, str):
                    revisions[sent_back_to] = revisions.get(sent_back_to, 0) + 1
    # A reject can name an agent that never appears as a dispatch in the traces
    # handed in (a run split across files, say). That is a caller's-population
    # problem in from_tallies, but here it is expected, so the agent simply has
    # no denominator and is not claimed about.
    revisions = {a: n for a, n in revisions.items() if a in runs}
    return from_tallies(runs, revisions, min_runs=min_runs)


__all__ = ["AgentRecord", "Reputation", "from_tallies", "from_traces",
           "DEFAULT_MIN_RUNS", "DEFAULT_NOTE_LIMIT"]
