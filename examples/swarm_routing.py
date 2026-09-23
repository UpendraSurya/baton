#!/usr/bin/env python3
"""Routing that learns from its own runs — and unlearns when the world changes.

    python3 examples/swarm_routing.py          # $0, offline, deterministic

intake routes each ticket by sampling a `swarm.Colony`. Ratified runs lay trail
on the handoffs they took; every run erodes all trails a little. Halfway through,
the desk that can actually resolve these tickets changes from `refunds` to
`docs` — nobody tells intake. Watch the share of tickets routed to the right
desk dip, and recover.

The model is simulated (the gate ratifies only the right desk's work); the
runtime, the guards and the trail arithmetic are real.
"""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from baton import (GATE, AgentSpec, ArtifactRef, Charter, Decision,  # noqa: E402
                   DispatchResult, Kind, MemoryTrace, run, swarm)

DESKS = ("refunds", "engineering", "docs")
AGENTS = {
    "intake": AgentSpec("intake", "Route the ticket to one desk.", set(DESKS) | {"gate"}),
    **{d: AgentSpec(d, f"You are the {d} desk.", {"gate", "intake"}) for d in DESKS},
    "gate": AgentSpec("gate", "Ratify only work that resolves the ticket.",
                      set(DESKS) | {"intake"}, role=GATE),
}
CHARTER = Charter(brief="Resolve the ticket.", entry_agent="intake",
                  gate_agent="gate", agent_pool=frozenset(AGENTS),
                  acceptance_criteria=("a reply", "an internal note"),
                  budget_ceiling_usd=1.0, max_hops=6)


def dispatch_for(colony, rng, right_desk):
    def dispatch(agent, baton, prompt):
        if agent.name == "intake":
            d = Decision(kind=Kind.HANDOFF, goal="resolve", rationale="trail",
                         to=colony.choose("intake", DESKS, rng))
        elif agent.name == "gate":
            d = (Decision(kind=Kind.RATIFY, summary="ok", coverage=("reply.md", "note.md"))
                 if baton.from_agent == right_desk else
                 Decision(kind=Kind.REJECT, to=baton.from_agent, reason="wrong desk"))
        else:
            d = Decision(kind=Kind.PROPOSE_DONE, summary="done", artifacts=(
                ArtifactRef("reply.md", "reply", content="A real reply. " * 12),
                ArtifactRef("note.md", "note", content="A real note. " * 12)))
        return DispatchResult(text=d.render(), in_tokens=1, out_tokens=1)
    return dispatch


def main() -> int:
    colony = swarm.Colony(evaporation=0.2)
    rng = random.Random(1)
    print(f"{'runs':>7}  {'right desk':<10} {'routed right':>12}   p(refunds/engineering/docs)")
    for right in ("refunds", "docs"):
        for block in range(4):
            hits = 0
            for i in range(10):
                r = run(CHARTER, AGENTS, dispatch_for(colony, rng, right),
                        trace=MemoryTrace(f"{right}-{block}-{i}"))
                colony.observe_result(r)
                hits += r.path[1] == right
            w = colony.weights("intake", DESKS)
            print(f"{colony.observed - 9:>3}-{colony.observed:<3}  {right:<10} "
                  f"{hits:>9}/10   " + " / ".join(f"{w[d]:.2f}" for d in DESKS))
    print("\nWhat intake would now read in its prompt:")
    print(colony.note("intake", DESKS).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
