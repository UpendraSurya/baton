"""
Rendering, parsing and validating the routing decision.

Half these cases are the DEGRADED forms — the parser is fed real recorded agent
prose with the routing block buried in it, wrapped wrong, duplicated, or missing
entirely. Clean canned JSON alone would make this suite vacuous.
"""
import json
import pathlib
import unittest

from baton.agent import GATE, AgentSpec
from baton.packet import Baton, Kind
from baton.charter import Charter
from baton.contract import (PRESSURE_LINE, parse_decision, render_prompt,
                             render_routing_contract, repair_nudge,
                             validate_decision)
from baton.errors import IllegalTarget, ParseFailure, RoleViolation

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
REAL_PROSE = json.loads((FIXTURES / "real_agent_prose.json").read_text())

CTO = AgentSpec("cto", "# CTO\nYou own technical architecture.",
                can_hand_to=frozenset({"frontend_engineer", "ml_engineer", "gate_agent"}))
GATE_AGENT = AgentSpec("gate_agent", "# GATE AGENT\nYou verify against criteria.",
                       can_hand_to=frozenset({"cto", "frontend_engineer"}), role=GATE)
CHARTER = Charter(brief="Build a docs Q&A bot", entry_agent="cto",
                  gate_agent="gate_agent",
                  agent_pool=frozenset({"cto", "frontend_engineer", "gate_agent"}),
                  acceptance_criteria=("answers cite sources",))


def block(payload):
    return "```handoff\n" + json.dumps(payload) + "\n```"


def real_prose(i=0):
    return REAL_PROSE[i % len(REAL_PROSE)]["text"]


class RenderingTests(unittest.TestCase):
    def test_contract_lists_only_legal_moves(self):
        text = render_routing_contract(CTO, CHARTER)
        self.assertIn("frontend_engineer", text)
        self.assertIn("gate_agent", text)
        # on CTO's whitelist but outside the charter pool — must not be offered
        self.assertNotIn("ml_engineer", text)

    def test_contract_shows_the_three_verbs_and_no_others(self):
        text = render_routing_contract(CTO, CHARTER)
        self.assertIn("HANDOFF", text)
        self.assertIn("PROPOSE_DONE", text)
        self.assertNotIn("RATIFY", text)        # worker may not ratify

    def test_gate_contract_shows_gate_verbs_only(self):
        text = render_routing_contract(GATE_AGENT, CHARTER)
        self.assertIn("RATIFY", text)
        self.assertIn("REJECT", text)
        self.assertNotIn("PROPOSE_DONE", text)

    def test_include_legal_moves_false_removes_the_whitelist(self):
        """The seam the anti-vacuity suite uses to delete guard A alone."""
        text = render_routing_contract(CTO, CHARTER, include_legal_moves=False)
        self.assertNotIn("frontend_engineer", text)
        self.assertIn("HANDOFF", text)          # the verb list survives

    def test_prompt_is_persona_then_baton_then_contract(self):
        b = Baton("t1", 1, "charter", "cto", "Design the retrieval layer")
        text = render_prompt(CTO, b, CHARTER)
        self.assertLess(text.index("You own technical architecture"),
                        text.index("Design the retrieval layer"))
        self.assertLess(text.index("Design the retrieval layer"),
                        text.index("Routing contract"))

    def test_prompt_renders_acceptance_criteria(self):
        b = Baton("t1", 1, "charter", "cto", "goal")
        text = render_prompt(CTO, b, CHARTER)
        self.assertIn("answers cite sources", text)

    def test_pressure_line_appears_only_under_pressure(self):
        b = Baton("t1", 1, "charter", "cto", "goal")
        self.assertNotIn(PRESSURE_LINE, render_prompt(CTO, b, CHARTER))
        self.assertIn(PRESSURE_LINE, render_prompt(CTO, b, CHARTER, pressure=True))

    def test_nudge_is_appended_last_and_nothing_else_is_resent(self):
        b = Baton("t1", 1, "charter", "cto", "goal")
        nudge = repair_nudge("no handoff block found", CTO, CHARTER)
        text = render_prompt(CTO, b, CHARTER, nudge=nudge)
        self.assertTrue(text.rstrip().endswith(nudge.rstrip()))


