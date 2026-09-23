"""A trail is a claim about a route, so the interesting cases are the ones where
the colony must NOT claim: an unfinished run, an edge with thin evidence, a
candidate the whitelist does not offer. The rest is the two swarm properties
that justify the module — shorter deliveries reinforce harder, and evaporation
plus the exploration floor let a colony follow a route that changes.
"""
import json
import random
import unittest

from baton import (GATE, AgentSpec, ArtifactRef, Charter, Decision,
                   DispatchResult, Kind, MemoryTrace, reputation, run, swarm)


def trace(path, reason="ratified", gate="gate"):
    """One run's records: `path` is the accepted routing decisions as
    (agent, kind, to) — `to` is ignored for PROPOSE_DONE, which goes to the gate."""
    recs = [{"event": "run_start", "charter": {"gate_agent": gate}}]
    for agent, kind, to in path:
        recs.append({"event": "decision", "agent": agent, "decision": kind,
                     "to": to})
    recs.append({"event": "run_end", "terminal_reason": reason,
                 "hops": len(path) + 1})
    return recs


HANDOFF, DONE, REJECT = "HANDOFF", "PROPOSE_DONE", "REJECT"


class Learning(unittest.TestCase):
    def test_a_ratified_run_lays_trail_on_every_edge_it_took(self):
        c = swarm.Colony()
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        self.assertGreater(c.trail("intake", "refunds").strength, 0)
        self.assertGreater(c.trail("refunds", "gate").strength, 0)

    def test_a_failed_run_is_counted_but_lays_nothing(self):
        c = swarm.Colony()
        c.observe(trace([("intake", HANDOFF, "docs")], reason="stalled"))
        t = c.trail("intake", "docs")
        self.assertEqual((t.runs, t.ratified, t.strength), (1, 0, 0.0))

    def test_an_unfinished_trace_is_not_a_failure(self):
        # No run_end means no outcome. Scoring it as a loss would punish a
        # route for a process that crashed or a file that was cut short.
        c = swarm.Colony()
        recs = trace([("intake", HANDOFF, "docs")])[:-1]
        self.assertFalse(c.observe(recs))
        self.assertIsNone(c.trail("intake", "docs"))
        self.assertEqual((c.observed, c.skipped), (0, 1))

    def test_a_shorter_delivery_reinforces_harder(self):
        c = swarm.Colony()
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        c.observe(trace([("intake", HANDOFF, "docs"), ("docs", HANDOFF, "engineering"),
                         ("engineering", HANDOFF, "docs"), ("docs", DONE, "")]))
        # refunds' edge has also evaporated once since; still stronger.
        self.assertGreater(c.trail("intake", "refunds").strength,
                           c.trail("intake", "docs").strength)

    def test_an_unused_trail_evaporates(self):
        c = swarm.Colony(evaporation=0.5)
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        before = c.trail("intake", "refunds").strength
        c.observe(trace([("intake", HANDOFF, "docs"), ("docs", DONE, "")]))
        self.assertAlmostEqual(c.trail("intake", "refunds").strength, before * 0.5)
        # The counts are evidence and do not decay.
        self.assertEqual(c.trail("intake", "refunds").runs, 1)

    def test_a_failed_run_erodes_the_edges_it_took(self):
        c = swarm.Colony(evaporation=0.1, penalty=0.5)
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        c.observe(trace([("intake", HANDOFF, "docs"), ("docs", DONE, "")]))
        before = c.trail("intake", "refunds").strength
        c.observe(trace([("intake", HANDOFF, "refunds")], reason="stalled"))
        self.assertAlmostEqual(c.trail("intake", "refunds").strength,
                               before * 0.9 * 0.5)
        # docs was not on the failed run: evaporation only.
        self.assertGreater(c.trail("intake", "docs").strength,
                           c.trail("intake", "refunds").strength)

    def test_evaporation_alone_moves_the_odds(self):
        # The floor is absolute. Were it relative to the strongest trail, the
        # weights would be scale-invariant and a decaying route would keep its
        # full share of traffic forever.
        c = swarm.Colony(evaporation=0.3, penalty=0.0)
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        fresh = c.weights("intake", {"refunds", "docs"})["refunds"]
        for _ in range(6):
            c.observe(trace([("other", HANDOFF, "x"), ("x", DONE, "")]))
        self.assertLess(c.weights("intake", {"refunds", "docs"})["refunds"], fresh)

    def test_a_trail_is_bounded(self):
        # Always-ratified one-hop route: steady state is deposit / evaporation.
        c = swarm.Colony(evaporation=0.2, deposit=1.0)
        for _ in range(200):
            c.observe([{"event": "run_start", "charter": {"gate_agent": "g"}},
                       {"event": "decision", "agent": "w", "decision": DONE},
                       {"event": "run_end", "terminal_reason": "ratified", "hops": 1}])
        self.assertLessEqual(c.trail("w", "g").strength, 1.0 / 0.2 + 1e-9)

    def test_a_gate_reject_is_a_routing_edge_too(self):
        c = swarm.Colony()
        c.observe(trace([("w", DONE, ""), ("gate", REJECT, "w"), ("w", DONE, "")]))
        self.assertEqual(c.trail("gate", "w").ratified, 1)

    def test_bad_parameters_are_refused(self):
        for kw in ({"evaporation": 0}, {"evaporation": 1.5}, {"deposit": 0},
                   {"penalty": -0.1}, {"penalty": 1.5}, {"min_runs": 0}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                swarm.Colony(**kw)


class Weighing(unittest.TestCase):
    def setUp(self):
        self.c = swarm.Colony()
        for _ in range(5):
            self.c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))

    def test_no_evidence_means_no_opinion(self):
        w = swarm.Colony().weights("intake", {"a", "b", "c"})
        self.assertEqual(set(w.values()), {1 / 3})

    def test_probabilities_cover_exactly_the_candidates(self):
        w = self.c.weights("intake", {"refunds", "docs"})
        self.assertEqual(set(w), {"refunds", "docs"})
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_the_reinforced_edge_is_favoured(self):
        w = self.c.weights("intake", {"refunds", "docs", "engineering"})
        self.assertGreater(w["refunds"], w["docs"])

    def test_no_legal_candidate_is_ever_ruled_out(self):
        w = self.c.weights("intake", {"refunds", "docs"}, alpha=4)
        self.assertGreater(w["docs"], 0)

    def test_a_trail_to_an_agent_not_offered_is_ignored(self):
        # The colony knows intake->refunds; the whitelist here does not offer it.
        w = self.c.weights("intake", {"docs", "engineering"})
        self.assertEqual(w, {"docs": 0.5, "engineering": 0.5})

    def test_alpha_zero_ignores_the_trail(self):
        w = self.c.weights("intake", {"refunds", "docs"}, alpha=0)
        self.assertAlmostEqual(w["refunds"], 0.5)

    def test_reputation_is_the_heuristic_term(self):
        rep = reputation.from_tallies({"docs": 10, "engineering": 10},
                                      {"docs": 8, "engineering": 1})
        eta = swarm.from_reputation(rep, unknown=0.5)
        w = swarm.Colony().weights("intake", {"docs", "engineering"},
                                   beta=1, heuristic=eta)
        self.assertGreater(w["engineering"], w["docs"])

    def test_an_unknown_agent_has_no_default_score(self):
        with self.assertRaises(TypeError):
            swarm.from_reputation(reputation.from_tallies({}, {}))  # noqa

    def test_beta_without_a_heuristic_is_refused(self):
        with self.assertRaises(ValueError):
            self.c.weights("intake", {"docs"}, beta=1)

    def test_choose_is_reproducible(self):
        picks = [self.c.choose("intake", {"refunds", "docs"}, random.Random(7))
                 for _ in range(3)]
        self.assertEqual(len(set(picks)), 1)


