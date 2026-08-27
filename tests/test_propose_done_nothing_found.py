"""PROPOSE_DONE with no artifacts, when nothing legitimately exists to attach.

Defect found by real use: `validate_decision` refused every PROPOSE_DONE with
no artifacts, unconditionally, on the grounds that a proposal with no evidence
is unverifiable (tests/test_evidence.py — a live bug where a worker CLAIMED a
draft existed and it did not). That refusal is correct for a claim. It is wrong
for an agent that actually looked and found nothing: "there is no such file",
"the search returned zero matches", "no migration is needed" are legitimate
completions with nothing to point at, and the contract gave them no way to say
so — every attempt was indistinguishable from the ForgeLine-style claim and got
the same IllegalTarget, forcing two wasted repair attempts before the runtime
escalated it to the gate anyway, mislabelled `malformed_routing`.

The fix is a signal the CLAIM-refusal cannot see: `nothing_found`. It is not a
weaker check — a bare claim with no artifacts and no `nothing_found` is refused
exactly as before (see the regression cases below, copied from
test_evidence.py's own fixtures). `nothing_found` still requires a summary: an
agent has to say what it looked for, not just tick a box.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.contract import parse_decision, validate_decision
from baton.errors import IllegalTarget
from baton.runtime import run
from tests.stub import ScriptedDispatch, ratify

WORKER = AgentSpec("writer", "# writer", frozenset({"gate"}))
GATE_AGENT = AgentSpec("gate", "# gate", frozenset({"writer"}), role=GATE)
CHARTER = Charter(brief="find the bug in the payments module",
                  entry_agent="writer", gate_agent="gate",
                  agent_pool=frozenset({"writer", "gate"}),
                  acceptance_criteria=("bug is found and described",))


def block(**kw):
    import json
    return "```handoff\n" + json.dumps(kw) + "\n```"


class ANothingFoundProposalIsAccepted(unittest.TestCase):
    def test_propose_done_with_nothing_found_and_a_summary_is_legal(self):
        d = parse_decision(block(
            decision="PROPOSE_DONE",
            summary="searched every file under payments/ for the reported "
                    "symptom; none reproduce it and no matching defect exists",
            nothing_found=True))
        self.assertIs(d, validate_decision(d, WORKER, CHARTER))

    def test_nothing_found_is_parsed_off_the_wire(self):
        d = parse_decision(block(decision="PROPOSE_DONE", summary="s",
                                 nothing_found=True))
        self.assertTrue(d.nothing_found)

    def test_nothing_found_defaults_to_false(self):
        d = parse_decision(block(decision="PROPOSE_DONE", summary="s",
                                 artifacts=[{"path": "a.md", "content": "x"}]))
        self.assertFalse(d.nothing_found)

    def test_nothing_found_without_a_summary_is_still_refused(self):
        """Ticking the box is not evidence either — say what was looked for."""
        d = parse_decision(block(decision="PROPOSE_DONE", nothing_found=True))
        with self.assertRaises(IllegalTarget):
            validate_decision(d, WORKER, CHARTER)

    def test_the_runtime_forwards_a_nothing_found_proposal_straight_to_the_gate(self):
        """No wasted repair attempts, no malformed_routing mislabel — a
        legitimate 'nothing found' reaches the gate on the first hop."""
        d = ScriptedDispatch({
            "writer": [block(decision="PROPOSE_DONE",
                             summary="no defect reproduces; nothing to fix",
                             nothing_found=True)],
            "gate": [ratify("confirmed the negative result",
                            coverage=["writer's nothing_found note"])]})
        r = run(CHARTER, {"writer": WORKER, "gate": GATE_AGENT}, d)
        self.assertEqual(1, d.call_count("writer"),
                         "a legitimate nothing_found proposal should not need a "
                         "repair retry")
        self.assertEqual(("writer", "gate"), r.path)


class TheClaimRefusalStillHolds(unittest.TestCase):
    """Regression: a bare claim with nothing attached and no nothing_found flag
    must be refused exactly as before. Copied from test_evidence.py's own live
    bug fixture — nothing_found must not become a loophole for it."""

    def test_a_bare_claim_with_no_artifacts_is_still_refused(self):
        d = parse_decision(block(decision="PROPOSE_DONE",
                                 summary="it is 79 words"))
        with self.assertRaises(IllegalTarget) as cm:
            validate_decision(d, WORKER, CHARTER)
        self.assertIn("cannot ratify a claim", str(cm.exception))

    def test_trust_me_it_is_finished_is_still_refused(self):
        d = parse_decision(block(decision="PROPOSE_DONE",
                                 summary="trust me, it is finished"))
        with self.assertRaises(IllegalTarget):
            validate_decision(d, WORKER, CHARTER)


if __name__ == "__main__":
    unittest.main()
