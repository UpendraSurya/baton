"""Killer 1: never-done. The hop cap always produces a JUDGED outcome."""
import unittest

from kernel.agent import GATE, AgentSpec
from kernel.charter import Charter
from kernel.errors import BatonError
from kernel.runtime import Guards, run
from kernel.trace import MemoryTrace
from tests.stub import ScriptedDispatch, handoff, ratify, reject


def roster():
    return {
        "cto": AgentSpec("cto", "# CTO", frozenset({"frontend_engineer", "gate_agent"})),
        "frontend_engineer": AgentSpec("frontend_engineer", "# FE",
                                       frozenset({"cto", "gate_agent"})),
        "gate_agent": AgentSpec("gate_agent", "# GATE",
                                frozenset({"cto", "frontend_engineer"}), role=GATE),
    }


def charter(**kw):
    base = dict(brief="never-ending brief", entry_agent="cto",
                gate_agent="gate_agent", agent_pool=frozenset(roster()),
                acceptance_criteria=("ship something",),
                budget_ceiling_usd=1000.0, max_hops=4)
    base.update(kw)
    return Charter(**base)


def never_finishes():
    """Two agents that hand to each other forever and never propose done."""
    return ScriptedDispatch({
        "cto": [handoff("frontend_engineer", "keep going")],
        "frontend_engineer": [handoff("cto", "no, you")],
        "gate_agent": [reject("cto", "nothing exists yet")],
    })


class HopCap(unittest.TestCase):
    def test_run_halts_at_max_hops(self):
        r = run(charter(max_hops=4), roster(), never_finishes(),
                guards=Guards(cycle=False))
        self.assertEqual("hops_exhausted", r.terminal_reason)

    def test_exactly_max_hops_worker_calls_happen_before_the_forced_gate(self):
        d = never_finishes()
        r = run(charter(max_hops=4), roster(), d, guards=Guards(cycle=False))
        worker_calls = d.call_count("cto") + d.call_count("frontend_engineer")
        self.assertEqual(4, worker_calls)
        self.assertEqual("gate_agent", r.path[-1], "the gate never judged")

    def test_the_gate_is_told_it_is_out_of_hops(self):
        d = never_finishes()
        run(charter(max_hops=2), roster(), d, guards=Guards(cycle=False))
        gate_prompt = d.prompts_for("gate_agent")[0]
        self.assertIn("hops_exhausted", gate_prompt)
        self.assertIn("assess what exists", gate_prompt.lower())

    def test_a_forced_gate_that_ratifies_yields_ratified_not_hops_exhausted(self):
        d = ScriptedDispatch({
            "cto": [handoff("frontend_engineer", "keep going")],
            "frontend_engineer": [handoff("cto", "no, you")],
            "gate_agent": [ratify("what exists already meets the bar")]})
        r = run(charter(max_hops=2), roster(), d, guards=Guards(cycle=False))
        self.assertEqual("ratified", r.terminal_reason)
        self.assertIn("forced gate", r.note)

    def test_max_hops_of_one_still_gets_a_verdict(self):
        d = never_finishes()
        r = run(charter(max_hops=1), roster(), d, guards=Guards(cycle=False))
        self.assertEqual(("cto", "gate_agent"), r.path)
        self.assertEqual("hops_exhausted", r.terminal_reason)

    def test_run_end_is_traced_once(self):
        t = MemoryTrace()
        run(charter(max_hops=3), roster(), never_finishes(), trace=t,
            guards=Guards(cycle=False))
        ends = [r for r in t.records() if r["event"] == "run_end"]
        self.assertEqual(1, len(ends))
        self.assertEqual("hops_exhausted", ends[0]["terminal_reason"])


class HopCapIsLoadBearing(unittest.TestCase):
    def test_without_the_hop_cap_the_run_hits_the_absolute_backstop(self):
        """Deleting this guard alone must be visible. If some other guard were
        quietly stopping the run, this would pass and the hop cap would be
        untested — that is exactly the overlapping-guards trap."""
        with self.assertRaises(BatonError) as cm:
            run(charter(max_hops=4), roster(), never_finishes(),
                guards=Guards(hops=False, cycle=False, budget=False))
        self.assertIn("stop rule is missing", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
