"""Turning the Company OS personas into AgentSpecs with sane whitelists."""
import os
import pathlib
import unittest

from baton.adapters.company_os import registry as R
from baton.agent import GATE

# These read the host's real registry and personas off disk. The adapter is a
# worked example of binding a host, and the host is not on a CI runner or a
# contributor's laptop — so they SKIP there rather than failing a build for the
# absence of something that was never shipped. The safety tests below (which
# path is refused, which is default) need no host and always run.
HOST = R.repo_path()
needs_host = unittest.skipUnless(
    (HOST / "registry" / "agent_registry.json").is_file(),
    f"no Company OS checkout at {HOST}")


class RepoPathSafety(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("BATON_COMPANY_OS_REPO", None)
        os.environ.pop("BATON_ALLOW_CANONICAL", None)

    def test_default_is_the_unlimited_sandbox_fork(self):
        self.assertEqual(pathlib.Path.home() / "unlimited" / "company-os",
                         R.repo_path())

    def test_pointing_at_canonical_company_os_is_refused(self):
        """The canonical state DB holds 66 irreplaceable runs. Development does
        not happen there, and the adapter will not be talked into it."""
        os.environ["BATON_COMPANY_OS_REPO"] = str(R.CANONICAL_REPO)
        with self.assertRaises(RuntimeError) as cm:
            R.repo_path()
        self.assertIn("canonical", str(cm.exception))

    def test_canonical_can_be_unlocked_deliberately(self):
        os.environ["BATON_COMPANY_OS_REPO"] = str(R.CANONICAL_REPO)
        os.environ["BATON_ALLOW_CANONICAL"] = "1"
        self.assertEqual(R.CANONICAL_REPO, R.repo_path())


@needs_host
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


@needs_host
class CanHandTo(unittest.TestCase):
    """Work flows DOWNHILL: an agent hands to the layer below it, plus the gate.

    Chosen 2026-08-23 over "own layer + below", which produced a median 24-way
    choice per hop because the delivery layer alone holds 18 agents. The design
    note asked for 5-10; this rule gives a median of 7.

    The bottom layer has nothing below it, so it falls back to its OWN layer.
    Without that fallback those agents can only reach the gate, which is the
    dead-end that made an entry agent end a run before any work happened.
    """

    def setUp(self):
        self.reg = R.load_registry()

    def test_an_agent_reaches_the_layer_below(self):
        # executive -> operations
        self.assertIn("master_intake_agent", R.can_hand_to("ceo", self.reg))

    def test_an_agent_reaches_its_own_layer(self):
        """Peer handoff. Restored 2026-08-23 after ForgeLine: a layer holds the
        people who do different KINDS of the same work, and a builder that
        cannot hand to a peer cannot assemble a multi-specialist deliverable."""
        self.assertIn("cpo", R.can_hand_to("cto", self.reg))

    def test_an_agent_never_hands_to_itself(self):
        for name in ("ceo", "cto", "frontend_engineer", "tester"):
            self.assertNotIn(name, R.can_hand_to(name, self.reg))

    def test_an_agent_cannot_reach_two_layers_down(self):
        self.assertNotIn("frontend_engineer", R.can_hand_to("ceo", self.reg))

    def test_the_bottom_layer_falls_back_to_its_own_layer(self):
        bottom = R.LAYERS[-1]
        peers = [a for a in self.reg[bottom] if not a.startswith("_")]
        name = peers[0]
        moves = R.can_hand_to(name, self.reg)
        self.assertGreater(len(moves), 1,
                           "the bottom layer would dead-end at the gate")
        self.assertTrue(set(moves) & (set(peers) - {name}))

    def test_the_gate_is_always_reachable(self):
        for name in ("ceo", "frontend_engineer", "tester", "copywriter"):
            self.assertIn("gate_agent", R.can_hand_to(name, self.reg),
                          f"{name} cannot reach the gate")

    def test_nobody_dead_ends(self):
        """A whitelist of only the gate means that agent can never do anything
        except end the run. That is the bug this rule exists to avoid."""
        for layer in R.LAYERS:
            for name in self.reg[layer]:
                if name.startswith("_") or name == "gate_agent":
                    continue
                moves = R.can_hand_to(name, self.reg)
                self.assertGreater(len(moves), 1, f"{name} can only reach the gate")

    def test_no_agent_faces_the_whole_firm(self):
        """The registry-wide size is NOT the number that governs a run — a
        whitelist is intersected with the staffed roster, where the median is
        roughly half this. What must never happen is an agent facing every
        agent in the firm, which is the free-for-all the layer rule exists to
        prevent. Roster-level sizing is asserted where a roster exists:
        company-os scripts/smoke_baton_router.py."""
        everyone = sum(len([n for n in self.reg[l] if not n.startswith("_")])
                       for l in R.LAYERS)
        for layer in R.LAYERS:
            for name in self.reg[layer]:
                if name.startswith("_"):
                    continue
                self.assertLess(len(R.can_hand_to(name, self.reg)), everyone * 0.75,
                                f"{name} faces most of the firm")


@needs_host
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
