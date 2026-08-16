"""
The gate must judge a deliverable, not a claim about one.

Every test here comes from a REAL failure observed on the first live Gemini run
(2026-08-16), not from imagination. Tier 1 had 212 green tests while all of this
was broken, because a scripted stub always attaches whatever the script says it
attaches. Live models do not.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.contract import parse_decision, render_routing_contract, validate_decision
from baton.errors import IllegalTarget
from baton.packet import MAX_CONTENT_CHARS, ArtifactRef, Baton
from baton.runtime import _merge_artifacts, run
from tests.stub import (ScriptedDispatch, propose_done,
                        propose_done_without_evidence, ratify)

WORKER = AgentSpec("writer", "# writer", frozenset({"editor", "gate"}))
GATE_AGENT = AgentSpec("gate", "# gate", frozenset({"writer"}), role=GATE)
CHARTER = Charter(brief="write copy", entry_agent="writer", gate_agent="gate",
                  agent_pool=frozenset({"writer", "gate"}),
                  acceptance_criteria=("under 120 words",))


def block(**kw):
    import json
    return "```handoff\n" + json.dumps(kw) + "\n```"


class ProposeDoneNeedsEvidence(unittest.TestCase):
    """THE live bug: writer sent PROPOSE_DONE with zero artifacts, and the gate
    ratified its assertion that the draft was '79 words'. The draft never
    existed anywhere."""

    def test_propose_done_with_no_artifacts_is_rejected(self):
        d = parse_decision(block(decision="PROPOSE_DONE", summary="it is 79 words"))
        with self.assertRaises(IllegalTarget) as cm:
            validate_decision(d, WORKER, CHARTER)
        self.assertIn("cannot ratify a claim", str(cm.exception))

    def test_propose_done_with_an_artifact_passes(self):
        d = parse_decision(block(
            decision="PROPOSE_DONE", summary="done",
            artifacts=[{"path": "draft.md", "description": "the copy",
                        "content": "Baton lets agents pick the next agent."}]))
        self.assertIs(d, validate_decision(d, WORKER, CHARTER))

    def test_the_runtime_nudges_a_worker_that_proposes_without_evidence(self):
        d = ScriptedDispatch({
            "writer": [propose_done_without_evidence(),          # no artifacts
                       block(decision="PROPOSE_DONE", summary="here it is",
                             artifacts=[{"path": "draft.md", "content": "the copy"}])],
            "gate": [ratify("read the draft, it is fine")]})
        agents = {"writer": WORKER, "gate": GATE_AGENT}
        r = run(CHARTER, agents, d)
        self.assertEqual(2, d.call_count("writer"), "no repair nudge was sent")
        self.assertEqual("ratified", r.terminal_reason)
        self.assertIn("attach the work product", d.prompts_for("writer")[1])

    def test_a_worker_that_never_attaches_evidence_reaches_the_gate_flagged(self):
        d = ScriptedDispatch({"writer": [propose_done_without_evidence(),
                                 propose_done_without_evidence()],
                              "gate": [ratify("assessed what exists")]})
        agents = {"writer": WORKER, "gate": GATE_AGENT}
        r = run(CHARTER, agents, d)
        self.assertEqual(("writer", "gate"), r.path)
        self.assertIn("malformed_routing", d.prompts_for("gate")[0])


class TheGateActuallySeesTheWork(unittest.TestCase):
    def test_artifact_content_reaches_the_gate_prompt(self):
        draft = "Baton routes at runtime. Agents choose the next agent."
        d = ScriptedDispatch({
            "writer": [block(decision="PROPOSE_DONE", summary="done",
                             artifacts=[{"path": "draft.md", "content": draft}])],
            "gate": [ratify("I read it")]})
        agents = {"writer": WORKER, "gate": GATE_AGENT}
        run(CHARTER, agents, d)
        self.assertIn(draft, d.prompts_for("gate")[0],
                      "the gate was asked to judge work it never saw")


class ContentIsBounded(unittest.TestCase):
    """Carrying content must not recreate the quadratic growth the original
    design refused contents to avoid."""

    def test_carried_artifacts_are_stripped_to_pointers(self):
        old = ArtifactRef("a.md", "old", "prev", "OLD CONTENT " * 100)
        new = ArtifactRef("b.md", "new", "prev", "NEW CONTENT")
        merged = _merge_artifacts((old,), (new,))
        by_path = {a.path: a for a in merged}
        self.assertEqual("", by_path["a.md"].content, "history kept its content")
        self.assertEqual("NEW CONTENT", by_path["b.md"].content)
        self.assertEqual("prev", by_path["a.md"].preview, "the pointer survived")

    def test_content_is_truncated_at_the_cap(self):
        a = ArtifactRef("big.md", content="x" * (MAX_CONTENT_CHARS + 5000))
        self.assertLess(len(a.content), MAX_CONTENT_CHARS + 100)
        self.assertIn("truncated", a.content)

    def test_prompt_size_stays_flat_across_many_hops(self):
        """The property that matters: hop 6 must not cost six deliverables."""
        big = "z" * 4000
        script = {}
        for name in ("writer", "editor"):
            script[name] = [block(decision="HANDOFF",
                                  to="editor" if name == "writer" else "writer",
                                  goal="keep going",
                                  artifacts=[{"path": f"{name}.md", "content": big}])]
        script["gate"] = [ratify()]
        agents = {
            "writer": AgentSpec("writer", "# w", frozenset({"editor", "gate"})),
            "editor": AgentSpec("editor", "# e", frozenset({"writer", "gate"})),
            "gate": AgentSpec("gate", "# g", frozenset({"writer", "editor"}), role=GATE)}
        ch = Charter(brief="b", entry_agent="writer", gate_agent="gate",
                     agent_pool=frozenset(agents), acceptance_criteria=("x",),
                     budget_ceiling_usd=999.0, max_hops=6)
        d = ScriptedDispatch(script)
        run(ch, agents, d)
        sizes = [len(p) for _n, p in d.calls]
        self.assertLess(max(sizes), min(sizes) + 6000,
                        f"prompt grew across hops: {sizes}")


class ContractDoesNotInviteFabrication(unittest.TestCase):
    """The researcher cited 'workspace/spec.md' — a file that did not exist. It
    was the placeholder from this very contract, echoed back as if real."""

    def test_the_example_contains_no_plausible_real_looking_path(self):
        text = render_routing_contract(WORKER, CHARTER)
        self.assertNotIn("workspace/spec.md", text)

    def test_the_contract_tells_the_agent_not_to_invent_paths(self):
        text = render_routing_contract(WORKER, CHARTER)
        self.assertIn("Do not cite a path you did not write", text)

    def test_the_contract_offers_content_as_the_alternative_to_a_path(self):
        text = render_routing_contract(WORKER, CHARTER)
        self.assertIn("content", text)


if __name__ == "__main__":
    unittest.main()
