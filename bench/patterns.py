#!/usr/bin/env python3
"""
bench/patterns.py — TIER 1. Simulated, deterministic, $0.

Every common agent-routing PATTERN, modelled in one world with one set of
assumptions and one cost ledger, so they can be compared on equal terms.

    python3 bench/patterns.py                 # 9 patterns, ~5 s
    python3 bench/patterns.py --out bench/results/patterns.md

This compares PATTERNS, not libraries. No library is installed or called; each
pattern is a model of the control flow a library ships (docs/patterns-landscape.md
maps pattern -> library, with sources). A library can always be configured
beyond its default pattern — a validator added, a cap raised — and this file
makes no claim about any library's best possible configuration.

The world (identical for every pattern)
---------------------------------------
* A ticket has a hidden class (billing / bug / howto), one right desk each.
* Any model reading a ticket perceives its class correctly with probability
  PERCEPTION; errors are uniform.
* The right desk's attempt is good with probability QUALITY. A bad attempt is a
  STUB (claims to be done, contains nothing) with probability STUB_SHARE, else
  a plausible wrong answer.
* A wrong desk writes a plausible wrong answer, unless it notices the ticket is
  not its own (probability NOTICE) and passes it on.
* Any LLM acting as a judge (supervisor review, speaker selector, critic,
  aggregator, baton's gate) accepts: a good answer 0.95, a plausible wrong
  answer 0.15, a stub 0.30. Lenient on stubs because judges are observed to be
  (docs/measuring-dynamic-routing.md, batch 3).
* Every model call costs 1 unit. Every pattern gets the same budget of
  MAX_CALLS calls per ticket.

The one asymmetry, stated up front
----------------------------------
baton's gate runs MECHANICAL checks before its judge sees anything: a ratified
run must cite real, non-stub artifacts for every criterion
(Guards.require_substance / require_coverage). Here that is modelled as "a stub
never survives baton's gate". That is baton's actual behaviour, not a thumb on
the scale — but other libraries can add equivalent validators (output
guardrails, custom termination conditions), and the comparison is against what
each pattern does with none added.

Metrics
-------
    delivered     the ticket was truly resolved (right desk, good work)
    reported      the system SAID it succeeded
    silent fail   reported success, but not delivered — the costly kind
    calls/ticket  model calls spent
    calls/deliv   model calls per truly resolved ticket (lower is cheaper)
"""
from __future__ import annotations

import argparse
import math
import pathlib
import random
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from baton import swarm  # noqa: E402

CLASSES = ("billing", "bug", "howto")
DESK = {"billing": "refunds", "bug": "engineering", "howto": "docs"}
DESKS = tuple(DESK.values())
PERCEPTION = 0.75
QUALITY = 0.85
STUB_SHARE = 0.5
NOTICE = 0.5
JUDGE = {"good": 0.95, "wrong": 0.15, "stub": 0.30}
MAX_CALLS = 10

PATTERNS = (
    "sequential", "conditional", "concurrent", "round-robin", "group-chat",
    "supervisor", "handoff", "baton", "baton+swarm")


@dataclass
class Ticket:
    """One ticket and its private random streams. Each pattern draws from the
    same named streams in the same order of need, so tickets are paired across
    patterns as closely as their different control flows allow."""

    seed: int
    i: int
    truth: str = ""
    streams: dict = field(default_factory=dict)

    def __post_init__(self):
        r = random.Random(f"{self.seed}:{self.i}:class")
        self.truth = r.choice(CLASSES)

    def rng(self, name: str) -> random.Random:
        if name not in self.streams:
            self.streams[name] = random.Random(f"{self.seed}:{self.i}:{name}")
        return self.streams[name]

    @property
    def right(self) -> str:
        return DESK[self.truth]


@dataclass
class Outcome:
    delivered: bool
    reported: bool
    calls: int


# --- building blocks, shared by every pattern ----------------------------------------

def _uniform(acc: float) -> dict:
    wrong = (1 - acc) / (len(CLASSES) - 1)
    return {c: {x: (acc if x == c else wrong) for x in CLASSES} for c in CLASSES}


