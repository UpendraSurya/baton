"""Charter.security_tier looks like a security control and is not one.

It is validated, serialised and printed into every prompt, and NOTHING in this
library acts on it. That is a legitimate design — the host application it was
written against classifies its own agents that way — but it is a trap for anyone
who assumes the name means enforcement.

So the claim is pinned from both ends. If someone wires it up, the whitelist
below fails and they must update the docs that promise it is advisory. If
someone deletes the promise from the docs, the doc tests fail while the code
still does nothing. Neither half can drift alone.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

from baton.agent import AgentSpec
from baton.charter import TIERS, Charter
from baton.contract import render_prompt
from baton.packet import Baton

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "baton"

# Where the tier is ALLOWED to appear, and the only thing it may do there.
#   charter.py             — declare, validate against TIERS, serialise
#   contract.py            — print it into the prompt
#   adapters/company_os/   — map the host's own tier onto the field
ADVISORY_SITES = {
    "charter.py",
    "contract.py",
    pathlib.Path("adapters/company_os/charter.py").as_posix(),
}


def _a_charter(**kw):
    base = dict(brief="ship it", entry_agent="w", gate_agent="g",
                agent_pool=frozenset({"w", "g"}),
                acceptance_criteria=("it works",))
    base.update(kw)
    return Charter(**base)


class TheTierReachesTheModel(unittest.TestCase):
    """The one thing it DOES do. Without this the test below is vacuous — a
    field nobody reads at all would pass the whitelist trivially."""

    def _prompt(self, tier):
        agent = AgentSpec(name="w", instructions="do the work",
                          can_hand_to=frozenset({"g"}))
        charter = _a_charter(security_tier=tier)
        baton = Baton(trace_id="t", hop=1, from_agent="w", to_agent="w",
                      goal="do the work")
        return render_prompt(agent, baton, charter)

    def test_every_tier_is_shown_to_the_agent(self):
        for tier in TIERS:
            self.assertIn(f"Security tier: {tier}.", self._prompt(tier))

    def test_the_tier_actually_varies_the_prompt(self):
        # Guards against a renderer that hardcodes one tier and looks correct.
        self.assertNotEqual(self._prompt("L"), self._prompt("H"))


class NothingEnforcesIt(unittest.TestCase):

    def test_no_module_outside_the_advisory_sites_reads_the_tier(self):
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            rel = path.relative_to(SRC).as_posix()
            if rel in ADVISORY_SITES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = (node.attr if isinstance(node, ast.Attribute) else
                        node.id if isinstance(node, ast.Name) else
                        node.value if isinstance(node, ast.Constant)
                        and isinstance(node.value, str) else None)
                if name == "security_tier":
                    offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "security_tier is documented as advisory but is now read at "
            f"{offenders}. Either revert that, or update README.md's 'Known "
            "limitations' section and the comment on Charter.security_tier — "
            "the library must not claim a control it does not have, and must "
            "not hide one it does.")

    def test_an_invalid_tier_is_still_rejected(self):
        # Advisory does not mean unvalidated: a typo'd tier reaching a prompt
        # would be worse than no tier at all.
        _a_charter(security_tier="H").validate()
        with self.assertRaises(Exception):
            _a_charter(security_tier="CRITICAL").validate()


class TheDocsSayItIsAdvisory(unittest.TestCase):
    """The other half of the ratchet. Deleting the warning must fail here."""

    def test_the_charter_field_carries_the_warning(self):
        src = (SRC / "charter.py").read_text(encoding="utf-8")
        self.assertIn("ADVISORY, NOT ENFORCED", src)

    def test_the_readme_documents_it_as_a_known_limitation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Known limitations", readme)
        self.assertIn("`Charter.security_tier` is advisory", readme)
        self.assertIn("baton enforces nothing", readme)


if __name__ == "__main__":
    unittest.main()
