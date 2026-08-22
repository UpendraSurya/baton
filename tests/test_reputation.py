"""Reputation is a claim about an agent, so it is computed from evidence and
carries its own sample size. The interesting cases are all about REFUSING to
make a claim: too few runs, no track record, an agent not on this team.
"""
import unittest

from baton import reputation


class Rate(unittest.TestCase):
    def test_rate_is_revisions_over_runs(self):
        rep = reputation.from_tallies({"backend_engineer": 78},
                                      {"backend_engineer": 26})
        self.assertAlmostEqual(rep["backend_engineer"].rate, 1 / 3)

    def test_an_agent_that_never_needed_rework_is_recorded_at_zero(self):
        # Not absent: "63 runs, never reworked" is a stronger statement than
        # silence, and callers need to tell it from "we have no idea".
        rep = reputation.from_tallies({"architecture_agent": 63}, {})
        self.assertEqual(rep["architecture_agent"].rate, 0.0)
        self.assertEqual(rep["architecture_agent"].runs, 63)

    def test_thin_evidence_is_not_evidence(self):
        rep = reputation.from_tallies({"ml_engineer": 2, "tester": 99},
                                      {"ml_engineer": 2})
        self.assertNotIn("ml_engineer", rep)
        self.assertIn("tester", rep)

    def test_min_runs_is_configurable(self):
        rep = reputation.from_tallies({"ml_engineer": 2}, {"ml_engineer": 1},
                                      min_runs=2)
        self.assertIn("ml_engineer", rep)

    def test_min_runs_below_one_is_refused(self):
        # min_runs=0 admits an agent with no runs, and rate divides by it.
        with self.assertRaises(ValueError):
            reputation.from_tallies({}, {}, min_runs=0)

    def test_revisions_for_an_agent_with_no_runs_are_refused(self):
        # A revision against an agent that never ran means the two tallies were
        # counted over different populations. Silently dropping it would hide
        # the mismatch and quietly understate the team's rework.
        with self.assertRaises(ValueError):
            reputation.from_tallies({"tester": 99}, {"ghost": 4})


class FromTraces(unittest.TestCase):
    """The library's own runs are the evidence source a user of baton has."""

    def trace(self, *records):
        return list(records)

    def test_a_dispatch_is_a_run(self):
        rep = reputation.from_traces([self.trace(
            {"event": "dispatch", "agent": "cto"},
            {"event": "dispatch", "agent": "cto"},
            {"event": "dispatch", "agent": "cto"},
        )], min_runs=1)
        self.assertEqual(rep["cto"].runs, 3)

    def test_a_reject_counts_against_the_agent_it_was_sent_back_to(self):
        # Not against the gate that issued it. The gate rejecting is the gate
        # working; being rejected is the reputation signal.
        rep = reputation.from_traces([self.trace(
            {"event": "dispatch", "agent": "backend_engineer"},
            {"event": "dispatch", "agent": "gate_agent"},
            {"event": "decision", "agent": "gate_agent",
             "decision": "REJECT", "to": "backend_engineer"},
        )], min_runs=1)
        self.assertEqual(rep["backend_engineer"].revisions, 1)
        self.assertEqual(rep["gate_agent"].revisions, 0)

    def test_a_handoff_is_not_a_revision(self):
        rep = reputation.from_traces([self.trace(
            {"event": "dispatch", "agent": "ceo"},
            {"event": "decision", "agent": "ceo",
             "decision": "HANDOFF", "to": "cto"},
            {"event": "dispatch", "agent": "cto"},
        )], min_runs=1)
        self.assertEqual(rep["cto"].revisions, 0)

    def test_evidence_accumulates_across_traces(self):
        one = self.trace({"event": "dispatch", "agent": "cto"})
        two = self.trace({"event": "dispatch", "agent": "cto"},
                         {"event": "decision", "agent": "gate_agent",
                          "decision": "REJECT", "to": "cto"})
        rep = reputation.from_traces([one, two], min_runs=2)
        self.assertEqual((rep["cto"].runs, rep["cto"].revisions), (2, 1))

    def test_an_empty_history_yields_an_empty_reputation(self):
        self.assertEqual(len(reputation.from_traces([])), 0)


