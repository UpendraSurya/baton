"""The gate must route AROUND an agent it has exhausted, not into the wall.

Observed on ForgeLine (2026-08-23): the gate rejected the same agent three
times and the run died `reject_cap_reached` with 30 other staffed agents never
touched. The runtime's own note said "after 2 it must ratify or route to a
different agent" — a rule that was described and never enforced. The cap
TERMINATED the run instead of constraining the gate's choice.

Fixing the prompt would fix one instance. Removing the exhausted agent from the
gate's legal moves fixes the class.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.runtime import DispatchResult, Guards, run
from baton.trace import MemoryTrace

ART = ('"artifacts": [{"path": "out.md", "description": "d", "content": "The deliverable body, in full. The deliverable body, in full. The deliverable body, in full. The deliverable body, in full. The deliverable body, in full. "}]')


def done(summary="done"):
    return '```handoff\n{"decision": "PROPOSE_DONE", "summary": "%s", %s}\n```' % (summary, ART)


def reject(to):
    return ('```handoff\n{"decision": "REJECT", "to": "%s", '
            '"reason": "not good enough"}\n```' % to)


def ratify():
    return ('```handoff\n{"decision": "RATIFY", "summary": "ok", "coverage": ["out.md"]}\n```')


def roster():
    return {
        "alice": AgentSpec("alice", "# A", frozenset({"bob", "gate"})),
        "bob": AgentSpec("bob", "# B", frozenset({"alice", "gate"})),
        "gate": AgentSpec("gate", "# G", frozenset({"alice", "bob"}), role=GATE),
    }


def charter(**kw):
    base = dict(brief="b", entry_agent="alice", gate_agent="gate",
                agent_pool=frozenset(roster()), acceptance_criteria=("x",),
                budget_ceiling_usd=100.0, max_hops=20, max_rejects_per_agent=2)
    base.update(kw)
    return Charter(**base)


class Scripted:
    """Replays a script, and records what the gate was TOLD it could do."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.gate_prompts = []

    def __call__(self, agent, baton, prompt):
        if agent.is_gate:
            self.gate_prompts.append(prompt)
        return DispatchResult(text=self.lines.pop(0), cost_usd=0.001,
                              in_tokens=1, out_tokens=1)


class TheGateIsSteeredAwayFromAnExhaustedAgent(unittest.TestCase):
    def test_an_exhausted_agent_leaves_the_gates_legal_moves(self):
        d = Scripted([done(), reject("alice"),      # alice reject 1
                      done(), reject("alice"),      # alice reject 2 -> exhausted
                      done(), ratify()])            # gate must not name alice again
        run(charter(), roster(), d, trace=MemoryTrace("t"))
        offer = d.gate_prompts[-1].rsplit("You may reject to exactly one of:", 1)[-1]
        self.assertNotIn("alice", offer,
                         "alice was still offered after being exhausted")
        self.assertIn("bob", offer, "the gate was left with nobody to route to")

    def test_the_run_continues_instead_of_dying_at_the_cap(self):
        d = Scripted([done(), reject("alice"), done(), reject("alice"),
                      done(), ratify()])
        r = run(charter(), roster(), d, trace=MemoryTrace("t"))
        self.assertEqual(r.terminal_reason, "ratified")

    def test_the_gate_can_still_route_to_a_DIFFERENT_agent(self):
        d = Scripted([done(), reject("alice"), done(), reject("alice"),
                      done(), reject("bob"), done(), ratify()])
        r = run(charter(), roster(), d, trace=MemoryTrace("t"))
        self.assertEqual(r.terminal_reason, "ratified")
        self.assertIn("bob", r.path)

    def test_when_everyone_is_exhausted_the_run_still_terminates(self):
        """The cap is not deleted, it is demoted to a backstop. With nobody
        left to route to, a gate that still refuses to ratify must end the run
        rather than loop forever."""
        d = Scripted([done(), reject("alice"), done(), reject("alice"),
                      done(), reject("bob"), done(), reject("bob"),
                      done(), reject("alice"), done(), reject("bob")])
        r = run(charter(), roster(), d, trace=MemoryTrace("t"))
        self.assertIn(r.terminal_reason,
                      ("reject_cap_reached", "charter_violation", "hops_exhausted"))

    def test_the_guard_can_be_switched_off_alone(self):
        # Anti-vacuity: with reject_cap off, the old behaviour returns and the
        # gate is free to name an exhausted agent forever.
        d = Scripted([done(), reject("alice"), done(), reject("alice"),
                      done(), reject("alice"), done(), ratify()])
        r = run(charter(), roster(), d, trace=MemoryTrace("t"),
                guards=Guards(reject_cap=False))
        self.assertEqual(r.terminal_reason, "ratified")


if __name__ == "__main__":
    unittest.main()
