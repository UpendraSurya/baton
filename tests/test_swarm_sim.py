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


def ep(*picks, delivered=""):
    """Trace records for one ticket: intake's handoffs in order, and the desk
    the run was ratified from (if any)."""
    recs = [{"event": "run_start", "charter": {"gate_agent": "gate"}}]
    for i, d in enumerate(picks):
        recs += [{"event": "decision", "agent": "intake", "decision": "HANDOFF", "to": d},
                 {"event": "decision", "agent": d, "decision": "PROPOSE_DONE"}]
        if d != delivered:
            recs.append({"event": "decision", "agent": "gate",
                         "decision": "REJECT", "to": "intake"})
    recs.append({"event": "run_end", "hops": 3 * len(picks),
                 "terminal_reason": "ratified" if delivered else "hops_exhausted"})
    return recs


class TheLearnersReadOnlyTheTrace(unittest.TestCase):
    def test_episode_names_the_delivering_desk(self):
        self.assertEqual(S.episode(ep("docs", "engineering", delivered="engineering")),
                         (["docs", "engineering"], "engineering"))
        self.assertEqual(S.episode(ep("docs", "refunds")), (["docs", "refunds"], ""))


class ThompsonSampling(unittest.TestCase):
    def test_a_delivery_is_a_success_and_a_misroute_a_failure(self):
        ts = S.Thompson(S.random.Random(0))
        ts.learn("howto", ep("docs", "engineering", delivered="engineering"))
        a_docs, b_docs = ts.ab[("howto", "docs")]
        a_eng, b_eng = ts.ab[("howto", "engineering")]
        self.assertGreater(b_docs, 1.0)          # docs failed once
        self.assertGreater(a_eng, 1.0)           # engineering delivered once
        self.assertEqual(b_eng, 1.0)

    def test_evidence_overrides_the_prior(self):
        ts = S.Thompson(S.random.Random(0))
        for _ in range(40):
            ts.learn("howto", ep("docs", "engineering", delivered="engineering"))
        picks = [ts.pick("howto", ["docs", "engineering"]) for _ in range(200)]
        self.assertGreater(picks.count("engineering"), 150)


class QLearning(unittest.TestCase):
    def test_a_delivery_pulls_q_toward_one_minus_cost(self):
        ql = S.QRouter(S.random.Random(0))
        before = ql._q("bug", (), "engineering")
        ql.learn("bug", ep("engineering", delivered="engineering"))
        after = ql._q("bug", (), "engineering")
        self.assertAlmostEqual(after, before + S.Q_LR * ((1 - S.Q_STEP_COST) - before))

    def test_a_misroute_bootstraps_from_the_next_state(self):
        # The first pick's value includes what re-routing from there is worth:
        # updated last step first, the delivery reaches it in the same episode.
        ql = S.QRouter(S.random.Random(0))
        ql.learn("howto", ep("docs", "engineering", delivered="engineering"))
        nxt = max(ql._q("howto", ("docs",), d) for d in S.DESKS if d != "docs")
        expect = 0.5 + S.Q_LR * ((-S.Q_STEP_COST + S.Q_GAMMA * nxt) - 0.5)
        self.assertAlmostEqual(ql._q("howto", (), "docs"), expect)


if __name__ == "__main__":
    unittest.main()