class Note(unittest.TestCase):
    """The line that reaches a model. It is the only part of this module that
    can change a routing decision, so it states numbers and no adjectives."""

    def rep(self):
        return reputation.from_tallies(
            {"integration_engineer": 31, "backend_engineer": 78,
             "frontend_engineer": 19, "tester": 99},
            {"integration_engineer": 16, "backend_engineer": 26,
             "frontend_engineer": 2})

    def test_note_names_the_worst_agent_first(self):
        note = self.rep().note(["backend_engineer", "integration_engineer"])
        self.assertLess(note.index("integration_engineer"),
                        note.index("backend_engineer"))

    def test_note_omits_agents_with_a_clean_record(self):
        # "tester has needed rework on 0% of past tasks" is noise on every hop.
        self.assertNotIn("tester", self.rep().note(["tester", "backend_engineer"]))

    def test_note_only_mentions_agents_on_this_team(self):
        note = self.rep().note(["backend_engineer"])
        self.assertNotIn("integration_engineer", note)

    def test_note_is_empty_when_nobody_has_a_track_record(self):
        # An empty string, not a header with nothing under it — the prompt
        # should gain no tokens when it gains no information.
        self.assertEqual(self.rep().note(["tester"]), "")

    def test_note_caps_how_many_agents_it_lists(self):
        rep = reputation.from_tallies({f"a{i}": 10 for i in range(9)},
                                      {f"a{i}": i + 1 for i in range(9)})
        note = rep.note([f"a{i}" for i in range(9)], limit=4)
        self.assertEqual(sum(f"a{i}" in note for i in range(9)), 4)

    def test_note_reports_a_rate_as_a_percentage(self):
        self.assertIn("33%", self.rep().note(["backend_engineer"]))


class Ranked(unittest.TestCase):
    def test_ranked_is_worst_first(self):
        rep = reputation.from_tallies({"a": 10, "b": 10, "c": 10},
                                      {"a": 1, "b": 5})
        self.assertEqual([r.agent for r in rep.ranked()], ["b", "a", "c"])


class Corpus(unittest.TestCase):
    """The frozen numbers from the 66-run Company OS corpus that the bench's
    reputation arm was built on. Tallies are inlined so this test runs with no
    database present — the point is that the LIBRARY reproduces the table.
    """

    RUNS = {"integration_engineer": 31, "lead_engineer": 8, "backend_engineer": 78,
            "data_analyst": 7, "frontend_engineer": 19, "architecture_agent": 63,
            "tester": 99, "qa_engineer": 53, "ceo": 62, "ml_engineer": 4}
    REVISIONS = {"integration_engineer": 16, "lead_engineer": 3,
                 "backend_engineer": 26, "data_analyst": 1, "frontend_engineer": 2}

    def test_reproduces_the_headline_table(self):
        rep = reputation.from_tallies(self.RUNS, self.REVISIONS)
        got = {r.agent: round(r.rate * 100) for r in rep.ranked()[:3]}
        self.assertEqual(got, {"integration_engineer": 52,
                               "lead_engineer": 38,
                               "backend_engineer": 33})

    def test_frontend_engineer_is_the_11_percent_lane(self):
        rep = reputation.from_tallies(self.RUNS, self.REVISIONS)
        self.assertEqual(round(rep["frontend_engineer"].rate * 100), 11)

    def test_the_thinnest_lane_is_excluded_by_default(self):
        # ml_engineer has 4 runs and would otherwise read as flawless.
        rep = reputation.from_tallies(self.RUNS, self.REVISIONS, min_runs=5)
        self.assertNotIn("ml_engineer", rep)


if __name__ == "__main__":
    unittest.main()
