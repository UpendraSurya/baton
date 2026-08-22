"""Which model ran, and what did it cost?

The trace is the record of what happened, and until 2026-08-22 it could not
answer the first question. Every cost row written into a host's ledger read
model_id='baton' -- the literal fallback string -- because DispatchResult never
carried the model and the trace therefore had nothing to record.

Cost attribution is not bookkeeping here: the budget ceiling is baton's only
spend guard, and a ledger that cannot name the model cannot be reconciled
against a provider's bill.
"""
import pathlib
import sqlite3
import tempfile
import unittest

from baton.adapters.company_os import state as S
from baton.agent import GATE, AgentSpec
from baton.charter import Charter
from baton.runtime import DispatchResult, run
from baton.trace import MemoryTrace


def roster():
    return {
        "writer": AgentSpec("writer", "# W", frozenset({"gate"})),
        "gate": AgentSpec("gate", "# G", frozenset({"writer"}), role=GATE),
    }


def charter():
    return Charter(brief="write a line", entry_agent="writer", gate_agent="gate",
                   agent_pool=frozenset(roster()), acceptance_criteria=("x",),
                   budget_ceiling_usd=10.0, max_hops=4)


DONE = ('```handoff\n{"decision": "PROPOSE_DONE", "summary": "done",'
        ' "artifacts": [{"path": "out.md", "description": "d", "content": "c"}]}\n```')
RATIFY = '```handoff\n{"decision": "RATIFY", "summary": "ok"}\n```'


class TheTraceNamesTheModel(unittest.TestCase):
    def dispatch_with(self, model_id):
        scripted = iter([DONE, RATIFY])

        def dispatch(agent, baton, prompt):
            return DispatchResult(text=next(scripted), cost_usd=0.01,
                                  in_tokens=100, out_tokens=50,
                                  model_id=model_id)
        return dispatch

    def test_the_dispatch_record_carries_the_model_that_ran(self):
        trace = MemoryTrace("t")
        run(charter(), roster(), self.dispatch_with("claude-opus-4-8"), trace=trace)
        models = [r.get("model_id") for r in trace.records()
                  if r.get("event") == "dispatch"]
        self.assertEqual(models, ["claude-opus-4-8", "claude-opus-4-8"])

    def test_a_provider_that_names_no_model_still_runs(self):
        # Backward compatible: model_id is evidence, not a requirement. A
        # provider that does not supply it must not break the run.
        trace = MemoryTrace("t")
        result = run(charter(), roster(), self.dispatch_with(""), trace=trace)
        self.assertEqual(result.terminal_reason, "ratified")
        rec = next(r for r in trace.records() if r.get("event") == "dispatch")
        self.assertEqual(rec["model_id"], "")


class TheLedgerNamesTheModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / "home"
        self.addCleanup(self.tmp.cleanup)

    def _run(self, model_id):
        scripted = iter([DONE, RATIFY])

        def dispatch(agent, baton, prompt):
            return DispatchResult(text=next(scripted), cost_usd=0.01,
                                  in_tokens=100, out_tokens=50, model_id=model_id)
        return run(charter(), roster(), dispatch, trace=MemoryTrace("t"))

    def ledger(self, project):
        con = sqlite3.connect(self.home / "state.db")
        rows = list(con.execute("select node, model_id from cost_ledger "
                                "where project_id=? order by seq", (project,)))
        con.close()
        return rows

    def test_the_real_model_reaches_the_cost_ledger(self):
        S.persist(self._run("claude-opus-4-8"), "p1", home=self.home)
        self.assertEqual([m for _, m in self.ledger("p1")],
                         ["claude-opus-4-8", "claude-opus-4-8"])

    def test_an_unnamed_model_is_marked_unknown_not_invented(self):
        # 'baton' is not a model. Writing the router's name into a model column
        # makes an unattributable row look attributed, which is worse than a
        # row that says so.
        S.persist(self._run(""), "p2", home=self.home)
        self.assertEqual({m for _, m in self.ledger("p2")}, {"unknown"})


if __name__ == "__main__":
    unittest.main()


class EveryProviderNamesItsModel(unittest.TestCase):
    """A provider already exposes `dispatch.model_id` for pre-flight planning.
    The RESULT has to carry it too: planning says what was intended, the trace
    has to say what ran."""

    def test_gemini_result_names_the_model(self):
        from baton.providers import gemini
        data = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}
        d = gemini.provider(api_key="k", model="gemini-2.5-flash",
                            transport=lambda *a, **k: data)
        self.assertEqual(d(None, None, "prompt").model_id, "gemini-2.5-flash")

    def test_openai_compat_result_names_the_model(self):
        from baton.providers import openai_compat
        data = {"choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        d = openai_compat.provider(api_key="k", vendor="mistral",
                                   model="mistral-small-latest",
                                   transport=lambda *a, **k: data)
        self.assertEqual(d(None, None, "prompt").model_id, "mistral-small-latest")

    def test_the_company_os_adapter_reports_the_RESOLVED_model(self):
        # The registry stores short aliases ("opus"); the ledger and the rate
        # card are keyed by full ids. The alias is what breaks reconciliation.
        from baton.adapters.company_os import dispatch as D
        self.assertEqual(D.resolve_model("opus"), "claude-opus-4-8")
