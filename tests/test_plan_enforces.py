"""The pre-flight estimate has to REFUSE, not merely report.

`plan.py`'s docstring said the fix was "refusing to start a run whose budget was
never survivable". It wasn't: `estimate()` returned an `Estimate` with a
`feasible` flag, never raised, and `run()` never called it. The promise was in
prose and the enforcement was left to a caller who had no reason to know.

A charter whose ceiling is breached on hop 1 is a configuration error, not a run
outcome — so it raises before anything is dispatched, exactly like the other
CharterInvalid checks, rather than spending a call to discover it.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.errors import CharterInvalid
from baton.plan import estimate
from baton.runtime import DispatchResult, Guards, run
from baton.trace import MemoryTrace

BODY = "The deliverable, in full. " * 5
DONE = ('```handoff\n{"decision": "PROPOSE_DONE", "summary": "d", "artifacts": '
        '[{"path": "out.md", "description": "d", "content": "%s"}]}\n```' % BODY)
RATIFY = ('```handoff\n{"decision": "RATIFY", "summary": "ok", '
          '"coverage": ["out.md"]}\n```')


def roster():
    return {"w": AgentSpec("w", "# W", frozenset({"g"})),
            "g": AgentSpec("g", "# G", frozenset({"w"}), role=GATE)}


def charter(**kw):
    base = dict(brief="b", entry_agent="w", gate_agent="g",
                agent_pool=frozenset(roster()), acceptance_criteria=("x",),
                budget_ceiling_usd=1.0, max_hops=6)
    base.update(kw)
    return Charter(**base)


class Scripted:
    def __init__(self):
        self.calls = 0
        self.lines = [DONE, RATIFY]

    def __call__(self, agent, baton, prompt):
        self.calls += 1
        return DispatchResult(text=self.lines.pop(0), cost_usd=0.0,
                              in_tokens=1, out_tokens=1)


class TheEstimateRefuses(unittest.TestCase):
    def test_an_infeasible_charter_raises_rather_than_reporting(self):
        est = estimate(charter(budget_ceiling_usd=0.01), roster(),
                       usd_per_call=5.0)
        self.assertFalse(est.feasible)
        with self.assertRaises(CharterInvalid):
            est.raise_if_infeasible()

    def test_the_refusal_says_which_problem(self):
        est = estimate(charter(budget_ceiling_usd=0.01), roster(),
                       usd_per_call=5.0)
        with self.assertRaises(CharterInvalid) as cm:
            est.raise_if_infeasible()
        self.assertIn("hop 1", str(cm.exception))

    def test_a_feasible_charter_passes_through(self):
        est = estimate(charter(budget_ceiling_usd=100.0), roster(),
                       usd_per_call=0.01)
        self.assertIsNone(est.raise_if_infeasible())


class RunPreflights(unittest.TestCase):
    """The enforcement has to be where the money is spent."""

    def test_run_refuses_an_unsurvivable_charter_before_spending(self):
        d = Scripted()
        with self.assertRaises(CharterInvalid):
            run(charter(budget_ceiling_usd=0.01), roster(), d,
                trace=MemoryTrace("t"), usd_per_call=5.0)
        self.assertEqual(0, d.calls, "it dispatched before checking the budget")

    def test_run_is_unchanged_when_no_cost_is_declared(self):
        # Without usd_per_call the runtime cannot know what a call costs, and
        # must not invent a number to refuse on.
        r = run(charter(), roster(), Scripted(), trace=MemoryTrace("t"))
        self.assertEqual("ratified", r.terminal_reason)

    def test_a_survivable_charter_still_runs(self):
        r = run(charter(budget_ceiling_usd=100.0), roster(), Scripted(),
                trace=MemoryTrace("t"), usd_per_call=0.01)
        self.assertEqual("ratified", r.terminal_reason)

    def test_the_preflight_switches_off_alone(self):
        r = run(charter(budget_ceiling_usd=0.01), roster(), Scripted(),
                trace=MemoryTrace("t"), usd_per_call=5.0,
                guards=Guards(preflight=False))
        self.assertEqual("ratified", r.terminal_reason)


if __name__ == "__main__":
    unittest.main()