class Noting(unittest.TestCase):
    def test_thin_evidence_is_not_mentioned(self):
        c = swarm.Colony(min_runs=3)
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        self.assertEqual(c.note("intake", {"refunds", "docs"}), "")

    def test_the_note_states_counts(self):
        c = swarm.Colony(min_runs=2)
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        c.observe(trace([("intake", HANDOFF, "refunds")], reason="stalled"))
        self.assertIn("refunds ratified in 1 of 2 past runs",
                      c.note("intake", {"refunds"}))

    def test_annotate_never_touches_the_whitelist(self):
        c = swarm.Colony(min_runs=1)
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        agents = {"intake": AgentSpec("intake", "Route it.", {"refunds", "docs", "gate"})}
        out = c.annotate(agents)
        self.assertEqual(out["intake"].can_hand_to, agents["intake"].can_hand_to)
        self.assertIn("Route history", out["intake"].instructions)
        self.assertEqual(agents["intake"].instructions, "Route it.")


class Persistence(unittest.TestCase):
    def test_round_trips_through_json(self):
        c = swarm.Colony(evaporation=0.3, min_runs=2)
        c.observe(trace([("intake", HANDOFF, "refunds"), ("refunds", DONE, "")]))
        c.observe(trace([("intake", HANDOFF, "docs")], reason="stalled"))
        back = swarm.Colony.from_dict(json.loads(json.dumps(c.to_dict())))
        self.assertEqual(back.to_dict(), c.to_dict())
        self.assertEqual(back.weights("intake", {"refunds", "docs"}),
                         c.weights("intake", {"refunds", "docs"}))


