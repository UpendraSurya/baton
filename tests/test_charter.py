"""The Charter bounds a run without prescribing its shape."""
import unittest

from kernel.agent import AgentSpec
from kernel.charter import Charter
from kernel.errors import CharterInvalid


def a_charter(**kw):
    base = dict(brief="Build a docs Q&A bot",
                entry_agent="ceo", gate_agent="gate_agent",
                agent_pool=frozenset({"ceo", "cto", "frontend_engineer", "gate_agent"}),
                acceptance_criteria=("answers cite sources", "p95 under 2s"))
    base.update(kw)
    return Charter(**base)


class CharterValidation(unittest.TestCase):
    def test_a_sane_charter_validates(self):
        a_charter().validate()                  # must not raise

    def test_entry_agent_must_be_in_the_pool(self):
        with self.assertRaises(CharterInvalid) as cm:
            a_charter(entry_agent="stranger").validate()
        self.assertIn("entry_agent", str(cm.exception))

    def test_gate_agent_must_be_in_the_pool(self):
        with self.assertRaises(CharterInvalid) as cm:
            a_charter(gate_agent="stranger").validate()
        self.assertIn("gate_agent", str(cm.exception))

    def test_empty_pool_is_invalid(self):
        with self.assertRaises(CharterInvalid):
            a_charter(agent_pool=frozenset()).validate()

    def test_acceptance_criteria_are_mandatory(self):
        """Written BEFORE the run so the gate cannot later be talked into
        lowering the bar. A charter with none is not runnable."""
        with self.assertRaises(CharterInvalid) as cm:
            a_charter(acceptance_criteria=()).validate()
        self.assertIn("acceptance_criteria", str(cm.exception))

    def test_budget_must_be_positive(self):
        with self.assertRaises(CharterInvalid):
            a_charter(budget_ceiling_usd=0).validate()

    def test_max_hops_must_be_at_least_one(self):
        with self.assertRaises(CharterInvalid):
            a_charter(max_hops=0).validate()

    def test_security_tier_is_a_closed_vocabulary(self):
        for tier in ("L", "M", "H"):
            a_charter(security_tier=tier).validate()
        with self.assertRaises(CharterInvalid):
            a_charter(security_tier="X").validate()

    def test_pressure_threshold_must_be_between_zero_and_one(self):
        with self.assertRaises(CharterInvalid):
            a_charter(pressure_threshold=1.5).validate()


class LegalMoves(unittest.TestCase):
    def test_legal_moves_are_the_intersection_of_whitelist_and_pool(self):
        agent = AgentSpec("cto", "persona",
                          can_hand_to=frozenset({"frontend_engineer", "ml_engineer"}))
        # ml_engineer is on the agent's whitelist but NOT in this run's pool.
        self.assertEqual(("frontend_engineer",), a_charter().legal_moves_for(agent))

    def test_legal_moves_are_sorted_for_a_stable_prompt(self):
        """An unstable order changes the prompt on every hop and silently
        destroys prompt-cache hits."""
        agent = AgentSpec("ceo", "persona",
                          can_hand_to=frozenset({"frontend_engineer", "cto", "gate_agent"}))
        self.assertEqual(("cto", "frontend_engineer", "gate_agent"),
                         a_charter().legal_moves_for(agent))

    def test_an_agent_with_no_legal_moves_is_reported_empty_not_crashing(self):
        agent = AgentSpec("orphan", "persona", can_hand_to=frozenset({"nobody"}))
        self.assertEqual((), a_charter().legal_moves_for(agent))


if __name__ == "__main__":
    unittest.main()
