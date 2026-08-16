"""The seam to Company OS: dispatch, charter priors, trace persistence.

Nothing here calls a model. real_dispatch is only ever exercised through its
refusal path — that refusal is what makes the gate provably free."""
import os
import pathlib
import sqlite3
import tempfile
import unittest

from baton.adapters.company_os import charter as C
from baton.adapters.company_os import dispatch as D
from baton.adapters.company_os import registry as R
from baton.adapters.company_os import state as S
from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.runtime import run
from tests.stub import ScriptedDispatch, propose_done, ratify


class RealDispatchIsBlockedUnderTheGate(unittest.TestCase):
    def test_it_refuses_when_the_forbid_flag_is_set(self):
        before = os.environ.get(D.FORBID)
        os.environ[D.FORBID] = "1"
        try:
            with self.assertRaises(RuntimeError) as cm:
                D.real_dispatch(None, None, "prompt")
            self.assertIn(D.FORBID, str(cm.exception))
        finally:
            if before is None:
                os.environ.pop(D.FORBID, None)
            else:
                os.environ[D.FORBID] = before

    def test_strip_persona_removes_it_exactly_once(self):
        """run_agent() prepends the persona itself. The kernel already rendered
        it, so the adapter takes it back out — otherwise every real call pays for
        the persona twice."""
        agent = AgentSpec("cto", "# CTO\nYou own architecture.", frozenset({"x"}))
        prompt = "# CTO\nYou own architecture.\n\n## Your baton\ngoal: ship"
        stripped = D.strip_persona(prompt, agent)
        self.assertNotIn("You own architecture.", stripped)
        self.assertIn("## Your baton", stripped)

    def test_strip_persona_is_a_no_op_when_the_persona_is_absent(self):
        agent = AgentSpec("cto", "# CTO\nsomething else", frozenset({"x"}))
        self.assertEqual("just a baton", D.strip_persona("just a baton", agent))


class CharterPriors(unittest.TestCase):
    def test_priors_come_from_the_topology_tables(self):
        """The edges die; the estimates survive. That is the whole point of the
        Charter replacing the topology."""
        p = C.priors("rag_chatbot")
        self.assertGreater(p["budget_ceiling_usd"], 0)
        self.assertIn(p["security_tier"], ("L", "M", "H"))

    def test_an_unknown_project_type_still_yields_usable_priors(self):
        p = C.priors("something_nobody_wrote_a_topology_for")
        self.assertGreater(p["budget_ceiling_usd"], 0)

    def test_charter_for_produces_a_valid_charter(self):
        ch = C.charter_for("Build a docs Q&A bot", project_type="rag_chatbot",
                           acceptance_criteria=("answers cite sources",))
        self.assertIsInstance(ch, Charter)
        ch.validate()
        self.assertIn(ch.entry_agent, ch.agent_pool)
        self.assertIn("gate_agent", ch.agent_pool)

    def test_acceptance_criteria_are_required_by_the_caller(self):
        with self.assertRaises(ValueError):
            C.charter_for("a brief", acceptance_criteria=())

    def test_explicit_overrides_beat_the_priors(self):
        ch = C.charter_for("brief", project_type="rag_chatbot",
                           acceptance_criteria=("x",), max_hops=3)
        self.assertEqual(3, ch.max_hops)

    def test_a_low_budget_run_gets_a_lower_ceiling_than_a_high_one(self):
        low = C.charter_for("b", project_type="rag_chatbot", budget="low",
                            acceptance_criteria=("x",))
        high = C.charter_for("b", project_type="rag_chatbot", budget="high",
                             acceptance_criteria=("x",))
        self.assertLess(low.budget_ceiling_usd, high.budget_ceiling_usd)

    def test_the_charter_and_the_registry_agree_on_the_roster(self):
        ch = C.charter_for("brief", project_type="rag_chatbot",
                           acceptance_criteria=("x",))
        agents = R.load_agents(pool=ch.agent_pool)
        self.assertEqual(set(ch.agent_pool), set(agents))
        d = ScriptedDispatch({ch.entry_agent: [propose_done()],
                              "gate_agent": [ratify()]})
        self.assertEqual("ratified", run(ch, agents, d).terminal_reason)


