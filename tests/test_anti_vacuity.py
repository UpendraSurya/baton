"""
Each guard, deleted alone.

Two defences against the same failure cover for each other, and the suite goes
green while neither is actually doing anything. The only way to know is to remove
one at a time and watch the behaviour change. See dev-notes:
feedback_overlapping_guards_vacuous_tests.

Every test here asserts a DIFFERENCE between guarded and unguarded behaviour. A
test that passes identically in both configurations proves nothing and does not
belong in this file.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.errors import BatonError
from baton.runtime import Guards, run
from tests.stub import (ScriptedDispatch, follow_contract, handoff,
                        propose_done, ratify, reject)


def roster():
    return {
        "cto": AgentSpec("cto", "# CTO", frozenset({"frontend_engineer", "gate_agent"})),
        "frontend_engineer": AgentSpec("frontend_engineer", "# FE",
                                       frozenset({"cto", "gate_agent"})),
        "gate_agent": AgentSpec("gate_agent", "# GATE",
                                frozenset({"cto", "frontend_engineer"}), role=GATE),
    }


def charter(**kw):
    base = dict(brief="a brief", entry_agent="cto", gate_agent="gate_agent",
                agent_pool=frozenset(roster()),
                acceptance_criteria=("ship something",),
                budget_ceiling_usd=1.00, max_hops=6)
    base.update(kw)
    return Charter(**base)


class GuardA_RenderedWhitelist(unittest.TestCase):
    """Guard A: the legal moves are rendered INTO the prompt.

    The stub here is prompt-sensitive on purpose: follow_contract() picks the
    first target the prompt offers, and invents a name when offered none —
    exactly what a real model does."""

    def test_with_the_whitelist_rendered_the_agent_routes_legally(self):
        d = ScriptedDispatch({"cto": [follow_contract()],
                              "frontend_engineer": [propose_done()],
                              "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)
        self.assertIn("frontend_engineer", r.path)

    def test_deleting_guard_A_alone_makes_the_agent_hallucinate_a_target(self):
        d = ScriptedDispatch({"cto": [follow_contract()],
                              "frontend_engineer": [propose_done()],
                              "gate_agent": [ratify("assessed what exists")]})
        r = run(charter(), roster(), d, guards=Guards(render_legal_moves=False))
        # Guard B catches the hallucination, so the run survives — but it has
        # been diverted to the gate and the intended agent never ran.
        self.assertNotIn("frontend_engineer", r.path)
        self.assertEqual(("cto", "gate_agent"), r.path)

    def test_guard_A_alone_is_not_sufficient(self):
        """Rendering is advice, not enforcement. A model that ignores the list
        must still be stopped — which is guard B's whole job."""
        d = ScriptedDispatch({"cto": [handoff("senior_vibe_officer")],
                              "gate_agent": [ratify()]})
        with self.assertRaises(KeyError):
            run(charter(), roster(), d,
                guards=Guards(render_legal_moves=True, validate_target=False))


class GuardB_PostParseValidation(unittest.TestCase):
    """Guard B: the target is validated AFTER parsing."""

    def test_with_validation_an_illegal_target_never_gets_dispatched(self):
        d = ScriptedDispatch({"cto": [handoff("senior_vibe_officer"),
                                      handoff("senior_vibe_officer")],
                              "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertNotIn("senior_vibe_officer", r.path)

    def test_deleting_guard_B_alone_lets_a_hallucinated_agent_through(self):
        d = ScriptedDispatch({"cto": [handoff("senior_vibe_officer")],
                              "gate_agent": [ratify()]})
        with self.assertRaises(KeyError) as cm:
            run(charter(), roster(), d, guards=Guards(validate_target=False))
        self.assertIn("senior_vibe_officer", str(cm.exception))

    def test_guard_B_still_enforces_roles_when_target_checking_is_off(self):
        """The two must not be one switch. A worker declaring itself finished is
        a ROLE failure, and turning off target validation must not excuse it."""
        d = ScriptedDispatch({"cto": [ratify("I grade myself a pass"),
                                      propose_done()],
                              "gate_agent": [ratify()]})
        r = run(charter(), roster(), d, guards=Guards(validate_target=False))
        self.assertEqual("ratified", r.terminal_reason)
        self.assertEqual("gate_agent", r.path[-1], "a worker ended the run")


class ThreeKillersAreNotRedundant(unittest.TestCase):
    """Hops, budget and the cycle detector each catch something the other two
    do not. Any one alone is insufficient — that is why all three exist."""

    def _forever(self, cost=0.0):
        return ScriptedDispatch({
            "cto": [handoff("frontend_engineer")],
            "frontend_engineer": [handoff("cto")],
            "gate_agent": [reject("cto", "nothing yet")]},
            cost_per_call=cost)

    def test_all_three_deleted_hits_the_absolute_backstop(self):
        with self.assertRaises(BatonError):
            run(charter(), roster(), self._forever(),
                guards=Guards(hops=False, budget=False, cycle=False))

    def test_hops_alone_stops_a_free_infinite_loop(self):
        r = run(charter(max_hops=6), roster(), self._forever(0.0),
                guards=Guards(budget=False, cycle=False))
        self.assertEqual("hops_exhausted", r.terminal_reason)

    def test_budget_alone_stops_an_expensive_loop_that_hops_would_not(self):
        r = run(charter(max_hops=1000, budget_ceiling_usd=1.0), roster(),
                self._forever(0.30), guards=Guards(hops=False, cycle=False))
        self.assertEqual("budget_exhausted", r.terminal_reason)

    def test_cycle_alone_stops_a_loop_the_others_would_run_to_the_end_of(self):
        r = run(charter(max_hops=1000, budget_ceiling_usd=1000.0), roster(),
                self._forever(0.0), guards=Guards(hops=False, budget=False))
        self.assertEqual("stalled", r.terminal_reason)
        self.assertLess(r.hops, 20)

    def test_the_cycle_detector_is_the_only_one_that_reacts_early(self):
        """Hops and budget are ceilings; only the detector notices the loop
        while there is still budget left to do something about it."""
        with_cycle = run(charter(max_hops=1000, budget_ceiling_usd=1000.0),
                         roster(), self._forever(0.01))
        without = run(charter(max_hops=30, budget_ceiling_usd=1000.0), roster(),
                      self._forever(0.01), guards=Guards(cycle=False))
        self.assertLess(with_cycle.hops, without.hops)
        self.assertLess(with_cycle.spend_usd, without.spend_usd)


class GateIsNotOptional(unittest.TestCase):
    def test_no_worker_verb_can_terminate_a_run(self):
        """Every terminal reason is produced by the runtime or the gate. If a
        worker could end a run, the model that did the work would grade it."""
        d = ScriptedDispatch({"cto": [propose_done("shipping it")],
                              "gate_agent": [reject("cto", "no"), ratify()]})
        r = run(charter(max_hops=10), roster(), d)
        self.assertEqual("gate_agent", r.path[-1])
        self.assertEqual("ratified", r.terminal_reason)


if __name__ == "__main__":
    unittest.main()
