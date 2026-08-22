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

# Reads the host's real topology library, registry and rate card off disk. The
# adapter is a worked example of binding a host; the host is not on a CI runner.
_HOST = R.repo_path()
needs_host = unittest.skipUnless(
    (_HOST / "registry" / "agent_registry.json").is_file(),
    f"no Company OS checkout at {_HOST}")
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


@needs_host
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


@needs_host
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


class TheDeliverableIsTheFullOutputNotTheDigest(unittest.TestCase):
    """Company OS writes the agent's full output to disk and hands the next node
    a <=200-char digest. That contract is right for Company OS's own DAG and
    wrong for baton: the handoff block is the LAST thing an agent writes, so the
    digest truncates away the routing decision every single time.

    Measured on the first live run: 4051 output tokens produced, 213 characters
    handed to the kernel, "no fenced handoff block found" on every attempt.
    """

    class FakeArtifact:
        def __init__(self, ref, summary):
            self.ref, self.summary = ref, summary

    class FakeOutput:
        def __init__(self, artifact=None, notes=""):
            self.artifact, self.notes = artifact, notes

    def out(self, ref="", summary="", notes=""):
        art = self.FakeArtifact(ref, summary) if (ref or summary) else None
        return self.FakeOutput(art, notes)

    def test_the_full_output_is_read_from_the_artifact_ref(self):
        full = ("# Postmortem template\n" + ("body line\n" * 200)
                + '```handoff\n{"decision": "HANDOFF", "to": "tester"}\n```')
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "ceo.md"
            path.write_text(full)
            text = D.deliverable_text(self.out(ref=str(path),
                                               summary=full[:200]))
        self.assertIn("```handoff", text)

    def test_the_summary_is_used_when_there_is_no_ref(self):
        self.assertEqual("a digest", D.deliverable_text(self.out(summary="a digest")))

    def test_a_ref_that_is_not_on_disk_falls_back_to_the_summary(self):
        # ref is a path in the HOST's workspace; a caller pointed elsewhere, or
        # the file was cleaned up. Losing the digest too would turn a degraded
        # hop into a dispatch failure.
        text = D.deliverable_text(self.out(ref="/nonexistent/ceo.md",
                                           summary="a digest"))
        self.assertEqual("a digest", text)

    def test_an_empty_file_does_not_beat_the_summary(self):
        # A zero-byte deliverable is a failed write, not a deliverable.
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "ceo.md"
            path.write_text("")
            text = D.deliverable_text(self.out(ref=str(path), summary="a digest"))
        self.assertEqual("a digest", text)

    def test_a_ref_that_is_not_a_path_is_not_opened(self):
        """Artifact.ref is "path / git ref". A bare git ref must not be opened
        as a file — and to prove the guard rather than the coincidence that
        "HEAD~1" is usually absent, there is a real file by that name in cwd."""
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "HEAD~1").write_text("WRONG: this is a git ref")
            cwd = os.getcwd()
            os.chdir(d)
            try:
                text = D.deliverable_text(self.out(ref="HEAD~1", summary="a digest"))
            finally:
                os.chdir(cwd)
        self.assertEqual("a digest", text)

    def test_notes_are_the_last_resort(self):
        self.assertEqual("rate_limited",
                         D.deliverable_text(self.out(notes="rate_limited")))

    def test_nothing_at_all_yields_empty_string(self):
        self.assertEqual("", D.deliverable_text(self.out()))


class TheHostBindingFailsHonestly(unittest.TestCase):
    """This adapter is a WORKED EXAMPLE of binding a host, and it ships in the
    wheel on purpose. An installed user without the Company OS checkout must be
    told that in one line, not handed `ModuleNotFoundError: No module named
    'core'` from three frames down a lazy import."""

    def test_a_missing_host_names_itself_the_path_and_the_remedy(self):
        # Exercised through _add_repo_to_path rather than real_dispatch: the
        # gate runs the suite with BATON_FORBID_REAL_DISPATCH=1, and a test that
        # cleared that flag to reach this code would weaken the one guarantee
        # making the gate provably free.
        before = os.environ.get("BATON_COMPANY_OS_REPO")
        os.environ["BATON_COMPANY_OS_REPO"] = "/nonexistent/company-os"
        try:
            with self.assertRaises(D.HostUnavailable) as cm:
                D._add_repo_to_path()
        finally:
            if before is None:
                os.environ.pop("BATON_COMPANY_OS_REPO", None)
            else:
                os.environ["BATON_COMPANY_OS_REPO"] = before
        msg = str(cm.exception)
        self.assertIn("/nonexistent/company-os", msg)      # where it looked
        self.assertIn("BATON_COMPANY_OS_REPO", msg)        # how to point it elsewhere
        self.assertIn("worked example", msg)               # what this adapter is

    def test_host_unavailable_is_a_baton_error(self):
        # A caller catching BatonError must catch this too; a bare RuntimeError
        # escaping an adapter is indistinguishable from a bug in the kernel.
        from baton.errors import BatonError
        self.assertTrue(issubclass(D.HostUnavailable, BatonError))
