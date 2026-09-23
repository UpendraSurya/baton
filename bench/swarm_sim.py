#!/usr/bin/env python3
"""
bench/swarm_sim.py — TIER 1. Simulated, deterministic, $0. Safe to run anywhere.

A paired, multi-arm benchmark of ROUTER POLICIES through the real runtime. The
model is simulated; the charter, the guards, the parser and the stop rules are
not. It asks the question the trail-aware arm was built for, in the one setting
where it can be answered for free: a gate that KNOWS the right answer.

    python3 bench/swarm_sim.py                     # 5 scenarios x 9 arms, ~30 s
    python3 bench/swarm_sim.py -n 60 --seeds 1     # smoke
    python3 bench/swarm_sim.py --out bench/results/swarm_sim.md

The world
---------
Support tickets have a hidden class. Each class is resolved by exactly one desk,
and even the right desk does acceptable work only `QUALITY` of the time. The
router (intake) does not see the class — it sees a PERCEIVED label, drawn from
a per-scenario confusion matrix, and a prior mapping label -> desk that is what
a model "knows" from the personas. The gate is an oracle: it ratifies only good
work from the right desk; wrong-desk work is rejected back to intake to be
re-routed (when the arm's whitelist allows it), bad work from the right desk is
rejected back to that desk.

The arms differ ONLY in how intake picks a desk
-----------------------------------------------
    static       a fixed DAG: intake -> the modal class's desk -> gate
    oracle       intake sees the true class (the ceiling, not a contender)
    dynamic      perceived label -> prior desk; re-route uniformly
    reputation   dynamic, but re-routes to the desk with the lowest rework rate
    swarm        a Colony per perceived label; ACO rule with the prior as the
                 heuristic term; re-routes by the same rule over untried desks
    swarm-flat   one Colony for everything (no context key)
    swarm-cold   swarm with no prior (beta=0): trails only
    thompson     a Beta(successes, failures) per (label, desk), prior desk
                 seeded with pseudo-successes, discounted so it can follow
                 drift; pick = argmax of one sample from each (a bandit)
    q-learning   tabular Q-learning over states (label, desks already tried):
                 reward +1 for a delivery, minus a cost per desk tried,
                 TD targets bootstrap from the next state; epsilon-greedy

The two learners above read only the trace, exactly as swarm does: which desks
intake handed to, in order, and which one (if any) the run was ratified from.
docs/rl-basics.md walks through both with this file as the running example.

Pairing: ticket i's class, perceived label and every work-quality draw are
seeded by (seed, i), identical in every arm. Only the router's own choices and
their consequences differ, so an exact McNemar test on per-ticket outcomes is
the right comparison — the same one docs/measuring-dynamic-routing.md uses.

What this can and cannot say
----------------------------
It can say whether the trail ARITHMETIC routes better than the alternatives
when routing is a code policy. It cannot say whether a MODEL reading a trail
note in its prompt routes better — that needs real calls (tier 2), and this
file makes no claim about it.
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

from baton import (GATE, AgentSpec, ArtifactRef, Charter, Decision,  # noqa: E402
                   DispatchResult, Guards, Kind, MemoryTrace, reputation, run,
                   swarm)

DESKS = ("refunds", "engineering", "docs", "payments")
CLASSES = ("billing", "bug", "howto")
PRIOR = {"billing": "refunds", "bug": "engineering", "howto": "docs"}
QUALITY = 0.85              # P(the right desk's attempt is acceptable)
HOP_USD = 0.001             # every dispatch costs the same, so $ ~ hops
# intake -> desk -> gate is 3 hops; one wrong desk and a re-route is 6. So a
# run survives ONE misroute and not two. Looser, and every arm that may
# re-route eventually stumbles onto the right desk: the arms agree at the
# ceiling and the comparison carries no information (the window problem in
# docs/measuring-dynamic-routing.md).
MAX_HOPS = 6
# The swarm arm's heuristic term: the prior desk weighs 1, every other desk
# this much. Small = trust the model's reading of the ticket; 1 = no prior
# (the same as swarm-cold). Settable with --prior; see the sweep in
# docs/swarm-simulation.md before reading anything into one value.
PRIOR_OTHERS = 0.2
ARMS = ("static", "oracle", "dynamic", "reputation", "swarm", "swarm-flat",
        "swarm-cold", "thompson", "q-learning")

# Thompson: pseudo-successes given to the prior desk before any evidence, and
# the per-observation discount that lets old counts fade (1.0 = never forget).
TS_PRIOR = 2.0
TS_DISCOUNT = 0.97
# Q-learning: learning rate, exploration rate, discount between re-route steps,
# and the cost charged per desk tried, in units of one delivery.
Q_LR = 0.1
Q_EPSILON = 0.1
Q_GAMMA = 0.9
Q_STEP_COST = 0.1
# The wall-clock guard spawns a thread per dispatch; it bounds real providers
# and cannot fire on an in-process stub. Everything that decides a routing
# outcome — whitelist, reject cap, cycle, hops, budget, coverage — stays on.
GUARDS = Guards().without("wall_clock")


# --- scenarios ----------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    name: str
    blurb: str
    mix: dict                                  # class -> share of tickets
    confusion: dict                            # true class -> {label: p}
    drift_at: float = 2.0                      # share of the stream; >1 = never
    after_drift: dict = field(default_factory=dict)   # class -> new right desk

    def right_desk(self, cls: str, i: int, n: int) -> str:
        if i >= self.drift_at * n and cls in self.after_drift:
            return self.after_drift[cls]
        return PRIOR[cls]


def _noisy(acc: float) -> dict:
    wrong = (1 - acc) / (len(CLASSES) - 1)
    return {c: {lab: (acc if lab == c else wrong) for lab in CLASSES} for c in CLASSES}


SCENARIOS = (
    Scenario("homogeneous",
             "every ticket is billing; perception 85% — a static DAG's home turf",
             {"billing": 1.0}, _noisy(0.85)),
    Scenario("mixed",
             "three classes, equal shares; perception 70%, errors uniform",
             {c: 1 / 3 for c in CLASSES}, _noisy(0.70)),
    Scenario("systematic",
             "three classes; bugs read as how-to 60% of the time, but for every "
             "label the prior desk is still the likeliest right one",
             {c: 1 / 3 for c in CLASSES},
             {"billing": {"billing": 0.9, "bug": 0.05, "howto": 0.05},
              "bug": {"billing": 0.05, "bug": 0.35, "howto": 0.60},
              "howto": {"billing": 0.05, "bug": 0.05, "howto": 0.9}}),
    Scenario("misled",
             "bugs are half the load and 70% of them read as how-to — so for "
             "the 'howto' label the prior desk (docs) is usually WRONG",
             {"billing": 0.2, "bug": 0.5, "howto": 0.3},
             {"billing": {"billing": 0.9, "bug": 0.05, "howto": 0.05},
              "bug": {"billing": 0.05, "bug": 0.25, "howto": 0.70},
              "howto": {"billing": 0.05, "bug": 0.05, "howto": 0.9}}),
    Scenario("drift",
             "mixed at 85% perception; halfway, billing moves from refunds to "
             "payments and nobody updates the prior",
             {c: 1 / 3 for c in CLASSES}, _noisy(0.85),
             drift_at=0.5, after_drift={"billing": "payments"}),
)


def _draw(rng: random.Random, dist: dict) -> str:
    keys = sorted(dist)
    return rng.choices(keys, weights=[dist[k] for k in keys], k=1)[0]


# --- the pool -------------------------------------------------------------------

def agents_for(arm: str, modal_desk: str) -> dict:
    if arm == "static":
        # The drawn graph: intake -> modal desk -> gate, and the gate can only
        # send work back where it came from.
        hand = {"intake": {modal_desk}, **{d: {"gate"} for d in DESKS},
                "gate": {modal_desk}}
    else:
        hand = {"intake": set(DESKS), **{d: {"gate", "intake"} for d in DESKS},
                "gate": set(DESKS) | {"intake"}}
    out = {"intake": AgentSpec("intake", "Route the ticket to one desk.",
                               hand["intake"])}
    for d in DESKS:
        out[d] = AgentSpec(d, f"You are the {d} desk.", hand[d])
    out["gate"] = AgentSpec("gate", "Ratify only a resolved ticket.",
                            hand["gate"], role=GATE)
    return out


def charter() -> Charter:
    return Charter(brief="Resolve the ticket.", entry_agent="intake",
                   gate_agent="gate", agent_pool=frozenset(("intake", "gate") + DESKS),
                   acceptance_criteria=("a reply", "an internal note"),
                   budget_ceiling_usd=0.05, max_hops=MAX_HOPS)


GOOD = (ArtifactRef("reply.md", "reply", content="A resolved reply. " * 10),
        ArtifactRef("note.md", "note", content="An internal note. " * 10))


# --- routers ----------------------------------------------------------------------

def episode(records: list) -> tuple[list, str]:
    """(desks intake handed to, in order; the desk the run was ratified from,
    or "") — read from the trace, never from the simulator's hidden state."""
    picks = [r["to"] for r in records if r.get("event") == "decision"
             and r.get("agent") == "intake" and r.get("decision") == "HANDOFF"]
    edges, reason, _ = swarm.edges_of(records)
    delivered = ""
    if reason == "ratified":
        for src, dst in swarm.loop_free(edges):
            if src == "intake":
                delivered = dst
    return picks, delivered