# Two worlds. In "uniform" a model's misreads are random; in "systematic" bug
# reports are read as how-to questions 60% of the time — the kind of blind spot
# a model has consistently, and the one a learning router can correct.
WORLDS = {
    "uniform": _uniform(PERCEPTION),
    "systematic": {
        "billing": {"billing": 0.9, "bug": 0.05, "howto": 0.05},
        "bug": {"billing": 0.05, "bug": 0.35, "howto": 0.60},
        "howto": {"billing": 0.05, "bug": 0.05, "howto": 0.9}},
}
CONFUSION = WORLDS["uniform"]


def perceive(t: Ticket, who: str) -> str:
    """A model's reading of the ticket, drawn from the world's confusion matrix."""
    row = CONFUSION[t.truth]
    keys = sorted(row)
    return t.rng(f"perceive:{who}").choices(keys, weights=[row[k] for k in keys])[0]


def attempt(t: Ticket, desk: str) -> str:
    """What one desk call produces: 'good', 'stub', 'wrong', or 'pass' (a wrong
    desk noticed it is not theirs)."""
    if desk == t.right:
        r = t.rng("quality")
        if r.random() < QUALITY:
            return "good"
        return "stub" if r.random() < STUB_SHARE else "wrong"
    return "pass" if t.rng(f"notice:{desk}").random() < NOTICE else "wrong"


def judge(t: Ticket, answer: str) -> bool:
    return t.rng("judge").random() < JUDGE.get(answer, 0.0)


def done(answer: str, reported: bool, calls: int) -> Outcome:
    return Outcome(delivered=(answer == "good" and reported), reported=reported,
                   calls=calls)


# --- the patterns --------------------------------------------------------------------

def sequential(t: Ticket, _state) -> Outcome:
    """A fixed pipeline: every ticket goes to the same desk, and the pipeline's
    last output is the answer. No routing call, no verifier."""
    a = attempt(t, "refunds")
    return done(a, reported=True, calls=1)


def conditional(t: Ticket, _state) -> Outcome:
    """A classifier call picks a branch; the branch runs once. The router is a
    model call; the edges are drawn by hand."""
    label = perceive(t, "classifier")
    a = attempt(t, DESK[label])
    return done(a, reported=True, calls=2)


def concurrent(t: Ticket, _state) -> Outcome:
    """Fan out to every desk at once, then an aggregator call picks an answer.
    Always reports success: some answer is always produced."""
    answers = {d: attempt(t, d) for d in DESKS}
    good = answers[t.right] == "good"
    # The aggregator finds the good answer when there is one with the judge's
    # accuracy; otherwise it returns a plausible wrong one.
    picked = "good" if good and judge(t, "good") else "wrong"
    return done(picked, reported=True, calls=len(DESKS) + 1)


def round_robin(t: Ticket, _state) -> Outcome:
    """Desks speak in a fixed rotation, then a critic decides whether to stop.
    Every round costs every participant."""
    calls, rotation = 0, list(DESKS) + ["critic"]
    while calls + len(rotation) <= MAX_CALLS:
        answers = [attempt(t, d) for d in DESKS]
        calls += len(rotation)
        best = "good" if "good" in answers else (
            "wrong" if "wrong" in answers else "stub")
        if judge(t, best):
            return done(best, reported=True, calls=calls)
    return done("none", reported=False, calls=calls)


def _next(t: Ticket, who: str, believed: str, tried: list, reworked: set,
          last: str, last_answer: str, choose) -> str:
    """The one retry rule every judged pattern shares, so they differ only in
    structure. After a rejection: if the router still believes the desk that
    just answered is the right one, send it back once for rework; otherwise
    try a desk not tried yet (chosen by `choose`)."""
    if last and last_answer != "pass" and last == believed and last not in reworked:
        reworked.add(last)
        return last
    options = [d for d in DESKS if d not in tried] or list(DESKS)
    return choose(options)


