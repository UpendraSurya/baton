"""The gate is the only role that can end a run — and it cannot loop forever."""
import unittest

from kernel.agent import GATE, AgentSpec
from kernel.charter import Charter
from kernel.runtime import Guards, run
from tests.stub import ScriptedDispatch, propose_done, ratify, reject


def roster():
    return {
        "cto": AgentSpec("cto", "# CTO", frozenset({"frontend_engineer", "gate_agent"})),
        "frontend_engineer": AgentSpec("frontend_engineer", "# FE",
                                       frozenset({"cto", "gate_agent"})),
        "gate_agent": AgentSpec("gate_agent", "# GATE",
                                frozenset({"cto", "frontend_engineer"}), role=GATE),
    }


def charter(**kw):
    base = dict(brief="a gated brief", entry_agent="cto", gate_agent="gate_agent",
                agent_pool=frozenset(roster()),
                acceptance_criteria=("tests exist",),
                budget_ceiling_usd=1000.0, max_hops=100, max_rejects_per_agent=2)
    base.update(kw)
    return Charter(**base)


class RejectBounce(unittest.TestCase):
    def test_a_rejection_sends_the_work_back_and_the_run_continues(self):
        d = ScriptedDispatch({
            "cto": [propose_done("I think it is done"), propose_done("now with tests")],
            "gate_agent": [reject("cto", "no tests"), ratify("tests are there now")]})
        r = run(charter(), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)
        self.assertEqual(("cto", "gate_agent", "cto", "gate_agent"), r.path)

    def test_the_rejected_worker_is_told_the_reason(self):
        d = ScriptedDispatch({
            "cto": [propose_done(), propose_done()],
            "gate_agent": [reject("cto", "criterion 1 has no evidence"), ratify()]})
        run(charter(), roster(), d)
        self.assertIn("criterion 1 has no evidence", d.prompts_for("cto")[1])

    def test_the_gate_may_reject_to_a_different_agent(self):
        d = ScriptedDispatch({
            "cto": [propose_done()],
            "frontend_engineer": [propose_done("fixed it")],
            "gate_agent": [reject("frontend_engineer", "the UI is broken"), ratify()]})
        r = run(charter(), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)
        self.assertIn("frontend_engineer", r.path)


class RejectCap(unittest.TestCase):
    def test_a_third_rejection_of_the_same_agent_ends_the_run(self):
        d = ScriptedDispatch({
            "cto": [propose_done("attempt")],
            "gate_agent": [reject("cto", "still no")]})
        r = run(charter(max_rejects_per_agent=2), roster(), d)
        self.assertEqual("reject_cap_reached", r.terminal_reason)

    def test_two_rejections_are_allowed_before_the_cap_bites(self):
        d = ScriptedDispatch({
            "cto": [propose_done("attempt")],
            "gate_agent": [reject("cto", "no"), reject("cto", "still no"),
                           ratify("third time lucky")]})
        r = run(charter(max_rejects_per_agent=2), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)

    def test_the_cap_is_per_agent_not_per_run(self):
        """Rejecting cto twice and frontend_engineer twice is four rejections
        but breaches nothing — the cap exists to stop one agent being ground
        down, not to cap the gate's total scepticism."""
        d = ScriptedDispatch({
            "cto": [propose_done()],
            "frontend_engineer": [propose_done()],
            "gate_agent": [reject("cto", "1"), reject("frontend_engineer", "2"),
                           reject("cto", "3"), reject("frontend_engineer", "4"),
                           ratify("ok")]})
        r = run(charter(max_rejects_per_agent=2), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)

    def test_rerouting_to_a_different_agent_escapes_the_cap(self):
        d = ScriptedDispatch({
            "cto": [propose_done()],
            "frontend_engineer": [propose_done("I fixed what cto could not")],
            "gate_agent": [reject("cto", "1"), reject("cto", "2"),
                           reject("frontend_engineer", "over to you"), ratify()]})
        r = run(charter(max_rejects_per_agent=2), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)

    def test_the_note_explains_the_cap(self):
        d = ScriptedDispatch({"cto": [propose_done()],
                              "gate_agent": [reject("cto", "no")]})
        r = run(charter(max_rejects_per_agent=2), roster(), d)
        self.assertIn("cto", r.note)
        self.assertIn("2", r.note)


class RejectCapIsLoadBearing(unittest.TestCase):
    def test_without_the_cap_the_reject_loop_runs_to_the_hop_cap(self):
        d = ScriptedDispatch({"cto": [propose_done()],
                              "gate_agent": [reject("cto", "no")]})
        r = run(charter(max_hops=8), roster(), d, guards=Guards(reject_cap=False))
        self.assertEqual("hops_exhausted", r.terminal_reason)
        self.assertEqual(8, r.hops - 1)     # +1 for the forced gate verdict

    def test_with_the_cap_it_stops_far_earlier(self):
        d = ScriptedDispatch({"cto": [propose_done()],
                              "gate_agent": [reject("cto", "no")]})
        r = run(charter(max_hops=8), roster(), d)
        self.assertEqual("reject_cap_reached", r.terminal_reason)
        self.assertLess(r.hops, 8)


if __name__ == "__main__":
    unittest.main()
