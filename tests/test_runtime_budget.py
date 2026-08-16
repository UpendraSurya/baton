"""Killer 2: budget spiral. Escalate at 80%, halt at 100%."""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.contract import PRESSURE_LINE
from baton.errors import BatonError
from baton.runtime import Guards, run
from baton.trace import MemoryTrace
from tests.stub import ScriptedDispatch, garbage, handoff, ratify, reject


def roster():
    return {
        "cto": AgentSpec("cto", "# CTO", frozenset({"frontend_engineer", "gate_agent"})),
        "frontend_engineer": AgentSpec("frontend_engineer", "# FE",
                                       frozenset({"cto", "gate_agent"})),
        "gate_agent": AgentSpec("gate_agent", "# GATE",
                                frozenset({"cto", "frontend_engineer"}), role=GATE),
    }


def charter(**kw):
    base = dict(brief="expensive brief", entry_agent="cto", gate_agent="gate_agent",
                agent_pool=frozenset(roster()),
                acceptance_criteria=("ship something",),
                budget_ceiling_usd=1.00, max_hops=100, pressure_threshold=0.8)
    base.update(kw)
    return Charter(**base)


def ping_pong(cost):
    return ScriptedDispatch({
        "cto": [handoff("frontend_engineer", "keep going")],
        "frontend_engineer": [handoff("cto", "no, you")],
        "gate_agent": [reject("cto", "nothing yet")],
    }, cost_per_call=cost)


class BudgetCeiling(unittest.TestCase):
    def test_run_halts_at_the_ceiling(self):
        r = run(charter(), roster(), ping_pong(0.25), guards=Guards(cycle=False))
        self.assertEqual("budget_exhausted", r.terminal_reason)

    def test_spend_never_exceeds_the_ceiling_by_more_than_one_hop(self):
        r = run(charter(budget_ceiling_usd=1.00), roster(), ping_pong(0.30),
                guards=Guards(cycle=False))
        self.assertLessEqual(r.spend_usd, 1.00 + 0.30 + 1e-9)

    def test_no_extra_gate_call_is_made_after_the_ceiling(self):
        """A forced gate call past the ceiling would spend money the charter
        forbade. Hops can afford a final verdict; budget cannot."""
        d = ping_pong(0.25)
        run(charter(), roster(), d, guards=Guards(cycle=False))
        self.assertEqual(0, d.call_count("gate_agent"))

    def test_the_note_says_what_was_spent(self):
        r = run(charter(), roster(), ping_pong(0.25), guards=Guards(cycle=False))
        self.assertIn("1.00", r.note)


class PressureFlip(unittest.TestCase):
    def test_no_pressure_line_below_the_threshold(self):
        d = ScriptedDispatch({"cto": [handoff("frontend_engineer")],
                              "frontend_engineer": [handoff("gate_agent")],
                              "gate_agent": [ratify()]}, cost_per_call=0.10)
        run(charter(), roster(), d)
        self.assertNotIn(PRESSURE_LINE, d.prompts_for("frontend_engineer")[0])

    def test_pressure_line_appears_once_80_percent_is_crossed(self):
        # ceiling 1.00, threshold 0.8, 0.45/call: after 2 calls spend = 0.90
        d = ScriptedDispatch({"cto": [handoff("frontend_engineer")],
                              "frontend_engineer": [handoff("cto")],
                              "gate_agent": [ratify()]}, cost_per_call=0.45)
        run(charter(), roster(), d, guards=Guards(cycle=False))
        self.assertNotIn(PRESSURE_LINE, d.prompts_for("cto")[0])
        self.assertNotIn(PRESSURE_LINE, d.prompts_for("frontend_engineer")[0])
        self.assertIn(PRESSURE_LINE, d.prompts_for("cto")[1])

    def test_pressure_is_traced_exactly_once(self):
        t = MemoryTrace()
        run(charter(), roster(), ping_pong(0.45), trace=t, guards=Guards(cycle=False))
        self.assertEqual(1, len([r for r in t.records() if r["event"] == "pressure"]))

    def test_repair_attempts_are_charged_too(self):
        """A retry is a real call. Not counting it lets a malformed agent spend
        past the ceiling for free."""
        d = ScriptedDispatch({"cto": [garbage(0), garbage(1)],
                              "gate_agent": [ratify()]}, cost_per_call=0.40)
        r = run(charter(), roster(), d)
        self.assertAlmostEqual(1.20, r.spend_usd, places=6)   # 2 repairs + 1 gate


class BudgetIsLoadBearing(unittest.TestCase):
    def test_without_the_budget_guard_nothing_else_stops_the_spend(self):
        with self.assertRaises(BatonError) as cm:
            run(charter(), roster(), ping_pong(0.25),
                guards=Guards(budget=False, hops=False, cycle=False))
        self.assertIn("stop rule is missing", str(cm.exception))

    def test_with_hops_but_no_budget_the_run_overspends(self):
        """Proves the two guards are NOT redundant: the hop cap stops the run
        but does nothing about a run that burns the ceiling in three hops."""
        r = run(charter(max_hops=10), roster(), ping_pong(0.50),
                guards=Guards(budget=False, cycle=False))
        self.assertEqual("hops_exhausted", r.terminal_reason)
        self.assertGreater(r.spend_usd, 1.00,
                           "budget guard was not the thing that stopped it")


if __name__ == "__main__":
    unittest.main()
