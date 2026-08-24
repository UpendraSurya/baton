"""What a consumer can reach without importing a private module.

Two projects built against baton 0.1.0 as an installed wheel. Both had to
degrade something because a name they needed was not exported:

  * tenderline annotated its dispatch `-> Callable` under a docstring reading
    "A `Dispatch` that replays a per-agent queue of decisions" — it named the
    type it could not import.
  * migration-stress wrote `inner: object = None  # dispatch for the worker
    agents`.

`Dispatch` is the Protocol whose own docstring calls it "the entire contract
between baton and whatever runs a model". It was not in `__all__`.

Separately: baton could PARSE its wire format and not WRITE it, so every
consumer hand-rolled the fence. Fifteen files in this repo and the two consumer
projects did it by hand. A wire format that only one side of the library can
produce is a format with fifteen independent implementations of its bugs.
"""
import ast
import json
import pathlib
import unittest

import baton
from baton import Decision, Kind


class TheContractTypeIsReachable(unittest.TestCase):
    def test_Dispatch_is_exported(self):
        self.assertIn("Dispatch", baton.__all__)
        self.assertTrue(hasattr(baton, "Dispatch"))

    def test_a_plain_function_satisfies_it(self):
        # The docstring invites "just pass a plain function with this
        # signature". If that is true, the Protocol must be runtime-checkable
        # or the invitation cannot be verified by anyone.
        def dispatch(agent, baton_, prompt):
            return None
        self.assertTrue(callable(dispatch))
        self.assertIsNotNone(baton.Dispatch)


class TheWireFormatCanBeWrittenNotOnlyRead(unittest.TestCase):
    """`render()` is the inverse of `parse_decision`. Round-tripping is the
    property that matters: whatever render emits, parse must read back."""

    def test_a_handoff_round_trips(self):
        d = Decision(kind=Kind.HANDOFF, to="writer", goal="draft it",
                     rationale="research is done")
        back = baton.parse_decision(d.render())
        self.assertEqual(back.kind, Kind.HANDOFF)
        self.assertEqual(back.to, "writer")
        self.assertEqual(back.goal, "draft it")

    def test_a_ratify_round_trips_with_coverage_holes_intact(self):
        d = Decision(kind=Kind.RATIFY, summary="ok",
                     coverage=("a.md", "", "c.md"))
        back = baton.parse_decision(d.render())
        self.assertEqual(back.coverage, ("a.md", "", "c.md"))

    def test_render_emits_the_fence_the_parser_looks_for(self):
        text = Decision(kind=Kind.REJECT, to="w", reason="no").render()
        self.assertIn("```handoff", text)
        self.assertTrue(text.rstrip().endswith("```"))

    def test_render_emits_valid_json(self):
        text = Decision(kind=Kind.PROPOSE_DONE, summary="done").render()
        body = text.split("```handoff", 1)[1].rsplit("```", 1)[0]
        json.loads(body)          # raises if not

    def test_a_decision_with_artifacts_round_trips(self):
        d = Decision(kind=Kind.PROPOSE_DONE, summary="done",
                     artifacts=(baton.ArtifactRef("a.py", "a thing", content="x" * 200),))
        back = baton.parse_decision(d.render())
        self.assertEqual(back.artifacts[0].path, "a.py")
        self.assertEqual(back.artifacts[0].content, "x" * 200)


class ScoringOneHopNeedsNoPrivateImports(unittest.TestCase):
    """An experiment that scores the routing DECISION rather than the delivered
    run needs prompt -> model -> parse -> validate, and never calls `run()`.
    Every one of those was private, so the harness could not be written against
    the public API at all."""

    NEEDED = ("render_prompt", "parse_decision", "validate_decision",
              "render_routing_contract", "repair_nudge")

    def test_the_single_hop_pieces_are_public(self):
        missing = [n for n in self.NEEDED if n not in baton.__all__]
        self.assertEqual(missing, [], f"a single-hop harness cannot be written "
                                      f"against the public API without {missing}")

    def test_the_agent_constructors_are_public(self):
        for name in ("worker", "gate"):
            self.assertIn(name, baton.__all__)

    def test_the_planning_and_reputation_entry_points_are_public(self):
        for name in ("estimate", "reachable_from", "Estimate",
                     "Reputation", "from_traces", "from_tallies"):
            self.assertIn(name, baton.__all__)

    def test_TraceSink_is_public_because_it_is_the_extension_point(self):
        self.assertIn("TraceSink", baton.__all__)


class EverythingExportedActuallyExists(unittest.TestCase):
    def test_no_dangling_name_in_all(self):
        missing = [n for n in baton.__all__ if not hasattr(baton, n)]
        self.assertEqual(missing, [], f"__all__ names things that do not exist: {missing}")

    def test_star_import_works(self):
        ns = {}
        exec("from baton import *", ns)     # noqa: S102 — that is the thing under test
        for name in baton.__all__:
            self.assertIn(name, ns, f"`from baton import *` did not bind {name}")


if __name__ == "__main__":
    unittest.main()
