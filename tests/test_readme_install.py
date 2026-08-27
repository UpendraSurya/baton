"""The README's install instruction must be one a stranger can actually run.

baton shipped a README whose FIRST command was `pip install baton-kernel` while
the package was not on PyPI and the repo carried no git tags -- so the very first
thing a stranger did, before seeing anything the library does well, was watch a
404. Every other gate in this repo was green while that was true, because no gate
read the README as instructions.

This is a ratchet, not a checker: it cannot reach the network, so it holds the
README and PUBLISHED_TO_PYPI in agreement. Flip the flag when the upload actually
happens and this test will insist the README go back to the PyPI instruction.
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
QUICKSTART = ROOT / "docs" / "quickstart.md"

# Flip to True in the SAME commit as the twine upload. Not before -- the point of
# this file is that the README stops promising things that are not true yet.
PUBLISHED_TO_PYPI = False

_INSTALL_CMD = re.compile(r"^\s*(?:\$\s*)?pip install\s+baton-kernel\s*$", re.M)


class ReadmeInstallIsHonest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = README.read_text(encoding="utf-8")

    def test_readme_exists_and_is_not_empty(self) -> None:
        self.assertGreater(len(self.text), 200, "README is missing or truncated")

    def test_install_instruction_matches_publication_state(self) -> None:
        promises_pypi = bool(_INSTALL_CMD.search(self.text))
        if PUBLISHED_TO_PYPI:
            self.assertTrue(
                promises_pypi,
                "baton-kernel is published, so the README should lead with "
                "`pip install baton-kernel` instead of an install-from-source dance.")
        else:
            self.assertFalse(
                promises_pypi,
                "README tells the reader to run `pip install baton-kernel`, but "
                "PUBLISHED_TO_PYPI is False -- that command 404s. Either publish "
                "and flip the flag, or document install-from-source.")

    def test_unpublished_readme_offers_a_working_alternative(self) -> None:
        if PUBLISHED_TO_PYPI:
            self.skipTest("published — install-from-source is no longer required")
        self.assertRegex(
            self.text, r"pip install \.",
            "an unpublished README must still give the reader a command that works")

    def test_readme_points_at_the_free_example(self) -> None:
        """The front door is the example that costs nothing. If it stops being
        named here, a stranger's first run needs an API key."""
        self.assertIn("examples/triage.py", self.text)
        example = ROOT / "examples" / "triage.py"
        self.assertTrue(example.is_file(), "README names an example that does not exist")


class QuickstartInstallIsHonest(unittest.TestCase):
    """The same claim lived one layer down in docs/quickstart.md, and verify.sh
    REQUIRED it to be there -- a gate enforcing an instruction that 404s."""

    def test_quickstart_matches_publication_state(self) -> None:
        if not QUICKSTART.is_file():
            self.skipTest("no quickstart in this checkout")
        promises_pypi = bool(_INSTALL_CMD.search(QUICKSTART.read_text(encoding="utf-8")))
        self.assertEqual(
            promises_pypi, PUBLISHED_TO_PYPI,
            "docs/quickstart.md and PUBLISHED_TO_PYPI disagree about whether "
            "`pip install baton-kernel` works.")


class RatchetIsNotVacuous(unittest.TestCase):
    """A check that cannot fail is not a check. Both branches must be reachable."""

    def test_regex_matches_the_command_it_is_meant_to_catch(self) -> None:
        self.assertTrue(_INSTALL_CMD.search("```bash\npip install baton-kernel\n```"))
        self.assertTrue(_INSTALL_CMD.search("$ pip install baton-kernel"))

    def test_regex_does_not_match_prose_about_the_command(self) -> None:
        self.assertFalse(_INSTALL_CMD.search(
            "a `pip install baton-kernel` would 404 today"))
        self.assertFalse(_INSTALL_CMD.search("pip install ."))


if __name__ == "__main__":
    unittest.main()
