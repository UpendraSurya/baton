"""
baton.crossover — should you route at all?

The founding thesis, "dynamic routing beats a static DAG", is false at zero
path variance and was never provable as stated. The provable form is a
crossover: routing pays iff the price ratio rho (one specialist hop / one
routing call) exceeds a threshold computed from measured quantities. That rule
predicted the empirical winner in 30/30 cells of a frozen 620-item sweep
(~/unlimited/pathvar/crossover.py). These tests pin the module to those rows.
"""
import math
import unittest

from baton import crossover
from baton.crossover import Crossover, estimate

MODAL = frozenset({"date", "currency"})
POOL = ("currency", "date", "email", "id", "name", "phone")


def corpus(variance: float, n: int = 500):
    """A task log with the pathvar shape: `variance` is the fraction of tasks
    whose needs are NOT the modal set. Off-modal tasks cycle through singleton
    sets outside the modal set, so p_modal == 1 - variance exactly (n=500 makes
    every frozen p_M below representable: 367, 274, 149 and 24 of 500)."""
    others = [frozenset({d}) for d in POOL if d not in MODAL]
    k = round(n * variance)
    return [MODAL] * (n - k) + [others[i % len(others)] for i in range(k)]


# (variance, a, E, p_M, rho_star) — the frozen sweep rows, a and E measured on
# mistral-medium, p_M a pure function of the corpus. rho_star is what
# pathvar/crossover.py printed and what matched the empirical winner 30/30.
FROZEN_ROWS = [
    (0.00, 0.935, 1.88, 1.000, math.inf),
    (0.25, 0.879, 1.91, 0.734, 2.06),
    (0.50, 0.839, 1.94, 0.548, 0.90),
    (0.75, 0.758, 1.95, 0.298, 0.39),
    (1.00, 0.669, 1.95, 0.048, 0.48),
]


class TheFrozenRowsReproduce(unittest.TestCase):
    """The threshold from (a, E, p_M) alone must equal what the validated
    script printed. This is the link between the library and the evidence."""

    def test_rho_star_matches_the_30_of_30_table(self):
        for variance, a, e, p_m, want in FROZEN_ROWS:
            with self.subTest(variance=variance):
                c = estimate(paths=corpus(1 - p_m), perception_accuracy=a,
                             hops_per_routed_task=e,
                             static_candidates=[MODAL, frozenset(POOL)])
                self.assertAlmostEqual(p_m, c.candidates[0].success, places=6)
                if math.isinf(want):
                    self.assertTrue(math.isinf(c.rho_star))
                else:
                    self.assertAlmostEqual(want, c.rho_star, delta=0.01)

    def test_the_empirical_winner_is_predicted_at_every_price(self):
        # pathvar's empirical winners at rho = 0.25, 0.5, 1, 2, 4, 8
        expect = {0.00: "SSSSSS", 0.25: "SSSSRR", 0.50: "SSRRRR",
                  0.75: "SRRRRR", 1.00: "SRRRRR"}
        for variance, a, e, p_m, _ in FROZEN_ROWS:
            got = ""
            for rho in (0.25, 0.5, 1, 2, 4, 8):
                c = estimate(paths=corpus(1 - p_m), perception_accuracy=a,
                             hops_per_routed_task=e, price_ratio=rho,
                             static_candidates=[MODAL, frozenset(POOL)])
                got += "R" if c.verdict == "route" else "S"
            self.assertEqual(expect[variance], got, variance)


class ZeroVarianceNeverRoutes(unittest.TestCase):
    def test_a_homogeneous_log_makes_routing_pure_overhead(self):
        c = estimate(paths=[MODAL] * 50, perception_accuracy=0.99, price_ratio=1000)
        self.assertTrue(math.isinf(c.rho_star))
        self.assertEqual("fixed_path", c.verdict)
        self.assertEqual(0.0, c.path_variance)
        self.assertEqual(MODAL, c.best_static.path)

    def test_a_perfect_router_still_loses_at_zero_variance(self):
        # Even a = 1.0: the fixed path is free of routing calls and never wrong.
        c = estimate(paths=[MODAL] * 50, perception_accuracy=1.0, price_ratio=8)
        self.assertEqual("fixed_path", c.verdict)


class TheThresholdMovesTheRightWay(unittest.TestCase):
    def test_better_perception_never_raises_the_threshold(self):
        paths = corpus(0.5)
        prev = math.inf
        for a in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
            r = estimate(paths=paths, perception_accuracy=a).rho_star
            self.assertLessEqual(r, prev)
            prev = r

    def test_more_variance_never_raises_the_threshold(self):
        """Under the validated two-candidate rule with E held fixed. Under
        exact search the best fixed path itself changes shape with the
        corpus, so 1 - modal frequency is no longer the governing quantity."""
        prev = math.inf
        for v in (0.0, 0.25, 0.5, 0.75, 1.0):
            r = estimate(paths=corpus(v), perception_accuracy=0.85,
                         hops_per_routed_task=2.0,
                         static_candidates=[MODAL, frozenset(POOL)]).rho_star
            self.assertLessEqual(r, prev)
            prev = r

    def test_every_input_is_load_bearing(self):
        """Anti-vacuity: perturb each input alone and the threshold changes."""
        base = dict(paths=corpus(0.5), perception_accuracy=0.85,
                    hops_per_routed_task=2.0, routing_calls_per_task=1.0)
        ref = estimate(**base).rho_star
        for key, val in (("perception_accuracy", 0.7), ("hops_per_routed_task", 3.0),
                         ("routing_calls_per_task", 3.0), ("paths", corpus(0.9))):
            with self.subTest(key=key):
                self.assertNotAlmostEqual(ref, estimate(**{**base, key: val}).rho_star)