class ParsingCleanTests(unittest.TestCase):
    def test_parses_a_well_formed_handoff(self):
        d = parse_decision(block({"decision": "HANDOFF", "to": "frontend_engineer",
                                  "goal": "Implement the hero",
                                  "rationale": "design settled"}))
        self.assertIs(Kind.HANDOFF, d.kind)
        self.assertEqual("frontend_engineer", d.to)
        self.assertEqual("Implement the hero", d.goal)

    def test_parses_artifacts_with_previews(self):
        d = parse_decision(block({
            "decision": "HANDOFF", "to": "frontend_engineer", "goal": "g",
            "artifacts": [{"path": "w/spec.md", "description": "spec",
                           "preview": "two\nlines"}]}))
        self.assertEqual("w/spec.md", d.artifacts[0].path)
        self.assertEqual("two\nlines", d.artifacts[0].preview)

    def test_parses_the_legacy_string_artifact_form(self):
        """The contract example in the design note shows `"path — description"`;
        real models copy that shape, so both forms must parse."""
        d = parse_decision(block({"decision": "HANDOFF", "to": "x", "goal": "g",
                                  "artifacts": ["workspace/spec.md — approved spec"]}))
        self.assertEqual("workspace/spec.md", d.artifacts[0].path)
        self.assertEqual("approved spec", d.artifacts[0].description)

    def test_parses_propose_done(self):
        d = parse_decision(block({"decision": "PROPOSE_DONE",
                                  "summary": "all criteria met"}))
        self.assertIs(Kind.PROPOSE_DONE, d.kind)
        self.assertEqual("all criteria met", d.summary)

    def test_parses_ratify_and_reject(self):
        self.assertIs(Kind.RATIFY, parse_decision(
            block({"decision": "RATIFY", "summary": "ships"})).kind)
        r = parse_decision(block({"decision": "REJECT", "to": "frontend_engineer",
                                  "reason": "no tests"}))
        self.assertIs(Kind.REJECT, r.kind)
        self.assertEqual("no tests", r.reason)


class ParsingDegradedTests(unittest.TestCase):
    """Real model output, wrong in the ways real model output is wrong."""

    def test_block_buried_after_real_agent_prose(self):
        text = real_prose(0) + "\n\n" + block({"decision": "HANDOFF", "to": "x",
                                               "goal": "g"})
        self.assertEqual("x", parse_decision(text).to)

    def test_prose_AFTER_the_block_is_ignored(self):
        text = (block({"decision": "HANDOFF", "to": "x", "goal": "g"})
                + "\n\n" + real_prose(1))
        self.assertEqual("x", parse_decision(text).to)

    def test_last_block_wins_when_the_agent_shows_its_working(self):
        text = ("Here is a draft:\n" + block({"decision": "HANDOFF", "to": "wrong",
                                              "goal": "draft"})
                + "\nOn reflection:\n"
                + block({"decision": "HANDOFF", "to": "right", "goal": "final"}))
        self.assertEqual("right", parse_decision(text).to)

    def test_json_fence_label_is_accepted(self):
        text = real_prose(2) + '\n```json\n{"decision": "HANDOFF", "to": "x", "goal": "g"}\n```'
        self.assertEqual("x", parse_decision(text).to)

    def test_unfenced_trailing_json_object_is_accepted(self):
        text = real_prose(3) + '\n\n{"decision": "HANDOFF", "to": "x", "goal": "g"}\n'
        self.assertEqual("x", parse_decision(text).to)

    def test_trailing_comma_is_tolerated(self):
        text = '```handoff\n{"decision": "HANDOFF", "to": "x", "goal": "g",}\n```'
        self.assertEqual("x", parse_decision(text).to)

    def test_smart_quotes_are_normalised(self):
        text = '```handoff\n{“decision”: “HANDOFF”, “to”: “x”, “goal”: “g”}\n```'
        self.assertEqual("x", parse_decision(text).to)

    def test_lowercase_decision_verb_is_normalised(self):
        self.assertIs(Kind.HANDOFF,
                      parse_decision(block({"decision": "handoff", "to": "x",
                                            "goal": "g"})).kind)

    def test_pure_real_prose_with_no_block_raises_parse_failure(self):
        for sample in REAL_PROSE[:10]:
            with self.assertRaises(ParseFailure):
                parse_decision(sample["text"])

    def test_truncated_json_raises_parse_failure(self):
        with self.assertRaises(ParseFailure):
            parse_decision('```handoff\n{"decision": "HANDOFF", "to": "x"')

    def test_unknown_verb_raises_parse_failure(self):
        with self.assertRaises(ParseFailure) as cm:
            parse_decision(block({"decision": "TRANSFER", "to": "x"}))
        self.assertIn("TRANSFER", str(cm.exception))

    def test_empty_output_raises_parse_failure(self):
        with self.assertRaises(ParseFailure):
            parse_decision("")


