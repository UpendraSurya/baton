#!/usr/bin/env python3
"""
A real, small, dynamic run against a LOCAL OpenAI-compatible server — Ollama,
LM Studio, vLLM. Free, no API key, no network beyond localhost.

No gate in this repo runs this: `tests/test_providers_local.py` proves the
request shape against an injected transport, never a real server. This script
is the live half — run it once after pulling a model, so "this works locally"
is a fact, not an inference from the request shape alone.

    ollama pull llama3.1        # or any OpenAI-compatible local model
    ollama serve                # usually already running as a background service
    python3 examples/smoke_local.py
    python3 examples/smoke_local.py --url http://localhost:1234/v1 --model qwen2.5-7b
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from baton import AgentSpec, Charter, run                    # noqa: E402
from baton.providers import openai_compat                    # noqa: E402

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
    ap.add_argument("--url", default="http://localhost:11434/v1",
                    help="OpenAI-compatible base URL (Ollama default shown)")
    ap.add_argument("--model", default="llama3.1")
    ap.add_argument("--api-key", default="not-needed",
                    help="most local servers ignore this; the provider still "
                         "requires SOME value to build the Authorization header")
    args = ap.parse_args()

    # A local model has no real dollar cost. UnmeteredModel exists specifically
    # so a caller cannot forget to say this on purpose — passing an explicit
    # zero rate card here, not omitting the model, is that "on purpose".
    prices = {args.model: {"in": 0.0, "out": 0.0}}

    dispatch = openai_compat.provider(
        api_key=args.api_key, base_url=args.url, model=args.model,
        prices=prices, temperature=0.3)

    print(f"url={args.url}  model={args.model}  ceiling=${CHARTER.budget_ceiling_usd} "
          f"(local model priced at $0 — the ceiling exists for parity, not to bind)")
    print("nobody is told the running order — the agents pick it\n")

    result = run(CHARTER, AGENTS, dispatch)

    print("route chosen by the agents:")
    for i, name in enumerate(result.path, 1):
        print(f"  {i}. {name}")
    print(f"\nterminal_reason : {result.terminal_reason}")
    print(f"hops            : {result.hops}")
    print(f"spend           : ${result.spend_usd:.4f}  (local: expect $0.0000)")
    if result.gate_summary:
        print(f"gate verdict    : {result.gate_summary[:400]}")
    if result.note:
        print(f"note            : {result.note}")
    return 0 if result.terminal_reason == "ratified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