class TheBestStaticPathIsFoundNotAssumed(unittest.TestCase):
    def test_exact_search_is_used_on_a_small_pool(self):
        c = estimate(paths=corpus(0.5), perception_accuracy=0.85)
        self.assertTrue(c.exact_search)

    def test_exact_search_beats_the_greedy_candidates_when_they_differ(self):
        # Greedy cumulative unions consider {a,b}, {a,b,c,d}, {a,b,c,d,e};
        # exact search also considers {c,d} alone and every other subset, so
        # its best fixed path can never cost more than greedy's.
        paths = ([frozenset("ab")] * 40 + [frozenset("cd")] * 35
                 + [frozenset("abe")] * 25)
        exact = estimate(paths=paths, perception_accuracy=0.9, price_ratio=1)
        greedy = estimate(paths=paths, perception_accuracy=0.9, price_ratio=1,
                          exact_search=False)
        self.assertTrue(exact.exact_search)
        self.assertFalse(greedy.exact_search)
        # Not a tie — the search is doing something. Exact finds {a,b,e},
        # greedy only ever considers the cumulative unions and stops at {a,b}.
        self.assertNotEqual(exact.best_static.path, greedy.best_static.path)
        self.assertLess(exact.best_static.cost_per_success,
                        greedy.best_static.cost_per_success)

    def test_a_better_static_baseline_RAISES_the_bar_routing_must_clear(self):
        """The honesty property. rho* is the price at which routing beats the
        BEST fixed path; comparing against a strawman would make routing look
        good by understating the alternative. So the exact search's threshold
        must never be lower than the greedy one's."""
        paths = ([frozenset("ab")] * 40 + [frozenset("cd")] * 35
                 + [frozenset("abe")] * 25)
        exact = estimate(paths=paths, perception_accuracy=0.9)
        greedy = estimate(paths=paths, perception_accuracy=0.9, exact_search=False)
        self.assertGreater(exact.rho_star, greedy.rho_star)

    def test_a_large_pool_falls_back_to_greedy_and_says_so(self):
        pool = [f"a{i}" for i in range(20)]
        paths = [frozenset(pool[:2])] * 10 + [frozenset(pool[i:i + 3]) for i in range(18)]
        c = estimate(paths=paths, perception_accuracy=0.9)
        self.assertFalse(c.exact_search)
        self.assertGreaterEqual(c.rho_star, 0.0)
        self.assertEqual(20, c.pool_size)


class TheResultIsComplete(unittest.TestCase):
    def test_every_field_is_populated_and_render_mentions_the_verdict(self):
        c = estimate(paths=corpus(0.5), perception_accuracy=0.85, price_ratio=2.0)
        self.assertIsInstance(c, Crossover)
        self.assertEqual(500, c.n_tasks)
        self.assertEqual(6, c.pool_size)
        self.assertAlmostEqual(0.5, c.path_variance, places=6)
        self.assertGreater(c.dynamic_cost_per_success, 0)
        self.assertGreater(c.best_static.cost_per_success, 0)
        self.assertIn(c.verdict, ("route", "fixed_path"))
        text = c.render()
        self.assertIn(c.verdict, text)
        self.assertIn("rho*", text)

    def test_without_a_price_the_verdict_is_undecided_but_rho_star_is_not(self):
        c = estimate(paths=corpus(0.5), perception_accuracy=0.85)
        self.assertEqual("undecided", c.verdict)
        self.assertTrue(math.isfinite(c.rho_star))


class BadInputsAreRefusedNotGuessed(unittest.TestCase):
    def test_no_paths(self):
        with self.assertRaises(ValueError):
            estimate(paths=[], perception_accuracy=0.9)

    def test_an_empty_path(self):
        with self.assertRaises(ValueError):
            estimate(paths=[MODAL, frozenset()], perception_accuracy=0.9)

    def test_accuracy_out_of_range(self):
        for a in (0.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                estimate(paths=corpus(0.5), perception_accuracy=a)

    def test_accuracy_has_no_default(self):
        with self.assertRaises(TypeError):
            estimate(paths=corpus(0.5))  # type: ignore[call-arg]

    def test_nonpositive_price_ratio(self):
        with self.assertRaises(ValueError):
            estimate(paths=corpus(0.5), perception_accuracy=0.9, price_ratio=0)

    def test_nonpositive_routing_calls(self):
        with self.assertRaises(ValueError):
            estimate(paths=corpus(0.5), perception_accuracy=0.9,
                     routing_calls_per_task=0)

    def test_negative_hops(self):
        with self.assertRaises(ValueError):
            estimate(paths=corpus(0.5), perception_accuracy=0.9,
                     hops_per_routed_task=-1)


class ItIsPublic(unittest.TestCase):
    def test_exported_from_the_package(self):
        import baton
        self.assertIn("crossover", baton.__all__)
        self.assertIn("Crossover", baton.__all__)
        self.assertIs(baton.crossover, crossover)


if __name__ == "__main__":
    unittest.main()
