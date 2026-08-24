"""What two outside consumers each had to work around (2026-08-24).

`tenderline` and `migration-stress` both use baton as an installed dependency
and never edit it. Building against 0.1.0 they independently hit the same wall
and each invented a different workaround for it:

  * tenderline emits a full-length coverage array with an `"UNANSWERED:R-013"`
    sentinel string in the hole, because a SHORT array says only "the tail is
    unaccounted for" — and if the gap is in the middle, that answer is wrong.
    The identity of the unanswered requirement is its entire product.
  * migration-stress splits one migration into seven patch files so that
    coverage names seven distinct artifacts, because its gate ran `pytest` and
    got refused by a heuristic written for gates that guess.

Neither workaround should have been necessary. Two out of two consumers is not
a coincidence; it is a missing feature reported twice.
"""
import json
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.packet import MAX_CONTENT_CHARS, ArtifactRef
from baton.runtime import DispatchResult, Guards, run
from baton.trace import MemoryTrace

CRITERIA = ("R-001 a working webhook", "R-002 a deployment file",
            "R-003 a runbook")
REAL = "x" * 900


def done(*arts):
    return ('```handoff\n' + json.dumps({
        "decision": "PROPOSE_DONE", "summary": "done",
        "artifacts": [{"path": p, "description": "a thing", "content": REAL}
                      for p in arts]}) + '\n```')


def ratify(coverage):
    return ('```handoff\n' + json.dumps(
        {"decision": "RATIFY", "summary": "ok", "coverage": coverage}) + '\n```')


def roster():
    return {"w": AgentSpec("w", "# W", frozenset({"g"})),
            "g": AgentSpec("g", "# G", frozenset({"w"}), role=GATE)}


def charter(**kw):
    base = dict(brief="b", entry_agent="w", gate_agent="g",
                agent_pool=frozenset(roster()), acceptance_criteria=CRITERIA,
                budget_ceiling_usd=100.0, max_hops=6)
    base.update(kw)
    return Charter(**base)


def go(lines, **kw):
    script = list(lines)

    def dispatch(agent, baton, prompt):
        return DispatchResult(text=script.pop(0), cost_usd=0.001,
                              in_tokens=1, out_tokens=1)

    return run(charter(), roster(), dispatch, trace=MemoryTrace("t"), **kw)


class AHoleInTheMiddleIsRepresentable(unittest.TestCase):
    """GAP: coverage is positional, so a gate that cannot answer criterion 2
    has no way to say so and still cite 1 and 3. Skipping it shifts every later
    entry up by one — the array stays plausible and now cites the WRONG
    artifact for every criterion past the gap."""

    def test_null_marks_the_unanswered_criterion(self):
        r = go([done("hook.py", "runbook.md"),
                ratify(["hook.py", None, "runbook.md"]),
                ratify(["hook.py", None, "runbook.md"])])
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")

    def test_the_refusal_names_the_criterion_in_the_HOLE(self):
        # Not the tail. R-002 is the one nothing answers; R-003 is cited and fine.
        r = go([done("hook.py", "runbook.md"),
                ratify(["hook.py", None, "runbook.md"]),
                ratify(["hook.py", None, "runbook.md"])])
        self.assertIn("R-002", r.note)
        self.assertNotIn("R-003", r.note)

    def test_an_empty_string_is_a_hole_too(self):
        # A model that emits "" rather than null means the same thing.
        r = go([done("hook.py", "runbook.md"),
                ratify(["hook.py", "", "runbook.md"]),
                ratify(["hook.py", "", "runbook.md"])])
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")
        self.assertIn("R-002", r.note)

    def test_a_hole_is_never_read_as_a_missing_file(self):
        # The old parser stringified None into the literal "None", which came
        # back as "the gate cited artifacts that do not exist: None".
        r = go([done("hook.py", "runbook.md"),
                ratify(["hook.py", None, "runbook.md"]),
                ratify(["hook.py", None, "runbook.md"])])
        self.assertNotIn("None", r.note)

    def test_alignment_survives_the_hole(self):
        # The point of the whole feature: entry i still answers criterion i.
        r = go([done("hook.py", "deploy.yml", "runbook.md"),
                ratify(["hook.py", "deploy.yml", "runbook.md"])])
        self.assertEqual(r.terminal_reason, "ratified")
        self.assertEqual(r.coverage[2], "runbook.md")


class MeasuredCoverageIsNotGuessedCoverage(unittest.TestCase):
    """GAP: the single-citation heuristic exists because a MODEL asked 'which
    file satisfies criterion 3?' will cite the same file three times. A gate
    that executed the criteria did not cite anything — it reported. Refusing
    that is a false negative on a correct run, and 0.1.0 offered no opt-out
    short of disabling coverage checking entirely."""

    def test_one_artifact_for_every_criterion_is_still_refused_by_default(self):
        r = go([done("all.patch"),
                ratify(["all.patch"] * 3), ratify(["all.patch"] * 3)])
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")
        self.assertIn("independent", r.note)

    def test_a_measuring_gate_can_switch_off_just_that_rule(self):
        r = go([done("all.patch"), ratify(["all.patch"] * 3)],
               guards=Guards(require_independent_work=False))
        self.assertEqual(r.terminal_reason, "ratified")

    def test_switching_it_off_keeps_every_other_coverage_check(self):
        # The opt-out must not become a way to ratify a phantom citation.
        r = go([done("all.patch"),
                ratify(["all.patch", "ghost.patch", "all.patch"]),
                ratify(["all.patch", "ghost.patch", "all.patch"])],
               guards=Guards(require_independent_work=False))
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")
        self.assertIn("ghost.patch", r.note)

    def test_and_it_keeps_the_hole_check(self):
        r = go([done("all.patch"),
                ratify(["all.patch", None, "all.patch"]),
                ratify(["all.patch", None, "all.patch"])],
               guards=Guards(require_independent_work=False))
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")
        self.assertIn("R-002", r.note)


class TruncationIsVisible(unittest.TestCase):
    """GAP: content over MAX_CONTENT_CHARS is silently cut. A truncated diff is
    not a smaller diff, it is a corrupt one — `git apply` rejects it, and the
    run reads as an agent that cannot write a patch rather than as a packet
    limit. The cut is correct; its invisibility is the defect."""

    def test_an_untouched_artifact_is_not_marked_truncated(self):
        self.assertFalse(ArtifactRef("a.py", content="short").truncated)

    def test_a_cut_artifact_says_so(self):
        big = ArtifactRef("a.patch", content="d" * (MAX_CONTENT_CHARS + 1))
        self.assertTrue(big.truncated)

    def test_the_flag_survives_becoming_a_pointer(self):
        # An artifact loses its content when it becomes history; it must not
        # lose the fact that what it carried was already incomplete.
        big = ArtifactRef("a.patch", content="d" * (MAX_CONTENT_CHARS + 1))
        self.assertTrue(big.without_content().truncated)

    def test_the_flag_crosses_the_wire(self):
        big = ArtifactRef("a.patch", content="d" * (MAX_CONTENT_CHARS + 1))
        self.assertTrue(big.to_dict()["truncated"])


if __name__ == "__main__":
    unittest.main()
