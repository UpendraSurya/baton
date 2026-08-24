"""A dispatch callable that raises must still end the run.

Found by an independent review (2026-08-24) against the README's central claim:
"Every run ends with exactly one of ten terminal reasons. There is no path out
of the loop that returns 'it just stopped'."

That was false. `Dispatch`'s own docstring invites "just pass a plain function
with this signature", and any exception such a function raises — a KeyError in
an adapter, a vendor SDK's own exception type, an unexpected response schema —
propagated straight out of `run()`. No RunResult, no terminal reason, no
`run_end` trace record. The two shipped providers happen to be hardened, which
is a much narrower guarantee than the one advertised.

A dispatch that raises IS a transport failure, and `dispatch_failure` is the
terminal reason that already exists for exactly that.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.runtime import DispatchResult, Guards, run
from baton.trace import MemoryTrace


def roster():
    return {"w": AgentSpec("w", "# W", frozenset({"g"})),
            "g": AgentSpec("g", "# G", frozenset({"w"}), role=GATE)}


def charter():
    return Charter(brief="b", entry_agent="w", gate_agent="g",
                   agent_pool=frozenset(roster()), acceptance_criteria=("c1",),
                   budget_ceiling_usd=1.0, max_hops=4)


def raiser(exc):
    def dispatch(agent, baton, prompt):
        raise exc
    return dispatch


class ADispatchThatRaisesStillTerminates(unittest.TestCase):
    def test_an_ordinary_exception_becomes_dispatch_failure(self):
        r = run(charter(), roster(), raiser(KeyError("bug in my adapter")),
                trace=MemoryTrace("t"))
        self.assertEqual(r.terminal_reason, "dispatch_failure")

    def test_the_note_names_the_exception_so_it_is_debuggable(self):
        r = run(charter(), roster(), raiser(KeyError("bug in my adapter")),
                trace=MemoryTrace("t"))
        self.assertIn("KeyError", r.note)
        self.assertIn("bug in my adapter", r.note)

    def test_the_run_is_recorded_rather_than_lost(self):
        # The old failure was silent by OMISSION: the process died mid-run and
        # the trace had no run_end, so a caller reading traces saw a run that
        # simply stopped existing.
        trace = MemoryTrace("t")
        run(charter(), roster(), raiser(RuntimeError("boom")), trace=trace)
        self.assertTrue(any(rec.get("event") == "run_end"
                            for rec in trace.records()))

    def test_it_holds_with_the_wall_clock_guard_OFF(self):
        # Two code paths reach the dispatch — the watchdog thread and, when
        # wall_clock is off, a direct call. Both leaked.
        r = run(charter(), roster(), raiser(KeyError("bug")),
                trace=MemoryTrace("t"), guards=Guards(wall_clock=False))
        self.assertEqual(r.terminal_reason, "dispatch_failure")

    def test_KeyboardInterrupt_is_NOT_swallowed(self):
        # The operator pressing ctrl-C is not a transport failure, and a runtime
        # that eats it is worse than one that crashes.
        with self.assertRaises(KeyboardInterrupt):
            run(charter(), roster(), raiser(KeyboardInterrupt()),
                trace=MemoryTrace("t"))

    def test_SystemExit_is_NOT_swallowed(self):
        with self.assertRaises(SystemExit):
            run(charter(), roster(), raiser(SystemExit(2)), trace=MemoryTrace("t"))


if __name__ == "__main__":
    unittest.main()
