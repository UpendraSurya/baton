"""The README's "Security & observability posture" section names specific
symbols as evidence for specific claims (identity is server-assigned, criteria
are frozen, ten terminal reasons, etc.). If one of those symbols is ever
renamed or removed, the section quietly becomes false while staying green
everywhere else -- no other gate reads that section as a claim about the code.

This is a ratchet, not a functional test: it does not re-verify the mechanisms
(other suites already do -- test_charter.py, test_ratify_is_checked.py,
test_terminal_reasons_scope.py, test_user_agent.py's identity assertions live
in runtime tests). It only proves the README's citations still point at
something real.
"""
from __future__ import annotations

import dataclasses
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

SECTION_HEADER = "## Security & observability posture"


class SecurityPostureSectionExists(unittest.TestCase):
    def setUp(self) -> None:
        self.text = README.read_text(encoding="utf-8")

    def test_section_is_present(self) -> None:
        self.assertIn(SECTION_HEADER, self.text)

    def test_all_ten_asi_risks_are_listed(self) -> None:
        for i in range(1, 11):
            self.assertIn(f"ASI0{i}" if i < 10 else "ASI10", self.text,
                         f"ASI{i:02d} missing from the OWASP mapping table")


class CitedSymbolsStillExist(unittest.TestCase):
    """Every backtick-quoted symbol the posture section leans on as evidence,
    checked against the actual source rather than trusted from the doc."""

    def test_charter_is_frozen_and_has_the_named_fields(self) -> None:
        from baton.charter import Charter
        self.assertTrue(dataclasses.is_dataclass(Charter))
        # frozen=True dataclasses raise on field assignment after construction.
        self.assertTrue(Charter.__dataclass_params__.frozen,
                        "README claims acceptance_criteria cannot move mid-run "
                        "because Charter is frozen -- it no longer is")
        fields = {f.name for f in dataclasses.fields(Charter)}
        for name in ("acceptance_criteria", "agent_pool", "gate_agent",
                    "budget_ceiling_usd", "max_hops", "max_wall_seconds",
                    "max_dispatch_seconds", "security_tier"):
            self.assertIn(name, fields)

    def test_agentspec_has_can_hand_to(self) -> None:
        from baton.agent import AgentSpec
        fields = {f.name for f in dataclasses.fields(AgentSpec)}
        self.assertIn("can_hand_to", fields)

    def test_baton_packet_has_from_agent_goal_and_rationale(self) -> None:
        from baton.packet import Baton, Decision
        baton_fields = {f.name for f in dataclasses.fields(Baton)}
        for name in ("from_agent", "to_agent", "goal", "rationale"):
            self.assertIn(name, baton_fields)
        decision_fields = {f.name for f in dataclasses.fields(Decision)}
        self.assertIn("rationale", decision_fields)

    def test_terminal_reasons_has_exactly_ten_and_the_two_named_here(self) -> None:
        from baton.runtime import TERMINAL_REASONS
        self.assertEqual(10, len(TERMINAL_REASONS))
        for reason in ("ratified_without_deliverable", "ratified_without_coverage"):
            self.assertIn(reason, TERMINAL_REASONS)

    def test_role_violation_is_a_real_error(self) -> None:
        from baton.errors import RoleViolation, BatonError
        self.assertTrue(issubclass(RoleViolation, BatonError))

    def test_tracesink_protocol_still_exists(self) -> None:
        from baton.trace import TraceSink
        self.assertTrue(hasattr(TraceSink, "append"))
        self.assertTrue(hasattr(TraceSink, "records"))

    def test_from_agent_is_assigned_by_the_runtime_not_the_model(self) -> None:
        """The one claim in the posture table load-bearing enough to check by
        source inspection, not just field presence: the README says
        `from_agent` is always `agent.name` from the runtime's own dispatch
        loop. Grepping for the literal assignment is crude but direct -- if
        this ever starts reading a value out of the parsed Decision instead,
        the identity-mitigation claim (ASI03) goes false silently."""
        runtime_src = (ROOT / "src" / "baton" / "runtime.py").read_text()
        self.assertIn("from_agent=agent.name", runtime_src,
                     "from_agent no longer appears to be server-assigned from "
                     "the dispatched AgentSpec -- re-check the ASI03 claim")

    def test_dependencies_are_still_empty(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn("dependencies = []", pyproject)


if __name__ == "__main__":
    unittest.main()
