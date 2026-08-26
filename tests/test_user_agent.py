"""Every request identifies itself, because some hosts refuse the ones that don't.

Discovered by a real consumer, not by the suite: `openai_compat` advertises
Groq support, and Groq was unreachable. Every call came back

    HTTP 403: error code: 1010

which is Cloudflare refusing the request outright. The cause was not the key,
the model or the payload — it was the absence of a User-Agent. urllib sends
`Python-urllib/3.x` by default, and that string is on enough blocklists that a
vendor sitting behind Cloudflare will reject it before the request reaches
their API.

A library whose entire transport story is "stdlib urllib, no SDK" has to send a
real identifier, or "no dependencies" quietly means "does not work with some
vendors".
"""
from __future__ import annotations

import os
import unittest
import unittest.mock

from baton import __version__
from baton.providers import base


class EveryRequestCarriesAUserAgent(unittest.TestCase):

    def send(self, **kw):
        """Run one http_json call with the socket replaced, and return the
        headers it would have sent.

        BATON_FORBID_REAL_DISPATCH is cleared for the duration. That guard
        exists to stop a real paid call escaping, and urlopen is replaced here
        so none can — `calls` asserts the mock was actually reached, so a
        future change that skipped the request would fail rather than pass
        silently. `TheNoRealCallsGuardStillWorks` below keeps the guard itself
        under test."""
        seen, calls = {}, []

        class FakeResp:
            def read(self_inner): return b'{"ok": true}'
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
            return FakeResp()

        saved = os.environ.pop(base.FORBID_ENV, None)
        try:
            with unittest.mock.patch("urllib.request.urlopen", fake_urlopen):
                base.http_json("https://example.test/v1", {"a": 1}, **kw)
        finally:
            if saved is not None:
                os.environ[base.FORBID_ENV] = saved
        self.assertEqual(len(calls), 1, "no request was attempted at all")
        return seen["headers"]

    def ua(self, **kw):
        h = self.send(**kw)
        self.assertIn("user-agent", h,
                      "no User-Agent: Cloudflare-fronted vendors return 403 "
                      "code 1010 before the request reaches the API")
        return h["user-agent"]

    def test_a_user_agent_header_is_sent(self):
        self.assertTrue(self.ua())

    def test_it_names_the_library_and_its_version(self):
        ua = self.ua()
        self.assertIn("baton", ua.lower())
        self.assertIn(__version__, ua)

    def test_a_caller_supplied_user_agent_wins(self):
        # A host application embedding baton should be able to identify itself.
        self.assertEqual(self.ua(headers={"User-Agent": "hostapp/2.0"}),
                         "hostapp/2.0")

    def test_the_default_urllib_agent_is_never_what_goes_out(self):
        self.assertNotIn("Python-urllib", self.ua())

    def test_the_content_type_is_still_sent(self):
        self.assertEqual(self.send()["content-type"], "application/json")


class TheNoRealCallsGuardStillWorks(unittest.TestCase):
    """The tests above clear BATON_FORBID_REAL_DISPATCH to reach the header
    construction. This asserts they did not defeat it for everyone else."""

    def test_it_refuses_when_the_env_var_is_set(self):
        saved = os.environ.get(base.FORBID_ENV)
        os.environ[base.FORBID_ENV] = "1"
        try:
            with self.assertRaises(base.ProviderError) as cm:
                base.http_json("https://example.test/v1", {"a": 1})
            self.assertIn("must not make a paid model call", str(cm.exception))
        finally:
            if saved is None:
                os.environ.pop(base.FORBID_ENV, None)
            else:
                os.environ[base.FORBID_ENV] = saved


if __name__ == "__main__":
    import unittest.mock
    unittest.main()
