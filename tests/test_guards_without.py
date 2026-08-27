"""`Guards` has no "with this off" helper.

Defect found by real use: every guard defaults to True, so switching ONE off
from a Guards a caller already holds (not the all-defaults instance) meant
reading every field it was already configured with and re-passing them all —
`dataclasses.replace` works but nothing on Guards itself offers the one-line
form, and both tests and callers reach for this constantly (see
test_anti_vacuity.py and test_every_guard_is_falsifiable.py, which construct
one-off-at-a-time Guards by hand throughout).
"""
import dataclasses
import unittest

from baton.runtime import Guards


class GuardsWithout(unittest.TestCase):
    def test_without_turns_off_exactly_the_named_guard(self):
        g = Guards().without("preflight")
        self.assertFalse(g.preflight)
        # every other field keeps its default
        for f in dataclasses.fields(Guards):
            if f.name != "preflight":
                self.assertEqual(getattr(g, f.name), f.default, f.name)

    def test_without_preserves_fields_already_customised(self):
        """The point of the helper: turning off ONE MORE guard must not lose
        customisation already present on the instance it is called on."""
        g = Guards(hops=False).without("budget")
        self.assertFalse(g.hops)
        self.assertFalse(g.budget)
        self.assertTrue(g.cycle)

    def test_without_accepts_multiple_names(self):
        g = Guards().without("hops", "budget")
        self.assertFalse(g.hops)
        self.assertFalse(g.budget)
        self.assertTrue(g.cycle)

    def test_without_is_a_new_object_the_original_is_untouched(self):
        original = Guards()
        g = original.without("preflight")
        self.assertTrue(original.preflight)
        self.assertIsNot(g, original)

    def test_without_rejects_an_unknown_guard_name(self):
        with self.assertRaises(ValueError):
            Guards().without("not_a_real_guard")


if __name__ == "__main__":
    unittest.main()