class StatePersistence(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def _a_result(self):
        agents = {
            "cto": AgentSpec("cto", "# CTO", frozenset({"gate_agent"})),
            "gate_agent": AgentSpec("gate_agent", "# GATE", frozenset({"cto"}),
                                    role=GATE)}
        ch = Charter(brief="b", entry_agent="cto", gate_agent="gate_agent",
                     agent_pool=frozenset(agents), acceptance_criteria=("x",),
                     budget_ceiling_usd=5.0, max_hops=5)
        d = ScriptedDispatch({"cto": [propose_done()], "gate_agent": [ratify()]},
                             cost_per_call=0.20)
        return run(ch, agents, d)

    def test_persist_writes_events_and_costs(self):
        n = S.persist(self._a_result(), "proj-1", home=self.home)
        self.assertGreater(n, 0)
        con = sqlite3.connect(self.home / "state.db")
        self.assertGreater(
            con.execute("select count(*) from events where project_id='proj-1'")
               .fetchone()[0], 0)
        self.assertAlmostEqual(
            0.40,
            con.execute("select sum(usd) from cost_ledger where project_id='proj-1'")
               .fetchone()[0], places=6)
        con.close()

    def test_the_terminal_reason_lands_as_an_event(self):
        S.persist(self._a_result(), "proj-2", home=self.home)
        con = sqlite3.connect(self.home / "state.db")
        payloads = [r[0] for r in con.execute(
            "select payload from events where project_id='proj-2'")]
        con.close()
        self.assertTrue(any("ratified" in p for p in payloads))

    def test_persisting_to_the_canonical_home_is_refused(self):
        with self.assertRaises(RuntimeError) as cm:
            S.persist(self._a_result(), "proj-3",
                      home=S.CANONICAL_HOME)
        self.assertIn("canonical", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


class CostMeterIsNotSilentlyZero(unittest.TestCase):
    """Found by a stub-mode probe of a REAL Company OS charter: every dispatch
    priced at $0.00, because agent_registry.json stores short aliases ('sonnet')
    while MODEL_PRICES is keyed by full ids ('claude-sonnet-4-6'). A model that
    costs zero never trips the budget ceiling — the ceiling silently stops
    existing, which is the failure this vault has already been burned by once."""

    def test_registry_aliases_resolve_to_priced_model_ids(self):
        self.assertEqual("claude-sonnet-4-6", D.resolve_model("sonnet"))
        self.assertEqual("claude-opus-4-8", D.resolve_model("opus"))
        self.assertEqual("claude-haiku-4-5", D.resolve_model("haiku"))

    def test_an_unknown_model_passes_through_unchanged(self):
        self.assertEqual("gpt-5", D.resolve_model("gpt-5"))

    def test_every_model_in_the_registry_can_be_priced(self):
        """The check that would have caught it. If a new alias appears in the
        registry with no price, this fails instead of quietly costing nothing."""
        import sys
        sys.path.insert(0, str(R.repo_path()))
        from core import config
        reg = R.load_registry()
        seen = set()
        for layer in R.LAYERS:
            for name, entry in reg.get(layer, {}).items():
                if name.startswith("_") or not isinstance(entry, dict):
                    continue
                m = entry.get("model")
                if m:
                    seen.add(m)
        unpriced = sorted(m for m in seen
                          if D.resolve_model(m) not in config.MODEL_PRICES)
        self.assertEqual([], unpriced,
                         f"registry models with no rate card: {unpriced}")

    def test_pricing_an_unpriced_model_raises_rather_than_returning_zero(self):
        from baton.providers.base import UnmeteredModel
        with self.assertRaises(UnmeteredModel):
            D.cost_usd("gpt-5", 1000, 1000)

    def test_a_priced_model_returns_a_real_number(self):
        self.assertGreater(D.cost_usd("sonnet", 1_000_000, 0), 0.5)
