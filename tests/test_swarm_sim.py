"""bench/swarm_sim.py is tier 1 — $0 and deterministic — so it is kept honest
by the suite rather than left to rot beside a README that quotes it. These are
the invariants that must hold for the benchmark to mean anything at all, not
the numbers it reports."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "bench"))

import swarm_sim as S  # noqa: E402


class TheBenchmarkMeansSomething(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = {sc.name: S.run_scenario(sc, 30, 1) for sc in S.SCENARIOS}

    def rate(self, scenario, arm):
        o = self.res[scenario][arm].outcomes
        return sum(o) / len(o)

    def test_the_oracle_is_a_ceiling(self):
        # A gate that knows the answer and a router that knows it too: if this
        # is not near-perfect, the harness is broken, not the router.
        for sc in S.SCENARIOS:
            with self.subTest(scenario=sc.name):
                self.assertGreaterEqual(self.rate(sc.name, "oracle"), 0.9)

    def test_on_one_ticket_class_the_drawn_graph_is_the_oracle(self):
        self.assertEqual(self.res["homogeneous"]["static"].outcomes,
                         self.res["homogeneous"]["oracle"].outcomes)

    def test_arms_are_paired(self):
        for sc in S.SCENARIOS:
            lens = {len(r.outcomes) for r in self.res[sc.name].values()}
            self.assertEqual(lens, {30})

    def test_it_is_deterministic(self):
        again = S.run_scenario(S.SCENARIOS[1], 30, 1)
        for arm in S.ARMS:
            self.assertEqual(again[arm].outcomes, self.res["mixed"][arm].outcomes)

    def test_the_report_renders_every_arm(self):
        text = S.report(self.res, 30, 1, 0.0)
        for arm in S.ARMS:
            self.assertIn(f"| {arm} |", text)

    def test_mcnemar_is_exact(self):
        self.assertEqual(S.mcnemar([1, 0], [1, 0]), (0, 0, 1.0))
        x, y, p = S.mcnemar([1] * 6 + [0] * 2, [0] * 6 + [1] * 2)
        self.assertEqual((x, y), (6, 2))
        self.assertAlmostEqual(p, 0.2890625)


if __name__ == "__main__":
    unittest.main()
