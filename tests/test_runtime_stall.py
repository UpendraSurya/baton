"""Killer 3: ping-pong. Escalate first, terminate second."""
import unittest

from kernel.agent import GATE, AgentSpec
from kernel.charter import Charter
from kernel.runtime import Guards, run
from kernel.trace import MemoryTrace
from tests.stub import ScriptedDispatch, handoff, propose_done, ratify, reject


def roster():
    return {
        "cto": AgentSpec("cto", "# CTO", frozenset({"frontend_engineer", "gate_agent"})),
        "frontend_engineer": AgentSpec("frontend_engineer", "# FE",
                                       frozenset({"cto", "gate_agent"})),
        "gate_agent": AgentSpec("gate_agent", "# GATE",
                                frozenset({"cto", "frontend_engineer"}), role=GATE),
    }


def charter(**kw):
    base = dict(brief="loopy brief", entry_agent="cto", gate_agent="gate_agent",
                agent_pool=frozenset(roster()),
                acceptance_criteria=("ship something",),
                budget_ceiling_usd=1000.0, max_hops=100)
    base.update(kw)
    return Charter(**base)


def ping_pong(gate_response=None):
    return ScriptedDispatch({
        "cto": [handoff("frontend_engineer", "you do it")],
        "frontend_engineer": [handoff("cto", "no, you")],
        "gate_agent": [gate_response or reject("cto", "still nothing")],
    })


class CycleDetector(unittest.TestCase):
    def test_the_second_occurrence_injects_a_stall_notice_not_a_kill(self):
        d = ping_pong()
        run(charter(max_hops=12), roster(), d)
        # cto -> frontend_engineer happens on hops 1 and 3; the notice rides the
        # baton that arrives at frontend_engineer on hop 4.
        fe_prompts = d.prompts_for("frontend_engineer")
        self.assertNotIn("Stall notice", fe_prompts[0])
        self.assertIn("Stall notice", fe_prompts[1])

    def test_the_stall_notice_names_the_loop(self):
        d = ping_pong()
        run(charter(max_hops=12), roster(), d)
        self.assertIn("cto", d.prompts_for("frontend_engineer")[1])

    def test_the_third_occurrence_force_routes_to_the_gate(self):
        d = ping_pong()
        r = run(charter(max_hops=30), roster(), d)
        self.assertIn("gate_agent", r.path)
        gate_prompt = d.prompts_for("gate_agent")[0]
        self.assertIn("stalled_loop", gate_prompt)
        self.assertIn("looping", gate_prompt)

    def test_a_gate_that_breaks_the_tie_lets_the_run_continue(self):
        d = ScriptedDispatch({
            "cto": [handoff("frontend_engineer"), handoff("frontend_engineer"),
                    handoff("frontend_engineer"), propose_done("fine, done")],
            "frontend_engineer": [handoff("cto")],
            "gate_agent": [reject("cto", "stop looping and finish it"),
                           ratify("now it is done")]})
        r = run(charter(max_hops=30), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)

    def test_a_fourth_occurrence_terminates_as_stalled(self):
        d = ping_pong()
        r = run(charter(max_hops=40), roster(), d)
        self.assertEqual("stalled", r.terminal_reason)
        self.assertIn("cto", r.note)

    def test_escalation_is_traced(self):
        t = MemoryTrace()
        run(charter(max_hops=40), roster(), ping_pong(), trace=t)
        events = [r["event"] for r in t.records()]
        self.assertIn("stall_escalate", events)

    def test_a_legitimate_repeat_visit_is_not_a_stall(self):
        """A -> B once, other work, A -> B again is normal. Only the SAME ordered
        pair repeating is a loop, and even then not until the fourth time."""
        d = ScriptedDispatch({
            "cto": [handoff("frontend_engineer", "first pass"),
                    handoff("frontend_engineer", "second pass")],
            "frontend_engineer": [handoff("cto", "back to you"),
                                  propose_done("done")],
            "gate_agent": [ratify()]})
        r = run(charter(max_hops=20), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)


class CycleDetectorIsLoadBearing(unittest.TestCase):
    def test_without_the_detector_the_hop_cap_is_what_stops_a_ping_pong(self):
        """Deleted alone, the run still stops — but it stops LATE and by the
        wrong rule, and no stall notice is ever injected. If this test showed no
        difference, the detector would be decoration."""
        d = ping_pong()
        r = run(charter(max_hops=6), roster(), d, guards=Guards(cycle=False))
        self.assertEqual("hops_exhausted", r.terminal_reason)
        for p in d.prompts_for("frontend_engineer"):
            self.assertNotIn("Stall notice", p)

    def test_with_the_detector_the_loop_is_caught_before_the_hop_cap(self):
        d = ping_pong()
        r = run(charter(max_hops=40), roster(), d)
        self.assertEqual("stalled", r.terminal_reason)
        self.assertLess(r.hops, 40, "the detector did not fire before the hop cap")


if __name__ == "__main__":
    unittest.main()
