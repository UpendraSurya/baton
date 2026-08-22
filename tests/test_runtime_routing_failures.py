"""
Turning free text into a route is where dynamic systems break. Two attempts,
then the gate judges what exists — never a third blind retry.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.runtime import Guards, run
from baton.trace import MemoryTrace
from tests.stub import (ScriptedDispatch, garbage, handoff, propose_done,
                        prose_then, ratify)


def roster():
    return {
        "ceo": AgentSpec("ceo", "# CEO", frozenset({"cto", "gate_agent"})),
        "cto": AgentSpec("cto", "# CTO", frozenset({"frontend_engineer", "gate_agent"})),
        "frontend_engineer": AgentSpec("frontend_engineer", "# FE",
                                       frozenset({"cto", "gate_agent"})),
        "gate_agent": AgentSpec("gate_agent", "# GATE",
                                frozenset({"ceo", "cto", "frontend_engineer"}),
                                role=GATE),
    }


def charter(**kw):
    base = dict(brief="Build a docs Q&A bot", entry_agent="ceo",
                gate_agent="gate_agent", agent_pool=frozenset(roster()),
                acceptance_criteria=("answers cite sources",),
                budget_ceiling_usd=10.0, max_hops=20)
    base.update(kw)
    return Charter(**base)


class MalformedOutput(unittest.TestCase):
    def test_a_missing_block_gets_exactly_one_repair_retry(self):
        d = ScriptedDispatch({"ceo": [garbage(0), prose_then(handoff("cto"))],
                              "cto": [propose_done()], "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertEqual(2, d.call_count("ceo"), "expected exactly one repair retry")
        self.assertEqual("ratified", r.terminal_reason)

    def test_the_repair_nudge_is_terse_and_does_not_resend_the_baton(self):
        d = ScriptedDispatch({"ceo": [garbage(0), prose_then(handoff("cto"))],
                              "cto": [propose_done()], "gate_agent": [ratify()]})
        run(charter(), roster(), d)
        first, second = d.prompts_for("ceo")
        self.assertIn("could not be routed", second)
        self.assertLess(len(second) - len(first), 600,
                        "the retry re-sent far more than a nudge")

    def test_two_malformed_outputs_force_route_to_the_gate(self):
        """The ROUTING is the assertion here: two bad attempts, then the gate.

        This scenario used to assert "ratified", which is how the empty-ratify
        bug hid in a green suite for so long — the worker produced nothing at
        all, and a gate willing to approve it made the run look successful.
        Observed live on 2026-08-21. The routing is still correct; the outcome
        is not a delivery."""
        d = ScriptedDispatch({"ceo": [garbage(0), garbage(1)],
                              "gate_agent": [ratify("assessed what exists")]})
        r = run(charter(), roster(), d)
        self.assertEqual(2, d.call_count("ceo"), "a third attempt was made")
        self.assertEqual(("ceo", "gate_agent"), r.path)
        self.assertEqual("ratified_without_deliverable", r.terminal_reason)

    def test_the_gate_is_told_the_routing_was_malformed(self):
        d = ScriptedDispatch({"ceo": [garbage(0), garbage(1)],
                              "gate_agent": [ratify()]})
        run(charter(), roster(), d)
        self.assertIn("malformed_routing", d.prompts_for("gate_agent")[0])

    def test_a_gate_that_cannot_be_routed_twice_ends_the_run(self):
        """There is nobody above the gate to escalate to."""
        d = ScriptedDispatch({"ceo": [propose_done()],
                              "gate_agent": [garbage(0), garbage(1)]})
        r = run(charter(), roster(), d)
        self.assertEqual("charter_violation", r.terminal_reason)
        self.assertIn("gate", r.note)


class IllegalTargets(unittest.TestCase):
    def test_an_illegal_target_is_retried_once_with_the_legal_list_echoed(self):
        d = ScriptedDispatch({
            "ceo": [handoff("frontend_engineer", "skip the cto"),   # not on ceo's list
                    handoff("cto", "fine, the cto")],
            "cto": [propose_done()], "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertEqual(2, d.call_count("ceo"))
        self.assertIn("cto", d.prompts_for("ceo")[1])
        self.assertEqual(("ceo", "cto", "gate_agent"), r.path)

    def test_a_hallucinated_agent_never_gets_dispatched(self):
        d = ScriptedDispatch({"ceo": [handoff("senior_vibe_officer"),
                                      handoff("senior_vibe_officer")],
                              "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertNotIn("senior_vibe_officer", r.path)
        self.assertEqual(("ceo", "gate_agent"), r.path)

    def test_an_agent_outside_the_charter_pool_is_illegal_even_if_whitelisted(self):
        small = charter(agent_pool=frozenset({"ceo", "gate_agent"}))
        d = ScriptedDispatch({"ceo": [handoff("cto"), handoff("cto")],
                              "gate_agent": [ratify()]})
        r = run(small, roster(), d)
        self.assertNotIn("cto", r.path)

    def test_a_worker_claiming_ratify_is_a_role_violation_not_a_termination(self):
        d = ScriptedDispatch({"ceo": [ratify("I declare myself finished"),
                                      handoff("cto")],
                              "cto": [propose_done()],
                              "gate_agent": [ratify("the gate's own words")]})
        r = run(charter(), roster(), d)
        self.assertEqual(("ceo", "cto", "gate_agent"), r.path)
        self.assertEqual("ratified", r.terminal_reason)
        self.assertEqual("the gate's own words", r.gate_summary)


class LadderIsLoadBearing(unittest.TestCase):
    """Mutation checks. Remove the guard, the behaviour must change."""

    def test_without_target_validation_the_hallucinated_agent_would_be_dispatched(self):
        d = ScriptedDispatch({"ceo": [handoff("senior_vibe_officer")],
                              "gate_agent": [ratify()]})
        with self.assertRaises(KeyError) as cm:
            run(charter(), roster(), d, guards=Guards(validate_target=False))
        self.assertIn("senior_vibe_officer", str(cm.exception))

    def test_repair_ladder_is_traced(self):
        t = MemoryTrace()
        d = ScriptedDispatch({"ceo": [garbage(0), prose_then(handoff("cto"))],
                              "cto": [propose_done()], "gate_agent": [ratify()]})
        run(charter(), roster(), d, trace=t)
        repairs = [r for r in t.records() if r["event"] == "repair"]
        self.assertEqual(1, len(repairs))
        self.assertEqual("ceo", repairs[0]["agent"])


if __name__ == "__main__":
    unittest.main()
