"""The packet is immutable and carries previews, never file contents."""
import dataclasses
import unittest

from kernel.baton import ArtifactRef, Baton, Decision, Kind


def a_baton(**kw):
    base = dict(trace_id="t1", hop=3, from_agent="cto", to_agent="frontend_engineer",
                goal="Implement the hero", rationale="design is settled")
    base.update(kw)
    return Baton(**base)


class ArtifactRefTests(unittest.TestCase):
    def test_carries_path_description_and_preview(self):
        art = ArtifactRef(path="workspace/spec.md", description="approved design spec",
                          preview="Sections 1-3 settle layout.\nSection 4 is open.")
        self.assertEqual("workspace/spec.md", art.path)
        self.assertIn("Section 4 is open.", art.preview)

    def test_render_shows_path_description_and_preview(self):
        art = ArtifactRef("workspace/spec.md", "approved design spec", "two line\nsummary")
        text = art.render()
        self.assertIn("workspace/spec.md", text)
        self.assertIn("approved design spec", text)
        self.assertIn("two line", text)

    def test_is_frozen(self):
        art = ArtifactRef("p", "d")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            art.path = "other"


class BatonTests(unittest.TestCase):
    def test_is_frozen(self):
        b = a_baton()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            b.to_agent = "someone_else"

    def test_to_dict_is_json_serialisable_and_flat(self):
        import json
        b = a_baton(artifacts=(ArtifactRef("w/spec.md", "spec", "prev"),),
                    open_questions=("is auth in scope?",))
        d = b.to_dict()
        json.dumps(d)                       # must not raise
        self.assertEqual("w/spec.md", d["artifacts"][0]["path"])
        self.assertEqual(["is auth in scope?"], list(d["open_questions"]))

    def test_render_includes_goal_and_artifact_previews(self):
        b = a_baton(artifacts=(ArtifactRef("w/spec.md", "spec", "the preview line"),))
        text = b.render()
        self.assertIn("Implement the hero", text)
        self.assertIn("w/spec.md", text)
        self.assertIn("the preview line", text)

    def test_render_omits_empty_sections(self):
        """A baton with no open questions must not render an empty heading —
        every rendered line is re-sent on every hop and costs tokens."""
        text = a_baton().render()
        self.assertNotIn("Open questions", text)
        self.assertNotIn("Stall notice", text)

    def test_render_surfaces_stall_notice_and_flags(self):
        b = a_baton(stall_notice="cto -> frontend_engineer has repeated",
                    flags=("malformed_routing",))
        text = b.render()
        self.assertIn("Stall notice", text)
        self.assertIn("has repeated", text)
        self.assertIn("malformed_routing", text)


class DecisionTests(unittest.TestCase):
    def test_kind_is_a_closed_vocabulary(self):
        self.assertEqual({"HANDOFF", "PROPOSE_DONE", "RATIFY", "REJECT"},
                         {k.value for k in Kind})

    def test_decision_is_frozen(self):
        d = Decision(kind=Kind.HANDOFF, to="tester")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            d.to = "ceo"


if __name__ == "__main__":
    unittest.main()
