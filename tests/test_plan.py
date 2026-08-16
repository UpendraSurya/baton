"""
Pre-flight: catch an unsurvivable budget before spending, not after.

Justified empirically — tests/test_adversarial.py measured spend/ceiling ratios
up to 8x on charters whose ceiling could not cover one call. No runtime check
fixes that, because cost is only known after the call returns. Refusing to start
does fix it.
"""
import unittest

from baton import plan
from baton.agent import GATE, AgentSpec
from baton.charter import Charter


def roster(**kw):
    a = {
        "alpha": AgentSpec("alpha", "# a", frozenset({"bravo", "gate"})),
        "bravo": AgentSpec("bravo", "# b", frozenset({"alpha", "gate"})),
        "orphan": AgentSpec("orphan", "# o", frozenset({"gate"})),
        "gate": AgentSpec("gate", "# g", frozenset({"alpha", "bravo"}), role=GATE),
    }
    a.update(kw)
    return a


def charter(**kw):
    base = dict(brief="b", entry_agent="alpha", gate_agent="gate",
                agent_pool=frozenset(roster()), acceptance_criteria=("x",),
                budget_ceiling_usd=1.00, max_hops=6)
    base.update(kw)
    return Charter(**base)


class WorstCase(unittest.TestCase):
    def test_worst_case_accounts_for_the_repair_retry_and_forced_gate(self):
        e = plan.estimate(charter(max_hops=6), roster(), usd_per_call=0.01)
        self.assertEqual(7, e.worst_case_hops)      # 6 + the forced gate verdict
        self.assertEqual(14, e.worst_case_calls)    # each hop may repair once
        self.assertAlmostEqual(0.14, e.worst_case_usd, places=6)

    def test_a_free_estimate_needs_no_price(self):
        e = plan.estimate(charter(), roster())
        self.assertEqual(0.0, e.worst_case_usd)
        self.assertTrue(e.feasible)


class UnsurvivableBudgetsAreRefusedUpFront(unittest.TestCase):
    def test_a_call_costing_more_than_the_whole_ceiling_is_a_problem(self):
        """The exact shape the fuzzer found overshooting 8x."""
        e = plan.estimate(charter(budget_ceiling_usd=0.05), roster(),
                          usd_per_call=0.10)
        self.assertFalse(e.feasible)
        self.assertIn("breached on hop 1", " ".join(e.problems))

    def test_one_hop_with_a_retry_exceeding_the_ceiling_is_a_problem(self):
        e = plan.estimate(charter(budget_ceiling_usd=0.15), roster(),
                          usd_per_call=0.10)
        self.assertFalse(e.feasible)
        self.assertIn("checked between hops", " ".join(e.problems))

    def test_a_survivable_ceiling_is_feasible_even_if_it_halts_early(self):
        e = plan.estimate(charter(budget_ceiling_usd=0.50), roster(),
                          usd_per_call=0.01)
        self.assertTrue(e.feasible)

    def test_a_worst_case_over_the_ceiling_warns_but_stays_feasible(self):
        """Halting early on budget is correct behaviour, not a misconfiguration."""
        e = plan.estimate(charter(budget_ceiling_usd=0.05, max_hops=20),
                          roster(), usd_per_call=0.002)
        self.assertTrue(e.feasible)
        self.assertIn("halt early", " ".join(e.warnings))


class GraphProblemsFoundOffline(unittest.TestCase):
    def test_an_unreachable_agent_is_reported(self):
        """Staffed, paid for in the roster, and the run can never arrive."""
        e = plan.estimate(charter(), roster())
        self.assertIn("orphan", e.unreachable)
        self.assertIn("unreachable", " ".join(e.warnings))

    def test_a_dead_end_agent_is_reported(self):
        agents = roster(cul_de_sac=AgentSpec("cul_de_sac", "# c", frozenset()))
        ch = charter(agent_pool=frozenset(agents))
        e = plan.estimate(ch, agents)
        self.assertIn("cul_de_sac", e.dead_ends)

    def test_a_healthy_charter_reports_nothing(self):
        agents = {k: v for k, v in roster().items() if k != "orphan"}
        e = plan.estimate(charter(agent_pool=frozenset(agents)), agents)
        self.assertEqual((), e.problems)
        self.assertEqual((), e.unreachable)

    def test_render_is_human_readable(self):
        text = plan.estimate(charter(budget_ceiling_usd=0.05), roster(),
                             usd_per_call=0.10).render()
        self.assertIn("feasible   : NO", text)


if __name__ == "__main__":
    unittest.main()
