"""Killer 5: a run that never comes back.

Observed 2026-08-23 — a real run sat for 25 minutes at 0% CPU with no trace
write, blocked inside a provider call, and ended for NO terminal reason at all.
It had to be killed by hand. For a kernel whose whole claim is "every run ends
for exactly one of the declared reasons", a run ending for none of them is the
claim being false.

The provider set `urlopen(timeout=30)` and it did not fire. So the kernel cannot
delegate this: a stop rule that depends on the thing being stopped is not a stop
rule.

TWO layers, because they catch different things:
  * a wall-clock deadline for the RUN — slow accumulation across many hops
  * a timeout on ONE dispatch — the loop never regains control otherwise, and a
    run-level deadline checked between hops would wait forever for a hop that
    never returns.
"""
import time
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.errors import CharterInvalid
from baton.runtime import DispatchResult, Guards, TERMINAL_REASONS, run
from baton.trace import MemoryTrace

BODY = "The deliverable, in full. " * 5
DONE = ('```handoff\n{"decision": "PROPOSE_DONE", "summary": "d", "artifacts": '
        '[{"path": "out.md", "description": "d", "content": "%s"}]}\n```' % BODY)
RATIFY = ('```handoff\n{"decision": "RATIFY", "summary": "ok", '
          '"coverage": ["out.md"]}\n```')


def roster():
    return {"w": AgentSpec("w", "# W", frozenset({"g"})),
            "g": AgentSpec("g", "# G", frozenset({"w"}), role=GATE)}


def charter(**kw):
    base = dict(brief="b", entry_agent="w", gate_agent="g",
                agent_pool=frozenset(roster()), acceptance_criteria=("x",),
                budget_ceiling_usd=100.0, max_hops=50)
    base.update(kw)
    return Charter(**base)


class Slow:
    """A dispatch that takes its time, then answers correctly."""

    def __init__(self, seconds, lines=None):
        self.seconds = seconds
        self.lines = list(lines or [DONE, RATIFY])

    def __call__(self, agent, baton, prompt):
        time.sleep(self.seconds)
        text = self.lines.pop(0) if self.lines else RATIFY
        return DispatchResult(text=text, cost_usd=0.0, in_tokens=1, out_tokens=1)


class Hangs:
    """A dispatch that never comes back. Exactly the observed failure."""

    def __call__(self, agent, baton, prompt):
        time.sleep(3600)


class TheRunHasADeadline(unittest.TestCase):
    def test_a_run_past_its_deadline_terminates(self):
        # one hop longer than the whole deadline, so the check between hops
        # has something to catch
        r = run(charter(max_wall_seconds=0.15), roster(), Slow(0.25),
                trace=MemoryTrace("t"))
        self.assertEqual("time_exhausted", r.terminal_reason)
        self.assertIn("time_exhausted", TERMINAL_REASONS)

    def test_a_fast_run_is_untouched(self):
        r = run(charter(max_wall_seconds=30), roster(), Slow(0),
                trace=MemoryTrace("t"))
        self.assertEqual("ratified", r.terminal_reason)

    def test_the_note_says_how_long_it_ran(self):
        r = run(charter(max_wall_seconds=0.15), roster(), Slow(0.25),
                trace=MemoryTrace("t"))
        self.assertIn("0.15s deadline", r.note)

    def test_the_deadline_must_be_positive(self):
        # validate() is explicit in this library; run() calls it.
        with self.assertRaises(CharterInvalid):
            charter(max_wall_seconds=0).validate()


class OneHopCannotHangForever(unittest.TestCase):
    """The layer that actually catches the observed bug. A run-level deadline
    checked BETWEEN hops never fires when a single hop never returns."""

    def test_a_hanging_dispatch_ends_the_run(self):
        started = time.time()
        r = run(charter(max_dispatch_seconds=0.2), roster(), Hangs(),
                trace=MemoryTrace("t"))
        self.assertEqual("dispatch_failure", r.terminal_reason)
        self.assertLess(time.time() - started, 20,
                        "the runtime waited for a hop that never returns")

    def test_the_note_names_the_timeout(self):
        r = run(charter(max_dispatch_seconds=0.2), roster(), Hangs(),
                trace=MemoryTrace("t"))
        self.assertIn("0.2", r.note)

    def test_a_dispatch_inside_the_limit_is_untouched(self):
        r = run(charter(max_dispatch_seconds=5), roster(), Slow(0.05),
                trace=MemoryTrace("t"))
        self.assertEqual("ratified", r.terminal_reason)

    def test_the_dispatch_limit_must_be_positive(self):
        with self.assertRaises(CharterInvalid):
            charter(max_dispatch_seconds=-1).validate()


class TheGuardSwitchesOffAlone(unittest.TestCase):
    def test_with_the_wall_clock_off_a_slow_run_is_not_stopped(self):
        r = run(charter(max_wall_seconds=0.05), roster(), Slow(0.25),
                trace=MemoryTrace("t"), guards=Guards(wall_clock=False))
        self.assertEqual("ratified", r.terminal_reason)


if __name__ == "__main__":
    unittest.main()
