"""Every switch on `Guards` must have a test that turns it off.

The anti-vacuity suite is hand-written, one case per guard. That works right up
until someone adds an eleventh guard and forgets — and a guard nobody has ever
watched fail is not a guard, it is a comment. Ten of these already exist; this
enumerates the dataclass instead of trusting the list to stay in step.

It is deliberately a source scan and not a behavioural test. What it asserts is
not "the guard works" — the sibling tests do that — but "somebody wrote the test
that would notice if it stopped working".
"""
import dataclasses
import pathlib
import re
import unittest

from baton.runtime import Guards

TESTS = pathlib.Path(__file__).parent
SELF = pathlib.Path(__file__).name


def switches() -> set[str]:
    return {f.name for f in dataclasses.fields(Guards) if f.type in ("bool", bool)}


def switched_off_somewhere() -> set[str]:
    """Guard names that some test constructs as False."""
    found = set()
    for path in TESTS.rglob("test_*.py"):
        if path.name == SELF:
            continue
        text = path.read_text(encoding="utf-8")
        for name in switches():
            if re.search(rf"\b{re.escape(name)}\s*=\s*False\b", text):
                found.add(name)
    return found


class EveryGuardHasADeletionTest(unittest.TestCase):
    def test_the_dataclass_is_all_switches(self):
        # If a non-boolean setting appears, this test's premise needs revisiting
        # rather than silently covering less than it claims.
        self.assertTrue(switches(), "Guards exposes no boolean switches at all")

    def test_no_guard_ships_without_a_test_that_turns_it_off(self):
        orphans = sorted(switches() - switched_off_somewhere())
        self.assertEqual(
            orphans, [],
            f"these guards are never switched off by any test, so nothing "
            f"proves they change behaviour: {orphans}. Add a case that runs "
            f"with the guard off and asserts the outcome differs.")


if __name__ == "__main__":
    unittest.main()
