#!/usr/bin/env python3
"""
A real, small, dynamic run on Gemini. COSTS MONEY (a few cents).

Three agents, none of whom are told the running order — they choose it. The gate
is the only one allowed to end the run.

    export GEMINI_API_KEY=...
    python3 examples/smoke_gemini.py
    python3 examples/smoke_gemini.py --model gemini-3.7-flash --in-price 0.5 --out-price 3
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from baton import AgentSpec, Charter, run                    # noqa: E402
from baton.providers import gemini                           # noqa: E402
from baton.trace import Trace                                # noqa: E402

AGENTS = {
    "researcher": AgentSpec(
        "researcher",
        "You are a researcher. You gather the facts and constraints needed to "
        "write something, and you do not write the final copy yourself.",
        can_hand_to=frozenset({"writer", "editor", "gate"})),
    "writer": AgentSpec(
        "writer",
        "You are a copywriter. You draft short, concrete marketing copy. You do "
        "not edit your own work to final quality.",
        can_hand_to=frozenset({"researcher", "editor", "gate"})),
    "editor": AgentSpec(
        "editor",
        "You are an editor. You tighten copy, cut cliches, and enforce word "
        "limits. You do not invent new facts.",
        can_hand_to=frozenset({"writer", "researcher", "gate"})),
    "gate": AgentSpec(
        "gate",
        "You are the quality gate. You judge the work against the acceptance "
        "criteria and nothing else. Be strict but not obstructive.",
        can_hand_to=frozenset({"researcher", "writer", "editor"}),
        role="gate"),
}

CHARTER = Charter(
    brief=("Write a launch announcement for 'baton', an open-source Python "
           "library that lets AI agents choose which agent runs next instead of "
           "following a fixed graph."),
    entry_agent="researcher",
    gate_agent="gate",
    agent_pool=frozenset(AGENTS),
    acceptance_criteria=(
        "under 120 words",
        "names one concrete thing baton does that a fixed graph cannot",
        "contains no marketing cliches such as 'game-changing' or 'revolutionary'",
    ),
    budget_ceiling_usd=0.25,
    max_hops=8,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=gemini.MODEL)
    ap.add_argument("--in-price", type=float, help="USD per 1M input tokens")
    ap.add_argument("--out-price", type=float, help="USD per 1M output tokens")
    ap.add_argument("--trace", default="")
    args = ap.parse_args()

    prices = None
    if args.in_price is not None and args.out_price is not None:
        prices = dict(gemini.PRICES)
        prices[args.model] = {"in": args.in_price, "out": args.out_price}

    dispatch = gemini.provider(model=args.model, prices=prices, temperature=0.3)
    trace = Trace(args.trace, trace_id="smoke") if args.trace else None

    print(f"model={args.model}  ceiling=${CHARTER.budget_ceiling_usd}  "
          f"max_hops={CHARTER.max_hops}")
    print("nobody is told the running order — the agents pick it\n")

    result = run(CHARTER, AGENTS, dispatch, trace=trace)

    print("route chosen by the agents:")
    for i, name in enumerate(result.path, 1):
        print(f"  {i}. {name}")
    print(f"\nterminal_reason : {result.terminal_reason}")
    print(f"hops            : {result.hops}")
    print(f"spend           : ${result.spend_usd:.4f} "
          f"of ${CHARTER.budget_ceiling_usd:.2f}")
    if result.gate_summary:
        print(f"gate verdict    : {result.gate_summary[:400]}")
    if result.note:
        print(f"note            : {result.note}")
    return 0 if result.terminal_reason == "ratified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
