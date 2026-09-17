"""
The Claude Code CLI provider (`claude -p`, subscription not API), tested
entirely offline.

The subprocess runner is injected, so this suite never shells out to a real
`claude` binary — no key, no network, no subscription quota spent, same rule
every other provider's test lives by.
"""
import contextlib
import os
import subprocess
import unittest

from baton.agent import AgentSpec
from baton.packet import Baton
from baton.providers import claude_cli
from baton.providers.base import FORBID_ENV

AGENT = AgentSpec("cto", "# CTO", frozenset({"frontend_engineer"}))
BATON = Baton("t1", 1, "charter", "cto", "design it")

# Every test here injects a fake `runner` that never touches a real subprocess
# or network — same safety property the other providers get for free by
# injecting a fake `transport`. But this provider's FORBID_ENV check lives
# INSIDE dispatch() itself (a stronger guarantee than a check that only lives
# inside http_json, which a test's injected transport bypasses anyway) — so if
# the ambient environment has BATON_FORBID_REAL_DISPATCH set (as the packaging
# audit deliberately does, as a blanket net over the whole suite), every test
# below would short-circuit before ever calling the injected runner. Clearing
# it here is safe precisely because the runner is fake in every test in this
# file; the one test that means to exercise the guard restores it explicitly.
_FORBID_BEFORE = None


def setUpModule():
    global _FORBID_BEFORE
    _FORBID_BEFORE = os.environ.pop(FORBID_ENV, None)


def tearDownModule():
    if _FORBID_BEFORE is not None:
        os.environ[FORBID_ENV] = _FORBID_BEFORE


def cli_json(text="hello from claude", in_tok=500, out_tok=50):
    import json
    return json.dumps({"result": text,
                       "usage": {"input_tokens": in_tok,
                                 "output_tokens": out_tok}})


@contextlib.contextmanager
def env(name, value):
    before = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before


class Recorder:
    """Stand-in subprocess runner. Records the argv it was called with."""

    def __init__(self, stdout="", stderr="", returncode=0, raises=None):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.raises = raises
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append({"argv": argv, "timeout": timeout})
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode,
                                           stdout=self.stdout, stderr=self.stderr)


class RequestShape(unittest.TestCase):
    def test_argv_carries_the_prompt_and_model(self):
        rec = Recorder(stdout=cli_json())
        dispatch = claude_cli.provider(model="sonnet", runner=rec)
        dispatch(AGENT, BATON, "do the thing")
        argv = rec.calls[0]["argv"]
        self.assertIn("-p", argv)
        self.assertIn("do the thing", argv)
        self.assertIn("--model", argv)
        self.assertIn("sonnet", argv)
        self.assertIn("--output-format", argv)
        self.assertIn("json", argv)

    def test_a_different_model_is_carried_through(self):
        rec = Recorder(stdout=cli_json())
        dispatch = claude_cli.provider(model="opus", runner=rec)
        result = dispatch(AGENT, BATON, "x")
        self.assertIn("opus", rec.calls[0]["argv"])
        self.assertEqual(result.model_id, "opus")


class ResponseParsing(unittest.TestCase):
    def test_pulls_text_and_real_token_counts(self):
        rec = Recorder(stdout=cli_json("the deliverable", in_tok=1234, out_tok=99))
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertEqual(result.text, "the deliverable")
        self.assertEqual(result.in_tokens, 1234)
        self.assertEqual(result.out_tokens, 99)
        self.assertEqual(result.error, "")

    def test_malformed_json_falls_back_to_raw_text(self):
        rec = Recorder(stdout="not json, just text")
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertEqual(result.text, "not json, just text")
        self.assertGreater(result.in_tokens, 0)

    def test_empty_completion_is_an_error_not_a_silent_pass(self):
        rec = Recorder(stdout=cli_json(text=""))
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertNotEqual(result.error, "")

    def test_nonzero_exit_with_no_text_is_an_error(self):
        rec = Recorder(stdout="", stderr="boom", returncode=1)
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertIn("boom", result.error)


class RateLimitIsNotADeliverable(unittest.TestCase):
    def test_a_session_limit_notice_is_reported_as_an_error(self):
        rec = Recorder(stdout=cli_json("You've hit your usage limit for this session"))
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertNotEqual(result.error, "")
        self.assertEqual(result.text, "")

    def test_a_long_answer_that_merely_discusses_limits_is_not_flagged(self):
        long_text = ("Here is a design for rate limiting your API. " * 30)
        rec = Recorder(stdout=cli_json(long_text))
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertEqual(result.error, "")
        self.assertEqual(result.text, long_text)


class CostIsHonestlyZeroNotHidden(unittest.TestCase):
    def test_cost_usd_is_always_zero(self):
        rec = Recorder(stdout=cli_json())
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertEqual(result.cost_usd, 0.0)


class TimeoutAndMissingBinary(unittest.TestCase):
    def test_timeout_is_reported_not_raised(self):
        rec = Recorder(raises=subprocess.TimeoutExpired(cmd="claude", timeout=1))
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertIn("timed out", result.error)

    def test_missing_binary_is_reported_not_raised(self):
        rec = Recorder(raises=FileNotFoundError())
        dispatch = claude_cli.provider(runner=rec)
        result = dispatch(AGENT, BATON, "prompt")
        self.assertIn("not found", result.error)


class TheOneRealNetworkPathGuard(unittest.TestCase):
    def test_forbid_env_blocks_dispatch_without_shelling_out(self):
        rec = Recorder(stdout=cli_json())
        dispatch = claude_cli.provider(runner=rec)
        with env(FORBID_ENV, "1"):
            result = dispatch(AGENT, BATON, "prompt")
        self.assertNotEqual(result.error, "")
        self.assertEqual(rec.calls, [], "must not shell out when forbidden")


if __name__ == "__main__":
    unittest.main()