class Thompson:
    """Beta-Bernoulli Thompson sampling, one arm per (label, desk).

    Belief about desk d for label L: Beta(a, b), where a counts deliveries and
    b counts misroutes. To choose, draw one sample from each desk's Beta and
    take the largest. An uncertain desk has a wide Beta, so it sometimes draws
    high and gets tried — exploration falls out of the uncertainty itself, with
    no epsilon or floor to tune."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.ab: dict[tuple[str, str], list[float]] = {}

    def _belief(self, label: str, desk: str) -> list[float]:
        seed = TS_PRIOR if desk == PRIOR[label] else 0.0
        return self.ab.setdefault((label, desk), [1.0 + seed, 1.0])

    def pick(self, label: str, options: list[str]) -> str:
        draws = {d: self.rng.betavariate(*self._belief(label, d)) for d in options}
        return max(options, key=lambda d: (draws[d], d))

    def learn(self, label: str, records: list) -> None:
        picks, delivered = episode(records)
        # Discount: every belief for this label drifts back toward its prior,
        # so evidence from long ago counts for less (non-stationary bandit).
        for (lab, desk), ab in self.ab.items():
            if lab == label:
                base = 1.0 + (TS_PRIOR if desk == PRIOR[lab] else 0.0)
                ab[0] = base + (ab[0] - base) * TS_DISCOUNT
                ab[1] = 1.0 + (ab[1] - 1.0) * TS_DISCOUNT
        for desk in dict.fromkeys(picks):
            self._belief(label, desk)[0 if desk == delivered else 1] += 1.0


class QRouter:
    """Tabular Q-learning. State = (label, desks already tried); action = the
    next desk. Q(s, a) estimates total future reward from taking a in s.

    After an episode, each step is updated toward its TD target:
        delivered here:   r = 1 - cost                    (terminal)
        run ended here:   r = -cost                       (terminal)
        misroute:         r = -cost + gamma * max_a' Q(s', a')
    so the value of a first pick includes what re-routing from there is worth.
    Updates run last step first, so one episode's delivery reaches its first
    pick immediately rather than over several episodes."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.q: dict[tuple, float] = {}

    def _q(self, label: str, tried: tuple, desk: str) -> float:
        key = (label, frozenset(tried), desk)
        if key not in self.q:
            # The model's reading of the ticket as an initial estimate.
            self.q[key] = 0.5 if desk == PRIOR[label] else 0.0
        return self.q[key]

    def pick(self, label: str, tried: list[str], options: list[str]) -> str:
        if self.rng.random() < Q_EPSILON:
            return self.rng.choice(options)
        vals = {d: self._q(label, tuple(tried), d) for d in options}
        best = max(vals.values())
        return self.rng.choice([d for d in options if vals[d] == best])

    def learn(self, label: str, records: list) -> None:
        picks, delivered = episode(records)
        for i in reversed(range(len(picks))):
            tried, desk = tuple(picks[:i]), picks[i]
            if desk == delivered:
                target = 1.0 - Q_STEP_COST
            elif i == len(picks) - 1:
                target = -Q_STEP_COST
            else:
                nxt = tuple(picks[:i + 1])
                rest = [d for d in DESKS if d not in nxt] or list(DESKS)
                target = -Q_STEP_COST + Q_GAMMA * max(
                    self._q(label, nxt, d) for d in rest)
            key = (label, frozenset(tried), desk)
            self.q[key] = self._q(label, tried, desk) + Q_LR * (
                target - self._q(label, tried, desk))


