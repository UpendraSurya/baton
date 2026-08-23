"""A ratified run must hand back WHAT it ratified.

Found from outside, by a project consuming baton as a wheel (tenderline, Phase 1).
`RunResult` carried `terminal_reason`, `hops`, `spend_usd`, `gate_summary`, `note`,
`path` and `last_baton` — but not the coverage the gate asserted, nor the artifact
set that coverage was checked against.

`last_baton.artifacts` is NOT a substitute: it is the set as it ARRIVED at the gate.
The gate may attach its own, and carried artifacts are stripped to pointers on the
way, so the caller cannot reconstruct what was ratified.

For that project the coverage array IS the compliance matrix — the commercial
deliverable — and it had to be recovered by scanning the trace. A consumer
re-deriving the runtime's own conclusion from the log is the tell that a return
value is missing.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.runtime import DispatchResult, run
from baton.trace import MemoryTrace

BODY = "The deliverable, in full. " * 5


def art(path):
    return ('{"path": "%s", "description": "d", "content": "%s"}' % (path, BODY))


DONE = ('```handoff\n{"decision": "PROPOSE_DONE", "summary": "d", "artifacts": [%s, %s]}\n```'
        % (art("a.md"), art("b.md")))
RATIFY = ('```handoff\n{"decision": "RATIFY", "summary": "ok", '
          '"coverage": ["a.md", "b.md"]}\n```')


def roster():
    return {"w": AgentSpec("w", "# W", frozenset({"g"})),
            "g": AgentSpec("g", "# G", frozenset({"w"}), role=GATE)}


def charter():
    return Charter(brief="b", entry_agent="w", gate_agent="g",
                   agent_pool=frozenset(roster()), acceptance_criteria=("c1", "c2"),
                   budget_ceiling_usd=10.0, max_hops=6)


class Scripted:
    def __init__(self):
        self.lines = [DONE, RATIFY]

    def __call__(self, agent, baton, prompt):
        return DispatchResult(text=self.lines.pop(0), cost_usd=0.0,
                              in_tokens=1, out_tokens=1)


class ARatifiedRunReturnsWhatItRatified(unittest.TestCase):
    def result(self):
        return run(charter(), roster(), Scripted(), trace=MemoryTrace("t"))

    def test_coverage_reaches_the_caller(self):
        self.assertEqual(("a.md", "b.md"), self.result().coverage)

    def test_the_ratified_artifact_set_reaches_the_caller(self):
        self.assertEqual({"a.md", "b.md"},
                         {a.path for a in self.result().artifacts})

    def test_every_cited_path_is_present_in_the_artifacts(self):
        # The property a compliance matrix depends on: you can build the table
        # from the RETURN VALUE, without reading the trace.
        r = self.result()
        paths = {a.path for a in r.artifacts}
        self.assertTrue(set(r.coverage) <= paths)

    def test_a_refused_run_carries_no_coverage(self):
        # ratified_without_coverage must not hand back a half-filled matrix that
        # a caller could mistake for a delivery.
        bad = ('```handoff\n{"decision": "RATIFY", "summary": "ok", '
               '"coverage": ["a.md"]}\n```')

        class Short(Scripted):
            def __init__(self):
                self.lines = [DONE, bad, bad]

        r = run(charter(), roster(), Short(), trace=MemoryTrace("t"))
        self.assertEqual("ratified_without_coverage", r.terminal_reason)
        self.assertEqual((), r.coverage)


class TheRefusalNamesTheUnansweredCriterion(unittest.TestCase):
    """Gap 2, diagnostics half: 'named work for 24' does not say WHICH of 25 is
    missing, so a consumer has to work it out. Positional coverage means the
    short entry is the one at that index."""

    def test_the_note_quotes_the_criterion_that_has_no_work(self):
        bad = ('```handoff\n{"decision": "RATIFY", "summary": "ok", '
               '"coverage": ["a.md"]}\n```')

        class Short(Scripted):
            def __init__(self):
                self.lines = [DONE, bad, bad]

        r = run(charter(), roster(), Short(), trace=MemoryTrace("t"))
        self.assertIn("c2", r.note)


if __name__ == "__main__":
    unittest.main()