def _judged(t: Ticket, *, first_call: int, per_attempt: int, final_call: int,
            mechanical: bool, choose=None, learn=None) -> Outcome:
    """Route -> work -> judge, retrying on rejection within the call budget.

    first_call:  calls spent before the first desk works (a triage/intake call)
    per_attempt: calls per desk attempt, including whatever routes and judges it
    final_call:  calls spent after acceptance (a supervisor's closing review)
    mechanical:  stubs are refused before the judge is asked (baton's guards)
    """
    label = perceive(t, "router")
    believed = DESK[label]
    pick = choose(label) if choose else (lambda opts: t.rng("reroute").choice(opts))
    calls, tried, reworked, edges = first_call, [], set(), []
    desk, last, last_answer = believed, "", ""
    while True:
        if last:
            desk = _next(t, "router", believed, tried, reworked, last, last_answer, pick)
        if calls + per_attempt + final_call > MAX_CALLS:
            out = done("none", reported=False, calls=calls)
            break
        calls += per_attempt
        if desk not in tried:
            tried.append(desk)
        edges.append(("intake" if not edges else "gate", desk))
        a = attempt(t, desk)
        last, last_answer = desk, a
        if a == "pass":
            continue
        if a == "stub" and mechanical:
            continue
        if judge(t, a):
            out = done(a, reported=True, calls=calls + final_call)
            break
    if learn:
        learn(label, edges, desk if out.reported else "", calls)
    return out


def supervisor(t: Ticket, _state) -> Outcome:
    """A supervisor call before every worker call decides who works next and
    reviews what came back; one more call closes the run (agents-as-tools /
    manager / hierarchical)."""
    return _judged(t, first_call=0, per_attempt=2, final_call=1, mechanical=False)


def group_chat(t: Ticket, _state) -> Outcome:
    """A selector call picks each next speaker; the termination check is folded
    into the next selection. Same call shape as a supervisor minus the closing
    call — the real difference, every speaker reading the whole chat, is in
    tokens, which this file does not model."""
    return _judged(t, first_call=0, per_attempt=2, final_call=0, mechanical=False)


def handoff(t: Ticket, _state) -> Outcome:
    """Peer handoff: a triage agent hands off, and the decision rides on each
    agent's own call (a handoff is a tool call). The run ends when an agent
    answers; there is no separate verifier."""
    calls, holder, tried = 1, DESK[perceive(t, "router")], []
    while calls < MAX_CALLS:
        calls += 1
        tried.append(holder)
        a = attempt(t, holder)
        if a != "pass":
            return done(a, reported=True, calls=calls)
        options = [d for d in DESKS if d not in tried] or list(DESKS)
        holder = t.rng("reroute").choice(options)
    return done("none", reported=False, calls=calls)


def baton_blind(t: Ticket, _state) -> Outcome:
    """baton: an intake agent routes once; each desk attempt is the desk's call
    plus the gate's, and the gate's choice of where the work goes next rides on
    its judging call. Only the gate can end the run, and its mechanical checks
    refuse stubs before its judge is asked."""
    return _judged(t, first_call=1, per_attempt=2, final_call=0, mechanical=True)


def baton_swarm(t: Ticket, state) -> Outcome:
    """baton with swarm trails keyed on the perceived label, learning from
    every run through baton.swarm.Colony itself."""
    colonies = state.setdefault("colonies", {})

    def colony(label):
        return colonies.setdefault(label, swarm.Colony())

    def choose(label):
        prior = DESK[label]
        return lambda opts: colony(label).choose(
            "intake", opts, t.rng("route"), beta=1.0,
            heuristic=lambda d: 1.0 if d == prior else 0.2)

    def learn(label, edges, delivered_by, calls):
        recs = [{"event": "run_start", "charter": {"gate_agent": "gate"}}]
        for src, dst in edges:
            recs.append({"event": "decision", "agent": "intake",
                         "decision": "HANDOFF", "to": dst})
            if dst != delivered_by:
                recs.append({"event": "decision", "agent": "gate",
                             "decision": "REJECT", "to": "intake"})
        recs.append({"event": "run_end", "hops": calls,
                     "terminal_reason": "ratified" if delivered_by else "hops_exhausted"})
        colony(label).observe(recs)

    return _judged(t, first_call=1, per_attempt=2, final_call=0, mechanical=True,
                   choose=choose, learn=learn)


