"""Defect found by real use: pre-flight CharterInvalid escaped the ten-reason
guarantee by raising before any RunResult existed.

`run()` raises `CharterInvalid` from three places — `charter.validate()`,
`_check_roster()`, and the budget pre-flight in `plan.estimate()` — and every
one of them runs BEFORE a trace or a RunResult is created. A caller who wrote
`result = run(...); handle(result.terminal_reason)` and only ever tests
TERMINAL_REASONS values never expected an exception, because the README said
"every run ends with exactly one of ten terminal reasons" with no carve-out.

The decision (see dev-notes/projects/baton/KNOWLEDGE/07-DECIDED.md, "four
baton defects" — this was left open, not closed either way): CharterInvalid
does NOT become an eleventh terminal reason. Manufacturing a RunResult for a
charter that never took a single hop would mean a trace_id, a hop count of
zero and a `ratified`-shaped return value existing for a run that never ran —
worse than an honest exception. Instead the guarantee is SCOPED: once a run
has started (a trace exists), it always ends via TERMINAL_REASONS; a charter
that could never have run at all is refused before that point, and refused
with an exception, not a RunResult.

This file enforces that the code, TERMINAL_REASONS and the README all agree
on that scope — not just that the runtime behaves this way today.
"""
import pathlib
import re
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.errors import CharterInvalid
from baton.runtime import TERMINAL_REASONS, Guards, run
from baton.trace import MemoryTrace

README = (pathlib.Path(__file__).parent.parent / "README.md").read_text()


def roster():
    return {"w": AgentSpec("w", "# w", frozenset({"g"})),
            "g": AgentSpec("g", "# g", frozenset({"w"}), role=GATE)}


def charter(**kw):
    base = dict(brief="b", entry_agent="w", gate_agent="g",
                agent_pool=frozenset(roster()), acceptance_criteria=("x",),
                budget_ceiling_usd=1.0, max_hops=6)
    base.update(kw)
    return Charter(**base)


class TheGuaranteeIsScopedInTheDocs(unittest.TestCase):
    """The literal defect: the README claimed the ten-reason guarantee
    unconditionally, with no mention of the pre-flight exception that already
    existed in the code. Docs and code must say the same thing."""

    def test_the_readme_scopes_the_guarantee_to_once_a_run_starts(self):
        self.assertIn("once a run starts", README.lower())

    def test_the_readme_names_charterinvalid_as_the_pre_flight_exception(self):
        stop_rules = README[README.index("## The stop rules"):]
        self.assertIn("CharterInvalid", stop_rules)

    def test_terminal_reasons_count_matches_the_readme_claim(self):
        self.assertEqual(10, len(TERMINAL_REASONS))
        self.assertIn("ten", README)

    def test_charterinvalid_is_not_smuggled_into_terminal_reasons(self):
        # The other half of the decision: it did NOT become an eleventh
        # terminal reason. If someone adds it there without updating this
        # test and the README's "ten", the count test above catches it first.
        self.assertNotIn("charter_invalid", TERMINAL_REASONS)


class TheScopeIsActuallyEnforced(unittest.TestCase):
    """Not just documented — a run that fails pre-flight must never produce a
    trace record. If it did, "once a run starts" would be a claim with
    nothing behind it."""

    def test_a_structurally_invalid_charter_leaves_no_trace_record(self):
        bad = charter(entry_agent="nobody")
        trace = MemoryTrace("t-struct")
        with self.assertRaises(CharterInvalid):
            run(bad, roster(), lambda *a: None, trace=trace)
        self.assertEqual([], list(trace.records()))

    def test_an_unsurvivable_budget_leaves_no_trace_record(self):
        trace = MemoryTrace("t-budget")
        with self.assertRaises(CharterInvalid):
            run(charter(budget_ceiling_usd=0.01), roster(), lambda *a: None,
                trace=trace, usd_per_call=5.0)
        self.assertEqual([], list(trace.records()))

    def test_once_a_hop_happens_no_charterinvalid_is_possible(self):
        # A structurally valid, survivable charter that actually dispatches
        # must resolve via TERMINAL_REASONS, never raise CharterInvalid.
        from baton.runtime import DispatchResult
        body = "The deliverable, in full. " * 5
        done = ('```handoff\n{"decision": "PROPOSE_DONE", "summary": "d", '
                '"artifacts": [{"path": "o.md", "content": "%s"}]}\n```' % body)
        ratify = ('```handoff\n{"decision": "RATIFY", "summary": "ok", '
                  '"coverage": ["o.md"]}\n```')
        lines = [done, ratify]

        def dispatch(agent, baton, prompt):
            return DispatchResult(text=lines.pop(0), cost_usd=0.0,
                                  in_tokens=1, out_tokens=1)

        r = run(charter(), roster(), dispatch, trace=MemoryTrace("t-ok"))
        self.assertIn(r.terminal_reason, TERMINAL_REASONS)


if __name__ == "__main__":
    unittest.main()
