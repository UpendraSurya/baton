"""A RATIFY has to point at work that exists and covers what was asked.

Two failures observed on the ForgeLine brief (2026-08-23), both scoring
`ratified`:

  * run 4 delivered three department briefs — planning memos — against
    acceptance criteria naming a deployed app, a 3D viewer, an ETL pipeline, an
    ML model, Stripe billing and GDPR handling. Artifacts EXISTED, so the
    artifact guard passed. Nothing asked for was built.
  * run 3 delivered nine artifacts of which six were 29-39 characters, with
    descriptions like "Production-ready Dockerfile" and "CI pipeline with
    pinned action versions". `Dockerfile.stripe` read, in full,
    "# Dockerfile.stripe content shown above".

Both are the same failure the whole project keeps hitting: checking a CLAIM
instead of the work. Existence was never the property that mattered.
"""
import unittest

from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.packet import ArtifactRef
from baton.runtime import DispatchResult, Guards, run
from baton.trace import MemoryTrace

CRITERIA = ("a working webhook", "a deployment file")

REAL = "x" * 900


def done(*arts):
    import json
    return ('```handoff\n' + json.dumps({
        "decision": "PROPOSE_DONE", "summary": "done",
        "artifacts": list(arts)}) + '\n```')


def art(path, content, desc="a thing"):
    return {"path": path, "description": desc, "content": content}


def ratify(coverage=None):
    import json
    body = {"decision": "RATIFY", "summary": "ok"}
    if coverage is not None:
        body["coverage"] = coverage
    return '```handoff\n' + json.dumps(body) + '\n```'


def roster():
    return {"w": AgentSpec("w", "# W", frozenset({"g"})),
            "g": AgentSpec("g", "# G", frozenset({"w"}), role=GATE)}


def charter(**kw):
    base = dict(brief="b", entry_agent="w", gate_agent="g",
                agent_pool=frozenset(roster()), acceptance_criteria=CRITERIA,
                budget_ceiling_usd=100.0, max_hops=6)
    base.update(kw)
    return Charter(**base)


class Script:
    def __init__(self, lines):
        self.lines = list(lines)
        self.prompts = []

    def __call__(self, agent, baton, prompt):
        self.prompts.append(prompt)
        return DispatchResult(text=self.lines.pop(0), cost_usd=0.001,
                              in_tokens=1, out_tokens=1)


def go(lines, **kw):
    return run(charter(**kw.pop("charter_kw", {})), roster(), Script(lines),
               trace=MemoryTrace("t"), **kw)


class CoverageIsRequired(unittest.TestCase):
    def test_a_ratify_that_maps_every_criterion_is_accepted(self):
        r = go([done(art("hook.py", REAL), art("deploy.yml", REAL)),
                ratify(["hook.py", "deploy.yml"])])
        self.assertEqual(r.terminal_reason, "ratified")

    def test_a_ratify_with_no_coverage_at_all_is_refused(self):
        # Exactly run 4: artifacts present, nothing said about what they satisfy.
        r = go([done(art("brief.md", REAL)), ratify(), ratify()])
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")

    def test_coverage_must_name_every_criterion(self):
        r = go([done(art("hook.py", REAL), art("deploy.yml", REAL)),
                ratify(["hook.py"]), ratify(["hook.py"])])
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")

    def test_coverage_cannot_cite_an_artifact_that_does_not_exist(self):
        # The citation is checked against the artifact set, not believed.
        r = go([done(art("hook.py", REAL), art("deploy.yml", REAL)),
                ratify(["hook.py", "imaginary.yml"]),
                ratify(["hook.py", "imaginary.yml"])])
        self.assertEqual(r.terminal_reason, "ratified_without_coverage")
        self.assertIn("imaginary.yml", r.note)

    def test_the_gate_is_TOLD_to_map_criteria(self):
        d = Script([done(art("hook.py", REAL), art("deploy.yml", REAL)),
                    ratify(["hook.py", "deploy.yml"])])
        run(charter(), roster(), d, trace=MemoryTrace("t"))
        self.assertIn("coverage", d.prompts[-1])

    def test_the_guard_switches_off_alone(self):
        r = go([done(art("brief.md", REAL)), ratify()],
               guards=Guards(require_coverage=False))
        self.assertEqual(r.terminal_reason, "ratified")


class StubsAreNotDeliverables(unittest.TestCase):
    """An artifact whose CLAIM is longer than its CONTENT is a stub. That rule
    calibrates itself: no magic byte count, just the description outweighing
    the work it describes."""

    def test_a_set_of_stubs_is_not_a_delivery(self):
        stub = art("Dockerfile.stripe", "# content shown above",
                   "Production-ready Dockerfile with idempotency and signature verification")
        r = go([done(stub, art("Dockerfile.ml", "# see above",
                               "Dockerfile for ML model serving with model persistence")),
                ratify(["Dockerfile.stripe", "Dockerfile.ml"]),
                ratify(["Dockerfile.stripe", "Dockerfile.ml"])])
        self.assertEqual(r.terminal_reason, "ratified_without_deliverable")
        self.assertIn("stub", r.note)

    def test_one_stub_among_real_work_is_tolerated(self):
        # A README that says "see the docs" alongside 900 chars of real code is
        # not a fraud. The guard fires on a set that is MOSTLY claim.
        r = go([done(art("hook.py", REAL), art("README.md", "see docs", "Runbook")),
                ratify(["hook.py", "README.md"])])
        self.assertEqual(r.terminal_reason, "ratified")

    def test_a_pointer_artifact_with_no_content_is_not_a_stub(self):
        # path + description and no content is the legitimate "open it yourself"
        # form for something the reader can fetch. Only a SHORT content that
        # under-delivers its own description counts.
        r = go([done({"path": "s3://bucket/model.pkl", "description": "the model"},
                     art("hook.py", REAL)),
                ratify(["s3://bucket/model.pkl", "hook.py"])])
        self.assertEqual(r.terminal_reason, "ratified")

    def test_the_guard_switches_off_alone(self):
        stub = art("a.py", "# above", "a very thoroughly described artifact indeed")
        r = go([done(stub), ratify(["a.py", "a.py"])],
               guards=Guards(require_substance=False))
        self.assertEqual(r.terminal_reason, "ratified")


if __name__ == "__main__":
    unittest.main()