RUN = {"sequential": sequential, "conditional": conditional,
       "concurrent": concurrent, "round-robin": round_robin,
       "group-chat": group_chat, "supervisor": supervisor, "handoff": handoff,
       "baton": baton_blind, "baton+swarm": baton_swarm}


# --- running and reporting ------------------------------------------------------------

def run_all(n: int, seeds: int, world: str = "uniform") -> dict:
    global CONFUSION
    CONFUSION = WORLDS[world]
    res = {p: [] for p in PATTERNS}
    for seed in range(seeds):
        state = {p: {} for p in PATTERNS}
        for i in range(n):
            for p in PATTERNS:
                res[p].append(RUN[p](Ticket(seed, i), state[p]))
    return res


def mcnemar(a: list, b: list) -> tuple[int, int, float]:
    x = sum(1 for p, q in zip(a, b) if p and not q)
    y = sum(1 for p, q in zip(a, b) if q and not p)
    k, m = min(x, y), x + y
    if m == 0:
        return x, y, 1.0
    return x, y, min(1.0, 2 * sum(math.comb(m, i) for i in range(k + 1)) / 2 ** m)


def table(res: dict, n: int, seeds: int, world: str) -> list:
    total = n * seeds
    lines = [f"## World: {world}", "", WORLD_BLURB[world], "",
             "| pattern | delivered | reported success | silent failures | "
             "calls/ticket | calls/delivered | delivered vs baton |",
             "|---|---|---|---|---|---|---|"]
    base = [o.delivered for o in res["baton"]]
    for p in PATTERNS:
        outs = res[p]
        d = sum(o.delivered for o in outs)
        rep = sum(o.reported for o in outs)
        silent = sum(o.reported and not o.delivered for o in outs)
        calls = sum(o.calls for o in outs)
        if p == "baton":
            cmp = "—"
        else:
            x, y, pv = mcnemar([o.delivered for o in outs], base)
            cmp = f"+{x}/−{y}, p={'<0.001' if pv < 0.001 else f'{pv:.3f}'}"
        lines.append(
            f"| {p} | {d / total:.1%} | {rep / total:.1%} | "
            f"**{silent / total:.1%}** ({silent / max(rep, 1):.0%} of reported) | "
            f"{calls / total:.2f} | {calls / max(d, 1):.2f} | {cmp} |")
    return lines + [""]


WORLD_BLURB = {
    "uniform": "_Misreads are random (perception 75%, errors spread evenly)._",
    "systematic": "_Bugs are read as how-to questions 60% of the time; other "
                  "classes are read correctly 90% of the time._",
}


def report(results: dict, n: int, seeds: int, secs: float) -> str:
    lines = [
        "# Routing patterns, one world — simulated",
        "",
        f"{n} tickets x {seeds} seeds per world; right-desk "
        f"quality {QUALITY:.0%}, {STUB_SHARE:.0%} of bad attempts are stubs, "
        f"judges accept good/wrong/stub at {JUDGE['good']}/{JUDGE['wrong']}/"
        f"{JUDGE['stub']}, budget {MAX_CALLS} calls. "
        f"Generated by `bench/patterns.py` in {secs:.1f}s, $0.",
        "",
        "**Patterns, not libraries** — see docs/patterns-landscape.md for which "
        "library ships which pattern, and what this cannot show.",
        "",
    ]
    for world, res in results.items():
        lines += table(res, n, seeds, world)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare agent-routing patterns, $0.")
    ap.add_argument("-n", type=int, default=1000, help="tickets per seed")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()
    t0 = time.monotonic()
    results = {w: run_all(args.n, args.seeds, w) for w in WORLDS}
    text = report(results, args.n, args.seeds, time.monotonic() - t0)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