class Router:
    """How intake picks a desk. `learn` sees every finished run of this arm."""

    def __init__(self, arm: str, modal_desk: str, rng: random.Random):
        self.arm, self.modal, self.rng = arm, modal_desk, rng
        self.colonies: dict[str, swarm.Colony] = {}
        self.history: list[list[dict]] = []
        self.rep = reputation.from_tallies({}, {})
        self.ts = Thompson(rng)
        self.ql = QRouter(rng)

    def _colony(self, label: str) -> swarm.Colony:
        key = "*" if self.arm == "swarm-flat" else label
        return self.colonies.setdefault(key, swarm.Colony())

    def pick(self, label: str, right: str, tried: list[str],
             allowed: frozenset) -> str:
        options = sorted(set(allowed) - set(tried) - {"gate"}) or sorted(
            set(allowed) - {"gate"})
        if self.arm == "static":
            return self.modal
        if self.arm == "oracle":
            return right if right in options else self.rng.choice(options)
        if self.arm == "thompson":
            return self.ts.pick(label, options)
        if self.arm == "q-learning":
            return self.ql.pick(label, tried, options)
        prior = PRIOR[label]
        if self.arm in ("dynamic", "reputation"):
            if not tried and prior in options:
                return prior
            if self.arm == "dynamic":
                return self.rng.choice(options)
            eta = swarm.from_reputation(self.rep, unknown=0.5)
            best = max(eta(d) for d in options)
            return self.rng.choice([d for d in options if eta(d) == best])
        # swarm arms: the ACO rule, prior as visibility unless cold.
        kw = {} if self.arm == "swarm-cold" else {
            "beta": 1.0,
            "heuristic": lambda d: 1.0 if d == prior else PRIOR_OTHERS}
        return self._colony(label).choose("intake", options, self.rng, **kw)

    def learn(self, label: str, records: list[dict]) -> None:
        if self.arm.startswith("swarm"):
            self._colony(label).observe(records)
        elif self.arm == "thompson":
            self.ts.learn(label, records)
        elif self.arm == "q-learning":
            self.ql.learn(label, records)
        elif self.arm == "reputation":
            self.history.append(records)
            if len(self.history) % 10 == 0:
                self.rep = reputation.from_traces(self.history)


