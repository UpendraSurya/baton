"""Turning the Company OS personas into AgentSpecs with sane whitelists."""
import os
import pathlib
import unittest

from adapters.company_os import registry as R
from kernel.agent import GATE


class RepoPathSafety(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("BATON_COMPANY_OS_REPO", None)
        os.environ.pop("BATON_ALLOW_CANONICAL", None)

    def test_default_is_the_unlimited_sandbox_fork(self):
        self.assertEqual(pathlib.Path.home() / "unlimited" / "company-os",
                         R.repo_path())

    def test_pointing_at_canonical_company_os_is_refused(self):
        """~/.company-os/state.db holds 66 irreplaceable runs. Development does
        not happen there, and the adapter will not be talked into it."""
        os.environ["BATON_COMPANY_OS_REPO"] = str(pathlib.Path.home() / "company-os")
        with self.assertRaises(RuntimeError) as cm:
            R.repo_path()
        self.assertIn("canonical", str(cm.exception))

    def test_canonical_can_be_unlocked_deliberately(self):
        os.environ["BATON_COMPANY_OS_REPO"] = str(pathlib.Path.home() / "company-os")
        os.environ["BATON_ALLOW_CANONICAL"] = "1"
        self.assertEqual(pathlib.Path.home() / "company-os", R.repo_path())


class Layers(unittest.TestCase):
    def setUp(self):
        self.reg = R.load_registry()

    def test_all_six_layers_are_present(self):
        for layer in R.LAYERS:
            self.assertIn(layer, self.reg, f"{layer} missing from agent_registry.json")

    def test_layer_of_finds_a_known_agent(self):
        self.assertEqual("delivery", R.layer_of("frontend_engineer", self.reg))
        self.assertEqual("executive", R.layer_of("ceo", self.reg))
        self.assertEqual("operations", R.layer_of("gate_agent", self.reg))

    def test_layer_of_an_unknown_agent_is_empty(self):
        self.assertEqual("", R.layer_of("senior_vibe_officer", self.reg))


class CanHandTo(unittest.TestCase):
    def setUp(self):
        self.reg = R.load_registry()

    def test_an_agent_can_reach_its_own_layer(self):
        moves = R.can_hand_to("cto", self.reg)
        self.assertIn("cpo", moves)
        self.assertNotIn("cto", moves, "an agent must not hand to itself")

    def test_an_agent_can_reach_the_layer_below(self):
        self.assertIn("master_intake_agent", R.can_hand_to("ceo", self.reg))

    def test_an_agent_cannot_reach_two_layers_down(self):
        self.assertNotIn("frontend_engineer", R.can_hand_to("ceo", self.reg))

    def test_the_gate_is_always_reachable(self):
        for name in ("ceo", "frontend_engineer", "tester", "copywriter"):
            self.assertIn("gate_agent", R.can_hand_to(name, self.reg),
                          f"{name} cannot reach the gate")

    def test_whitelists_stay_well_under_the_49_way_choice(self):
        """The whole point of the layer rule: a 49-way choice per hop destroys
        routing quality. If this ever fails, the layer rule has stopped working."""
        for layer in R.LAYERS:
            for name in self.reg[layer]:
                if name.startswith("_"):
                    continue
                n = len(R.can_hand_to(name, self.reg))
                self.assertLessEqual(n, 30, f"{name} has a {n}-way choice")


class LoadAgents(unittest.TestCase):
    def test_every_spec_carries_its_real_persona_text(self):
        agents = R.load_agents()
        self.assertGreater(len(agents), 30)
        self.assertIn("frontend_engineer", agents)
        self.assertGreater(len(agents["frontend_engineer"].instructions), 200,
                           "persona file was not loaded")

    def test_the_gate_agent_gets_role_gate_and_everyone_else_worker(self):
        agents = R.load_agents()
        self.assertEqual(GATE, agents["gate_agent"].role)
        self.assertEqual("worker", agents["ceo"].role)

    def test_a_pool_restricts_both_the_roster_and_the_whitelists(self):
        pool = {"ceo", "cto", "frontend_engineer", "gate_agent"}
        agents = R.load_agents(pool=pool)
        self.assertEqual(pool, set(agents))
        for spec in agents.values():
            self.assertTrue(spec.can_hand_to <= pool,
                            f"{spec.name} can reach outside the pool")

    def test_specs_carry_the_registry_model(self):
        self.assertTrue(R.load_agents()["frontend_engineer"].model_id)


if __name__ == "__main__":
    unittest.main()
