"""The happy path: work gets done, the gate ratifies, the run ends.

Note what a worker CANNOT do here: end the run. Only the gate ratifies —
otherwise the model that did the work grades the work.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.errors import CharterInvalid
from baton.runtime import TERMINAL_REASONS, run
from baton.trace import MemoryTrace
from tests.stub import (ScriptedDispatch, boom, handoff, propose_done,
                        propose_done_without_evidence, prose_then, ratify)


def roster():
    return {
        "ceo": AgentSpec("ceo", "# CEO", frozenset({"cto", "gate_agent"})),
        "cto": AgentSpec("cto", "# CTO", frozenset({"frontend_engineer", "gate_agent"})),
        "frontend_engineer": AgentSpec("frontend_engineer", "# FE",
                                       frozenset({"cto", "gate_agent"})),
        "gate_agent": AgentSpec("gate_agent", "# GATE",
                                frozenset({"ceo", "cto", "frontend_engineer"}),
                                role=GATE),
    }


def charter(**kw):
    base = dict(brief="Build a docs Q&A bot", entry_agent="ceo",
                gate_agent="gate_agent",
                agent_pool=frozenset(roster()),
                acceptance_criteria=("answers cite sources", "p95 under 2s"),
                budget_ceiling_usd=10.0, max_hops=20)
    base.update(kw)
    return Charter(**base)


class HappyPath(unittest.TestCase):
    def test_run_terminates_on_ratify(self):
        d = ScriptedDispatch({
            "ceo": [prose_then(handoff("cto", "design the retrieval layer"))],
            "cto": [prose_then(handoff("frontend_engineer", "build the UI"))],
            "frontend_engineer": [prose_then(propose_done("both criteria met"))],
            "gate_agent": [prose_then(ratify("verified both criteria"))],
        })
        r = run(charter(), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)
        self.assertEqual(("ceo", "cto", "frontend_engineer", "gate_agent"), r.path)
        self.assertEqual(4, r.hops)
        self.assertIn("verified both criteria", r.gate_summary)

    def test_terminal_reason_is_from_the_closed_vocabulary(self):
        d = ScriptedDispatch({"ceo": [propose_done()], "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertIn(r.terminal_reason, TERMINAL_REASONS)

    def test_propose_done_routes_to_the_gate_not_to_the_worker_whitelist(self):
        """A worker proposes; the runtime decides the gate sees it. The worker
        never gets to choose its own examiner."""
        d = ScriptedDispatch({"ceo": [propose_done("I think we are finished")],
                              "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertEqual(("ceo", "gate_agent"), r.path)

    def test_the_gate_sees_the_proposal_summary_and_the_criteria(self):
        d = ScriptedDispatch({"ceo": [propose_done("criterion 1 met via citations")],
                              "gate_agent": [ratify()]})
        run(charter(), roster(), d)
        gate_prompt = d.prompts_for("gate_agent")[0]
        self.assertIn("criterion 1 met via citations", gate_prompt)
        self.assertIn("answers cite sources", gate_prompt)

    def test_artifacts_accumulate_across_hops(self):
        d = ScriptedDispatch({
            "ceo": [handoff("cto", "design", artifacts=[
                {"path": "w/brief.md", "description": "the brief", "preview": "p1"}])],
            "cto": [handoff("frontend_engineer", "build", artifacts=[
                {"path": "w/spec.md", "description": "the spec", "preview": "p2"}])],
            "frontend_engineer": [propose_done()],
            "gate_agent": [ratify()],
        })
        run(charter(), roster(), d)
        fe_prompt = d.prompts_for("frontend_engineer")[0]
        self.assertIn("w/brief.md", fe_prompt)      # earlier artifact still travels
        self.assertIn("w/spec.md", fe_prompt)

    def test_artifact_contents_never_travel_only_paths_and_previews(self):
        d = ScriptedDispatch({
            "ceo": [handoff("cto", "design", artifacts=[
                {"path": "w/spec.md", "description": "the spec",
                 "preview": "three short lines"}])],
            "cto": [propose_done()], "gate_agent": [ratify()]})
        run(charter(), roster(), d)
        cto_prompt = d.prompts_for("cto")[0]
        self.assertIn("three short lines", cto_prompt)
        self.assertLess(len(cto_prompt), 4000,
                        "the packet is growing — something is passing contents")


class TraceTests(unittest.TestCase):
    def test_every_hop_is_traced_with_cost_and_tokens(self):
        t = MemoryTrace(trace_id="tr1")
        d = ScriptedDispatch({"ceo": [propose_done()], "gate_agent": [ratify()]},
                             cost_per_call=0.25)
        run(charter(), roster(), d, trace=t)
        events = [r["event"] for r in t.records()]
        self.assertEqual("run_start", events[0])
        self.assertEqual("run_end", events[-1])
        dispatches = [r for r in t.records() if r["event"] == "dispatch"]
        self.assertEqual(2, len(dispatches))
        self.assertEqual(0.25, dispatches[0]["cost_usd"])
        self.assertGreater(dispatches[0]["in_tokens"], 0)

    def test_run_end_records_the_terminal_reason_and_spend(self):
        t = MemoryTrace()
        d = ScriptedDispatch({"ceo": [propose_done()], "gate_agent": [ratify()]},
                             cost_per_call=0.25)
        r = run(charter(), roster(), d, trace=t)
        end = t.records()[-1]
        self.assertEqual("ratified", end["terminal_reason"])
        self.assertAlmostEqual(0.50, end["spend_usd"], places=6)
        self.assertAlmostEqual(0.50, r.spend_usd, places=6)


class DispatchFailureTests(unittest.TestCase):
    def test_a_failed_dispatch_ends_the_run(self):
        d = ScriptedDispatch({"ceo": [boom()]})
        r = run(charter(), roster(), d)
        self.assertEqual("dispatch_failure", r.terminal_reason)

    def test_a_failed_dispatch_is_not_retried(self):
        """A transport failure is not a parse failure. Retrying it here would
        double-charge for something the ladder cannot fix."""
        d = ScriptedDispatch({"ceo": [boom()]})
        run(charter(), roster(), d)
        self.assertEqual(1, d.call_count("ceo"))


class RosterValidation(unittest.TestCase):
    def test_a_pool_member_with_no_spec_is_rejected_before_any_spend(self):
        agents = roster()
        del agents["frontend_engineer"]
        d = ScriptedDispatch({})
        with self.assertRaises(CharterInvalid):
            run(charter(), agents, d)
        self.assertEqual(0, d.call_count())

    def test_a_gate_agent_that_is_not_role_gate_is_rejected(self):
        agents = roster()
        agents["gate_agent"] = AgentSpec("gate_agent", "# GATE", frozenset({"ceo"}))
        with self.assertRaises(CharterInvalid) as cm:
            run(charter(), agents, ScriptedDispatch({}))
        self.assertIn("role", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


class RatifyRequiresADeliverable(unittest.TestCase):
    """A gate asked "is this done?" is strongly disposed to say yes.

    Observed live 2026-08-21 on mistral-large: a worker's routing block failed to
    parse twice, the runtime escalated to the gate, and the gate ratified an EMPTY
    artifact set with prose that only restated the acceptance criteria back. The
    ledger recorded that run as `delivered`.

    The contract already refused PROPOSE_DONE with no artifacts
    (contract.py) — but the malformed-routing escalation reaches the gate WITHOUT
    passing through PROPOSE_DONE, so that check was never on this path. The hole
    was not a missing idea; it was one idea enforced on one of two routes in.
    """

    def _empty_ratify_run(self, **guard_kw):
        from baton.runtime import Guards
        d = ScriptedDispatch({"ceo": [prose_then(handoff("cto"))],
                              "cto": [propose_done_without_evidence()],
                              "gate_agent": [ratify("looks complete to me")]})
        return run(charter(), roster(), d, guards=Guards(**guard_kw))

    def test_a_ratify_with_no_artifact_is_not_a_delivery(self):
        r = self._empty_ratify_run()
        self.assertEqual("ratified_without_deliverable", r.terminal_reason)
        self.assertIn("nothing was delivered", r.note)

    def test_the_outcome_is_distinguishable_from_an_infrastructure_failure(self):
        """bench/run.py maps dispatch_failure -> error (excluded from every rate)
        and everything else -> blocked. Producing nothing is a real routing
        outcome and must land in `blocked`, not be excused as an error."""
        r = self._empty_ratify_run()
        self.assertNotEqual("dispatch_failure", r.terminal_reason)
        self.assertIn(r.terminal_reason, TERMINAL_REASONS)

    def test_deleting_THIS_guard_alone_restores_the_bug(self):
        """The anti-vacuity discipline: if the suite still passes with the guard
        switched off, the guard was never what the test was testing."""
        r = self._empty_ratify_run(require_artifacts=False)
        self.assertEqual("ratified", r.terminal_reason,
                         "with the guard off the empty ratify must sail through "
                         "again — otherwise something else is catching this and "
                         "the new guard is dead code")

    def test_a_ratify_WITH_an_artifact_still_ratifies(self):
        """The guard must not make the happy path unreachable."""
        d = ScriptedDispatch({"ceo": [prose_then(handoff("cto"))],
                              "cto": [propose_done()],
                              "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)

    def test_an_artifact_produced_EARLIER_in_the_run_still_counts(self):
        """Artifacts travel on the baton. A deliverable attached at hop 1 and
        ratified at hop 5 is delivered — the check is 'does the work exist',
        not 'did the gate personally receive it this hop'."""
        from baton.packet import ArtifactRef
        art = ArtifactRef(path="workspace/spec.md", description="written early")
        d = ScriptedDispatch({"ceo": [prose_then(handoff("cto", artifacts=[art]))],
                              "cto": [propose_done_without_evidence()],
                              "gate_agent": [ratify()]})
        r = run(charter(), roster(), d)
        self.assertEqual("ratified", r.terminal_reason)