def dispatch_for(router: Router, label: str, right: str,
                 world: random.Random, tried: list):
    attempts = {"n": 0}

    def dispatch(agent, baton, prompt):
        if agent.name == "intake":
            d_to = router.pick(label, right, tried, agent.can_hand_to)
            tried.append(d_to)
            d = Decision(kind=Kind.HANDOFF, to=d_to, goal="resolve the ticket",
                         rationale=f"reads as {label}")
        elif agent.name == "gate":
            desk = baton.from_agent
            if desk == right and baton.artifacts and \
                    baton.artifacts[0].description == "good":
                d = Decision(kind=Kind.RATIFY, summary="resolved",
                             coverage=("reply.md", "note.md"))
            elif desk != right and "intake" in agent.can_hand_to:
                d = Decision(kind=Kind.REJECT, to="intake",
                             reason=f"{desk} cannot resolve this; re-route")
            else:
                # Back to the desk. Past the reject cap the runtime has removed
                # it from this whitelist, and an honest gate that still will
                # not ratify ends the run at reject_cap_reached.
                d = Decision(kind=Kind.REJECT, to=desk, reason="not acceptable yet")
        else:
            ok = False
            if agent.name == right:
                attempts["n"] += 1
                ok = world.random() < QUALITY
            tag = "good" if ok else "attempt"
            d = Decision(kind=Kind.PROPOSE_DONE, summary=f"{agent.name} answered",
                         artifacts=tuple(ArtifactRef(a.path, tag, content=a.content)
                                         for a in GOOD))
        return DispatchResult(text=d.render(), cost_usd=HOP_USD,
                              in_tokens=1, out_tokens=1)
    return dispatch


# --- running ------------------------------------------------------------------------

@dataclass
class ArmResult:
    outcomes: list = field(default_factory=list)      # 1 = ratified, per ticket
    hops: list = field(default_factory=list)
    spend: list = field(default_factory=list)
    late: list = field(default_factory=list)          # outcomes, 2nd half only
    first: list = field(default_factory=list)         # 1 = first desk was right
    per_seed: list = field(default_factory=list)      # delivery rate per seed