# --- against the real runtime -------------------------------------------------

SPECIALISTS = ("refunds", "engineering", "docs")
AGENTS = {
    "intake": AgentSpec("intake", "Route the ticket.", set(SPECIALISTS) | {"gate"}),
    **{s: AgentSpec(s, f"You are {s}.", {"gate", "intake"}) for s in SPECIALISTS},
    "gate": AgentSpec("gate", "Judge.", set(SPECIALISTS) | {"intake"}, role=GATE),
}
CHARTER = Charter(brief="Resolve the ticket.", entry_agent="intake",
                  gate_agent="gate", agent_pool=frozenset(AGENTS),
                  acceptance_criteria=("a reply", "an internal note"),
                  budget_ceiling_usd=1.0, max_hops=6)


def colony_dispatch(colony, rng, good):
    """intake routes by sampling the colony; the gate ratifies only work from
    the specialist that can actually resolve this kind of ticket."""
    def dispatch(agent, baton, prompt):
        if agent.name == "intake":
            to = colony.choose("intake", set(SPECIALISTS), rng)
            d = Decision(kind=Kind.HANDOFF, to=to, goal="resolve", rationale="colony")
        elif agent.name == "gate":
            if baton.from_agent == good:
                d = Decision(kind=Kind.RATIFY, summary="ok",
                             coverage=("reply.md", "note.md"))
            else:
                d = Decision(kind=Kind.REJECT, to=baton.from_agent, reason="wrong desk")
        else:
            d = Decision(kind=Kind.PROPOSE_DONE, summary="done", artifacts=(
                ArtifactRef("reply.md", "reply", content="A real reply. " * 12),
                ArtifactRef("note.md", "note", content="A real note. " * 12)))
        return DispatchResult(text=d.render(), in_tokens=1, out_tokens=1)
    return dispatch


class AgainstTheRuntime(unittest.TestCase):
    def test_observe_result_reads_a_real_trace(self):
        c = swarm.Colony()
        r = run(CHARTER, AGENTS, colony_dispatch(c, random.Random(1), "refunds"),
                trace=MemoryTrace("t"))
        self.assertTrue(c.observe_result(r))
        self.assertEqual(c.observed, 1)
        first = r.path[1]
        self.assertEqual(c.trail("intake", first).runs, 1)

    def test_the_colony_follows_a_route_that_changes(self):
        # The stigmergy claim, in miniature: after enough ratified runs the
        # right desk dominates; when the right desk CHANGES, evaporation and
        # the exploration floor let the colony find the new one.
        c = swarm.Colony(evaporation=0.2)
        rng = random.Random(2026)

        def share(good, n):
            hits = 0
            for i in range(n):
                r = run(CHARTER, AGENTS, colony_dispatch(c, rng, good),
                        trace=MemoryTrace(f"{good}{i}"))
                c.observe_result(r)
                hits += r.path[1] == good
            return hits / n

        share("refunds", 40)
        self.assertGreater(c.weights("intake", SPECIALISTS)["refunds"], 0.8)
        share("docs", 60)
        self.assertGreater(c.weights("intake", SPECIALISTS)["docs"], 0.8)

    def test_the_whitelist_still_wins(self):
        # A colony that has only ever seen intake->refunds cannot make intake
        # reach an agent the charter does not offer it.
        c = swarm.Colony(min_runs=1)
        c.observe(trace([("intake", HANDOFF, "billing_ghost"), ("billing_ghost", DONE, "")]))
        self.assertEqual(c.annotate(AGENTS)["intake"].instructions, "Route the ticket.")


if __name__ == "__main__":
    unittest.main()
