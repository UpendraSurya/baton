"""
Adversarial property test: try to break the runtime's core promises.

baton's central claim is that EVERY run ends with a judged outcome from a closed
vocabulary, inside a bounded number of hops, inside a bounded spend. That is the
one thing worth proving rather than asserting, so this generates thousands of
randomised runs against deliberately hostile agents and asserts the invariants
hold every single time.

Hostile means: agents that emit garbage, name agents that do not exist, refuse to
attach evidence, ping-pong forever, gates that never ratify, gates that emit
garbage, dispatches that fail, and rosters with dead ends. Each seed is printed
on failure so any counterexample is reproducible exactly.

This is the test that would catch a regression no example-based test would.
"""
import random
import unittest

from baton.agent import GATE, WORKER, AgentSpec
from baton.charter import Charter
from baton.errors import BatonError, CharterInvalid
from baton.runtime import TERMINAL_REASONS, DispatchResult, Guards, run

# How many randomised runs. Bumped high because each is microseconds — the whole
# suite is still faster than one network call.
ITERATIONS = 4000

WORKER_NAMES = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
GATE_NAME = "gate"


class Hostile:
    """A dispatcher that behaves as badly as the seed tells it to."""

    BEHAVIOURS = (
        "garbage",            # prose, no routing block at all
        "illegal",            # names an agent it may not reach
        "hallucinate",        # names an agent that does not exist anywhere
        "no_evidence",        # PROPOSE_DONE with nothing attached
        "wrong_role",         # worker tries to RATIFY / gate tries to PROPOSE_DONE
        "pingpong",           # hand straight back to the sender
        "truncated",          # a routing block cut in half
        "empty",              # returns nothing at all
        "boom",               # transport failure
        "legal_handoff",      # behaves
        "propose",            # behaves
        "reject_forever",     # gate that never ratifies
    )

    def __init__(self, rng, agents, cost):
        self.rng = rng
        self.agents = agents
        self.cost = cost
        self.calls = 0

    def __call__(self, agent, baton, prompt):
        self.calls += 1
        if self.calls > 5000:                      # the fuzzer's own backstop
            raise AssertionError("dispatcher called 5000 times — runtime did not stop")
        b = self.rng.choice(self.BEHAVIOURS)

        if b == "boom":
            return DispatchResult(text="", error="simulated transport failure")
        if b == "empty":
            return DispatchResult(text="", cost_usd=self.cost)

        text = self._text(b, agent, baton)
        return DispatchResult(text=text, cost_usd=self.cost,
                              in_tokens=len(prompt) // 4, out_tokens=20)

    def _text(self, b, agent, baton):
        legal = sorted(agent.can_hand_to)
        blob = '{"decision": "%s"%s}'
        if b == "garbage":
            return "I did some work and then forgot the routing block entirely."
        if b == "truncated":
            return '```handoff\n{"decision": "HANDOFF", "to": "alpha"'
        if b == "hallucinate":
            return self._blk('{"decision":"HANDOFF","to":"nonexistent_agent","goal":"g"}')
        if b == "illegal":
            outside = [n for n in self.agents if n not in agent.can_hand_to
                       and n != agent.name]
            target = self.rng.choice(outside) if outside else "nobody"
            return self._blk('{"decision":"HANDOFF","to":"%s","goal":"g"}' % target)
        if b == "no_evidence":
            return self._blk('{"decision":"PROPOSE_DONE","summary":"trust me"}')
        if b == "wrong_role":
            verb = "PROPOSE_DONE" if agent.is_gate else "RATIFY"
            return self._blk('{"decision":"%s","summary":"s"}' % verb)
        if b == "pingpong":
            back = baton.from_agent
            if back in agent.can_hand_to:
                return self._blk('{"decision":"HANDOFF","to":"%s","goal":"g"}' % back)
            b = "legal_handoff"
        if b == "reject_forever" and agent.is_gate:
            t = self.rng.choice(legal) if legal else "alpha"
            return self._blk('{"decision":"REJECT","to":"%s","reason":"no"}' % t)
        if agent.is_gate:
            if self.rng.random() < 0.3:
                return self._blk('{"decision":"RATIFY","summary":"fine"}')
            t = self.rng.choice(legal) if legal else "alpha"
            return self._blk('{"decision":"REJECT","to":"%s","reason":"no"}' % t)
        if b == "propose":
            return self._blk('{"decision":"PROPOSE_DONE","summary":"done",'
                             '"artifacts":[{"path":"d.md","content":"work"}]}')
        t = self.rng.choice(legal) if legal else "nobody"
        return self._blk('{"decision":"HANDOFF","to":"%s","goal":"g"}' % t)

    @staticmethod
    def _blk(payload):
        return "prose first\n\n```handoff\n" + payload + "\n```"


def build_case(seed):
    rng = random.Random(seed)
    n = rng.randint(1, len(WORKER_NAMES))
    names = WORKER_NAMES[:n]

    agents = {}
    for name in names:
        peers = [p for p in names if p != name]
        reach = set(rng.sample(peers, rng.randint(0, len(peers))))
        if rng.random() < 0.8:              # sometimes the gate is unreachable
            reach.add(GATE_NAME)
        agents[name] = AgentSpec(name, f"# {name}", frozenset(reach), role=WORKER)
    agents[GATE_NAME] = AgentSpec(GATE_NAME, "# gate", frozenset(names), role=GATE)

    charter = Charter(
        brief="adversarial brief",
        entry_agent=rng.choice(names),
        gate_agent=GATE_NAME,
        agent_pool=frozenset(list(agents)),
        acceptance_criteria=("something must be true",),
        budget_ceiling_usd=rng.choice([0.05, 0.5, 5.0, 100.0]),
        max_hops=rng.randint(1, 15),
        max_rejects_per_agent=rng.randint(1, 3),
        pressure_threshold=rng.choice([0.5, 0.8, 1.0]),
    )
    guards = Guards(
        hops=rng.random() < 0.9,
        budget=rng.random() < 0.9,
        cycle=rng.random() < 0.9,
        reject_cap=rng.random() < 0.9,
        validate_target=True,       # off means illegal targets KeyError by design
        render_legal_moves=rng.random() < 0.9,
    )
    cost = rng.choice([0.0, 0.001, 0.01, 0.1])
    return agents, charter, guards, Hostile(rng, agents, cost), cost


class EveryRunTerminatesJudged(unittest.TestCase):
    def test_thousands_of_hostile_runs_all_terminate_in_the_closed_vocabulary(self):
        stopped_by_backstop = 0
        reasons = {}
        worst_overshoot = 0.0

        for seed in range(ITERATIONS):
            agents, charter, guards, dispatch, cost = build_case(seed)
            try:
                result = run(charter, agents, dispatch, guards=guards)
            except BatonError as exc:
                # The absolute backstop is allowed ONLY when a stop rule was
                # deliberately disabled by the fuzzer. With all guards on it
                # would be a genuine defect.
                if "absolute backstop" in str(exc):
                    self.assertFalse(
                        guards.hops and guards.budget and guards.cycle,
                        f"seed {seed}: backstop hit with every guard ENABLED")
                    stopped_by_backstop += 1
                    continue
                raise AssertionError(f"seed {seed}: {type(exc).__name__}: {exc}")
            except KeyError as exc:
                raise AssertionError(f"seed {seed}: escaped KeyError {exc}")

            self.assertIn(result.terminal_reason, TERMINAL_REASONS,
                          f"seed {seed}: invented a terminal reason")
            reasons[result.terminal_reason] = reasons.get(result.terminal_reason, 0) + 1

            if guards.hops:
                self.assertLessEqual(
                    result.hops, charter.max_hops + 1,
                    f"seed {seed}: {result.hops} hops over cap {charter.max_hops}")

            if guards.budget and cost > 0:
                ceiling = charter.budget_ceiling_usd
                # A hop may spend up to 2 calls (dispatch + one repair) after the
                # ceiling check, and a forced gate verdict may add 2 more.
                allowed = ceiling + 4 * cost
                self.assertLessEqual(
                    result.spend_usd, allowed,
                    f"seed {seed}: spent {result.spend_usd} over ceiling {ceiling}")
                worst_overshoot = max(worst_overshoot,
                                      result.spend_usd / ceiling if ceiling else 0)

            self.assertEqual(len(result.path), result.hops,
                             f"seed {seed}: path and hop count disagree")

        # Report shape so a regression that collapses the outcome space is visible.
        print(f"\n  {ITERATIONS} adversarial runs")
        print(f"  terminal reasons observed: {len(reasons)}/{len(TERMINAL_REASONS)}")
        for r, k in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {r:<22}{k:>5}")
        print(f"  backstop (guards deliberately off): {stopped_by_backstop}")
        print(f"  worst spend/ceiling ratio: {worst_overshoot:.2f}x")

        self.assertGreaterEqual(len(reasons), 5,
                                "the outcome space collapsed — most reasons unreachable")

    def test_with_every_guard_enabled_the_backstop_is_never_reached(self):
        """The strongest single claim: with the defences on, nothing the agents
        can do prevents a judged outcome."""
        for seed in range(ITERATIONS // 2):
            agents, charter, _guards, dispatch, _cost = build_case(seed)
            try:
                result = run(charter, agents, dispatch, guards=Guards())
            except BatonError as exc:
                raise AssertionError(f"seed {seed}: run did not terminate — {exc}")
            self.assertIn(result.terminal_reason, TERMINAL_REASONS)


class CharterValidationIsNotBypassable(unittest.TestCase):
    def test_a_gate_outside_the_pool_is_always_refused(self):
        for seed in range(200):
            agents, charter, guards, dispatch, _ = build_case(seed)
            broken = Charter(
                brief=charter.brief, entry_agent=charter.entry_agent,
                gate_agent="not_in_pool", agent_pool=charter.agent_pool,
                acceptance_criteria=charter.acceptance_criteria)
            with self.assertRaises(CharterInvalid):
                run(broken, agents, dispatch, guards=guards)


if __name__ == "__main__":
    unittest.main()