class ValidationTests(unittest.TestCase):
    def test_legal_handoff_passes(self):
        d = parse_decision(block({"decision": "HANDOFF", "to": "frontend_engineer",
                                  "goal": "g"}))
        self.assertIs(d, validate_decision(d, CTO, CHARTER))

    def test_target_outside_the_whitelist_is_illegal(self):
        d = parse_decision(block({"decision": "HANDOFF", "to": "ceo", "goal": "g"}))
        with self.assertRaises(IllegalTarget) as cm:
            validate_decision(d, CTO, CHARTER)
        self.assertIn("frontend_engineer", str(cm.exception))   # echoes the legal list

    def test_target_on_the_whitelist_but_outside_the_pool_is_illegal(self):
        d = parse_decision(block({"decision": "HANDOFF", "to": "ml_engineer",
                                  "goal": "g"}))
        with self.assertRaises(IllegalTarget):
            validate_decision(d, CTO, CHARTER)

    def test_hallucinated_agent_is_illegal(self):
        d = parse_decision(block({"decision": "HANDOFF", "to": "senior_vibe_officer",
                                  "goal": "g"}))
        with self.assertRaises(IllegalTarget):
            validate_decision(d, CTO, CHARTER)

    def test_handoff_with_empty_goal_is_illegal(self):
        """A baton with no goal is a dropped baton — the receiver has nothing."""
        d = parse_decision(block({"decision": "HANDOFF", "to": "frontend_engineer",
                                  "goal": "   "}))
        with self.assertRaises(IllegalTarget):
            validate_decision(d, CTO, CHARTER)

    def test_worker_may_not_ratify(self):
        d = parse_decision(block({"decision": "RATIFY", "summary": "looks good to me"}))
        with self.assertRaises(RoleViolation):
            validate_decision(d, CTO, CHARTER)

    def test_worker_may_not_reject(self):
        d = parse_decision(block({"decision": "REJECT", "to": "frontend_engineer",
                                  "reason": "meh"}))
        with self.assertRaises(RoleViolation):
            validate_decision(d, CTO, CHARTER)

    def test_gate_may_not_propose_done(self):
        d = parse_decision(block({"decision": "PROPOSE_DONE", "summary": "s"}))
        with self.assertRaises(RoleViolation):
            validate_decision(d, GATE_AGENT, CHARTER)

    def test_gate_reject_target_must_be_in_the_pool(self):
        d = parse_decision(block({"decision": "REJECT", "to": "stranger",
                                  "reason": "r"}))
        with self.assertRaises(IllegalTarget):
            validate_decision(d, GATE_AGENT, CHARTER)

    def test_propose_done_needs_no_target_but_does_need_evidence(self):
        d = parse_decision(block({"decision": "PROPOSE_DONE", "summary": "done",
                                  "artifacts": [{"path": "d.md",
                                                 "content": "the work"}]}))
        self.assertIs(d, validate_decision(d, CTO, CHARTER))

    def test_propose_done_without_artifacts_is_refused(self):
        """Live 2026-08-16: a worker proposed done with nothing attached and the
        gate ratified the claim. A proposal with no evidence is unverifiable."""
        d = parse_decision(block({"decision": "PROPOSE_DONE", "summary": "done"}))
        with self.assertRaises(IllegalTarget):
            validate_decision(d, CTO, CHARTER)

    def test_enforce_target_false_lets_an_illegal_target_through(self):
        """The seam the anti-vacuity suite uses to delete guard B alone."""
        d = parse_decision(block({"decision": "HANDOFF", "to": "ceo", "goal": "g"}))
        self.assertIs(d, validate_decision(d, CTO, CHARTER, enforce_target=False))

    def test_enforce_target_false_still_enforces_roles(self):
        """Deleting the target guard must not silently delete the role guard —
        that is exactly how overlapping guards cover for each other."""
        d = parse_decision(block({"decision": "RATIFY", "summary": "s"}))
        with self.assertRaises(RoleViolation):
            validate_decision(d, CTO, CHARTER, enforce_target=False)


class RepairNudgeTests(unittest.TestCase):
    def test_nudge_is_terse_and_re_states_the_legal_moves(self):
        n = repair_nudge("no handoff block found", CTO, CHARTER)
        self.assertLess(len(n), 500, "the nudge must not re-send the whole contract")
        self.assertIn("frontend_engineer", n)
        self.assertIn("no handoff block found", n)


if __name__ == "__main__":
    unittest.main()
