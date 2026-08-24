#!/usr/bin/env python3
"""A four-agent support desk. Nobody is told the running order.

    python3 examples/triage.py                 # $0, offline, deterministic
    python3 examples/triage.py --live          # real routing on Mistral (~$0.002)

The point of the example is the third line of output: two tickets, the same
charter, the same agent pool, and DIFFERENT paths through it. No edge was drawn
between `intake` and `refunds`; intake decided.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from baton import (GATE, AgentSpec, ArtifactRef, Charter, Decision,  # noqa: E402
                   DispatchResult, Kind, MemoryTrace, run)

# --- the pool ----------------------------------------------------------------
# `hand_to` is a WHITELIST, not an edge list: it says who this agent is allowed
# to reach, not who it will reach. intake may pick any of the three specialists;
# which one is a decision made at runtime, by the agent, from the ticket text.

SPECIALISTS = frozenset({"refunds", "engineering", "docs"})

AGENTS = {
    "intake": AgentSpec(
        "intake",
        "You are support intake. Read the ticket and hand it to the ONE "
        "specialist who can resolve it. Do not attempt the work yourself.",
        SPECIALISTS | {"gate"}),

    "refunds": AgentSpec(
        "refunds",
        "You handle billing and refunds. Write the reply to the customer and "
        "record the internal action, then propose the ticket done.",
        frozenset({"gate", "intake"})),

    "engineering": AgentSpec(
        "engineering",
        "You turn a report into an engineering bug write-up: repro steps, "
        "expected vs actual, severity. Then propose the ticket done.",
        frozenset({"gate", "intake"})),

    "docs": AgentSpec(
        "docs",
        "You fix documentation gaps. Write the corrected passage and the reply "
        "pointing the customer at it, then propose the ticket done.",
        frozenset({"gate", "intake"})),

    "gate": AgentSpec(
        "gate",
        "You are the gate. Ratify only if every acceptance criterion has an "
        "artifact behind it. Otherwise reject to whoever did the work.",
        SPECIALISTS | {"intake"}, role=GATE),
}

CRITERIA = ("a reply that can be sent to the customer as written",
            "the internal action recorded for the team")


def charter(ticket: str) -> Charter:
    return Charter(
        brief=f"Resolve this support ticket:\n\n{ticket}",
        entry_agent="intake",
        gate_agent="gate",
        agent_pool=frozenset(AGENTS),
        acceptance_criteria=CRITERIA,
        budget_ceiling_usd=0.25,   # the run halts here, it does not warn
        max_hops=8,
    )


# --- the offline stand-in ----------------------------------------------------
# Not a mock of baton — a mock of the MODEL. Everything below the dispatch seam
# is the real runtime: real parser, real guards, real routing.

def offline(ticket: str):
    keyword = ("refunds" if "charged" in ticket else
               "engineering" if "crash" in ticket else "docs")

    def dispatch(agent, baton, prompt):
        if agent.name == "intake":
            d = Decision(kind=Kind.HANDOFF, to=keyword,
                         goal="resolve this ticket end to end",
                         rationale=f"the ticket is a {keyword} matter")
        elif agent.name == "gate":
            d = Decision(kind=Kind.RATIFY, summary="both criteria evidenced",
                         coverage=("reply.md", "internal_note.md"))
        else:
            d = Decision(kind=Kind.PROPOSE_DONE,
                         summary=f"{agent.name} resolved the ticket",
                         artifacts=(
                             ArtifactRef("reply.md", "customer reply",
                                         content=f"Hello,\n\n{agent.name} has looked at this. " * 12),
                             ArtifactRef("internal_note.md", "internal action",
                                         content=f"Logged by {agent.name}; follow-up owned. " * 12)))
        # `render()` writes the fence baton's own parser reads. No consumer
        # should be hand-assembling this JSON.
        return DispatchResult(text=d.render(preamble="Working the ticket."),
                              cost_usd=0.0, in_tokens=1, out_tokens=1)

    return dispatch


TICKETS = {
    "billing":  "I was charged twice for the October invoice. Please refund one.",
    "bug":      "The exporter crash es every time I select more than 500 rows.",
    "confused": "Where do I set the retry limit? I cannot find it anywhere.",
}


# --- what the runtime refuses -------------------------------------------------
# The routing above is only as good as the model. THIS half is not: these three
# runs are refused by the runtime no matter what the model says, which is why
# `ratified` means something.

def lazy(kind: str):
    """A specialist that cuts a corner, and a gate that waves it through."""
    def block(payload):
        return "```handoff\n" + json.dumps(payload) + "\n```"

    def dispatch(agent, baton, prompt):
        if agent.name == "intake":
            body = {"decision": "HANDOFF", "to": "docs", "goal": "fix it",
                    "rationale": "documentation gap"}
        elif agent.name == "gate":
            # A gate that ratifies whatever it is handed.
            body = {"decision": "RATIFY", "summary": "looks good to me"}
            if kind != "no_coverage":
                body["coverage"] = ["reply.md", "internal_note.md"]
        elif kind == "nothing_built":
            body = {"decision": "PROPOSE_DONE", "summary": "all done!"}
        elif kind == "stubs":
            body = {"decision": "PROPOSE_DONE", "summary": "all done!",
                    "artifacts": [
                        {"path": "reply.md",
                         "description": "a complete customer-ready reply",
                         "content": "TODO"},
                        {"path": "internal_note.md",
                         "description": "full internal action record",
                         "content": "TODO"}]}
        else:  # no_coverage — real work, but the gate never says what it answers
            body = {"decision": "PROPOSE_DONE", "summary": "all done!",
                    "artifacts": [
                        {"path": "reply.md", "description": "reply",
                         "content": "Hello, here is the answer. " * 20}]}
        return DispatchResult(text=block(body), cost_usd=0.0,
                              in_tokens=1, out_tokens=1)
    return dispatch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="real model (costs money)")
    ap.add_argument("--guards", action="store_true",
                    help="show what the runtime refuses to ratify")
    args = ap.parse_args()

    if args.guards:
        for kind in ("nothing_built", "stubs", "no_coverage"):
            r = run(charter(TICKETS["confused"]), AGENTS, lazy(kind),
                    trace=MemoryTrace(kind))
            print(f"{kind:14} {r.terminal_reason}")
            print(f"               {r.note}\n")
        return 0

    if args.live:
        from baton.providers import mistral
        dispatch_for = lambda ticket: mistral.provider()     # noqa: E731
    else:
        dispatch_for = offline

    for name, ticket in TICKETS.items():
        result = run(charter(ticket), AGENTS, dispatch_for(ticket),
                     trace=MemoryTrace(name))
        print(f"{name:9} {result.terminal_reason:12} "
              f"{' -> '.join(result.path):40} ${result.spend_usd:.4f}")
        if result.terminal_reason != "ratified":
            print(f"          {result.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