def run_scenario(sc: Scenario, n: int, seeds: int) -> dict:
    modal = PRIOR[max(sc.mix, key=lambda c: (sc.mix[c], c))]
    results = {arm: ArmResult() for arm in ARMS}
    for seed in range(seeds):
        routers = {arm: Router(arm, modal, random.Random(f"{seed}:{arm}"))
                   for arm in ARMS}
        pools = {arm: agents_for(arm, modal) for arm in ARMS}
        seed_hits = {arm: 0 for arm in ARMS}
        for i in range(n):
            t = random.Random(f"{seed}:{i}:ticket")
            truth = _draw(t, sc.mix)
            label = _draw(t, sc.confusion[truth])
            right = sc.right_desk(truth, i, n)
            for arm in ARMS:
                world = random.Random(f"{seed}:{i}:work")       # paired draws
                tried: list = []
                r = run(charter(), pools[arm],
                        dispatch_for(routers[arm], label, right, world, tried),
                        trace=MemoryTrace(f"{arm}-{seed}-{i}"), guards=GUARDS)
                routers[arm].learn(label, r.trace.records())
                ok = int(r.terminal_reason == "ratified")
                res = results[arm]
                res.outcomes.append(ok)
                res.first.append(int(bool(tried) and tried[0] == right))
                res.hops.append(r.hops)
                res.spend.append(r.spend_usd)
                if i >= n // 2:
                    res.late.append(ok)
                seed_hits[arm] += ok
        for arm in ARMS:
            results[arm].per_seed.append(seed_hits[arm] / n)
    return results


def mcnemar(a: list, b: list) -> tuple[int, int, float]:
    """(a-only, b-only, exact two-sided p) over paired binary outcomes."""
    x = sum(1 for p, q in zip(a, b) if p and not q)
    y = sum(1 for p, q in zip(a, b) if q and not p)
    k, m = min(x, y), x + y
    if m == 0:
        return x, y, 1.0
    tail = sum(math.comb(m, i) for i in range(k + 1)) / 2 ** m
    return x, y, min(1.0, 2 * tail)


def _p(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def report(all_results: dict, n: int, seeds: int, secs: float) -> str:
    lines = [f"# Simulated routing benchmark — {len(ARMS)} arms, "
             f"{len(SCENARIOS)} scenarios",
             "",
             f"{n} tickets x {seeds} seeds per scenario, paired across arms; "
             f"right-desk quality {QUALITY:.0%}; every dispatch ${HOP_USD}; "
             f"max_hops {MAX_HOPS}, reject cap 2; swarm prior weight on "
             f"other desks {PRIOR_OTHERS}. Generated by `bench/swarm_sim.py` "
             f"in {secs:.0f}s, $0.",
             "",
             "`p vs X` is an exact McNemar test on per-ticket outcomes; "
             "`+a/-b` counts tickets only this arm / only X delivered.",
             ""]
    for sc in SCENARIOS:
        res = all_results[sc.name]
        lines += [f"## {sc.name}", "", f"_{sc.blurb}_", "",
                  "| arm | delivered | rate (seed range) | 2nd half | first pick right "
                  "| avg hops | $/delivered | vs static | vs dynamic |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for arm in ARMS:
            r = res[arm]
            tot = len(r.outcomes)
            got = sum(r.outcomes)
            cpd = (f"${sum(r.spend) / got:.4f}" if got else "—")
            lo, hi = min(r.per_seed), max(r.per_seed)
            cells = []
            for base in ("static", "dynamic"):
                if arm == base:
                    cells.append("—")
                    continue
                x, y, p = mcnemar(r.outcomes, res[base].outcomes)
                cells.append(f"+{x}/-{y}, p={_p(p)}")
            lines.append(
                f"| {arm} | {got}/{tot} | {got / tot:.1%} ({lo:.0%}–{hi:.0%}) "
                f"| {sum(r.late) / len(r.late):.1%} "
                f"| {sum(r.first) / tot:.1%} "
                f"| {sum(r.hops) / tot:.2f} | {cpd} | {cells[0]} | {cells[1]} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("-n", type=int, default=300, help="tickets per seed")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path, help="also write the report here")
    ap.add_argument("--prior", type=float, default=PRIOR_OTHERS,
                    help="swarm arm: heuristic weight of non-prior desks, (0, 1]")
    args = ap.parse_args()
    if not 0 < args.prior <= 1:
        ap.error("--prior must be in (0, 1]")
    globals()["PRIOR_OTHERS"] = args.prior
    t0 = time.monotonic()
    all_results = {sc.name: run_scenario(sc, args.n, args.seeds) for sc in SCENARIOS}
    text = report(all_results, args.n, args.seeds, time.monotonic() - t0)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
