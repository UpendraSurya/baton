"""bench/patterns.py compares routing patterns in one simulated world. These
tests hold the properties that make that comparison fair — not its numbers."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "bench"))

import patterns as P  # noqa: E402


class TheComparisonIsFair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = P.run_all(200, 1, "uniform")

    def test_every_pattern_saw_the_same_tickets(self):
        self.assertEqual({len(v) for v in self.res.values()}, {200})

    def test_no_pattern_exceeds_the_shared_budget(self):
        for p, outs in self.res.items():
            with self.subTest(pattern=p):
                self.assertLessEqual(max(o.calls for o in outs), P.MAX_CALLS)

    def test_patterns_without_a_verifier_always_claim_success(self):
        # The point of the silent-failure column: nothing in these patterns
        # can say "I failed".
        for p in ("sequential", "conditional", "concurrent"):
            self.assertTrue(all(o.reported for o in self.res[p]))

    def test_delivered_implies_reported(self):
        for p, outs in self.res.items():
            self.assertFalse([o for o in outs if o.delivered and not o.reported], p)

    def test_it_is_deterministic(self):
        again = P.run_all(200, 1, "uniform")
        for p in P.PATTERNS:
            self.assertEqual([o.delivered for o in again[p]],
                             [o.delivered for o in self.res[p]])


class BatonsOnlyAsymmetryIsTheStubCheck(unittest.TestCase):
    def test_with_no_stubs_baton_and_a_supervisor_deliver_alike(self):
        # Remove fake completions and the one mechanism baton is credited with
        # has nothing to catch; any gap left would be a thumb on the scale.
        saved = P.STUB_SHARE
        try:
            P.STUB_SHARE = 0.0
            res = P.run_all(300, 1, "uniform")
        finally:
            P.STUB_SHARE = saved
        silent = {p: sum(o.reported and not o.delivered for o in res[p])
                  for p in ("baton", "supervisor")}
        self.assertEqual(silent["baton"], silent["supervisor"])


if __name__ == "__main__":
    unittest.main()
