# Industrial-readiness plan

A separate track from the paper work: getting `baton` itself to a standard
suitable for real production adoption, independent of whether the paper's
comparison ever runs. Some items share underlying code with the paper plan
(noted inline); most do not overlap. Nothing here is implemented yet.

Priority key: **P0** blocks calling this production-grade, **P1** matters
for real adoption, **P2** operational polish.

## P0 — security gaps already named in the README's own posture table

1. **ASI01 Agent Goal Hijack.** `Baton.goal` is never validated against the
   run's original `Charter.brief` — a compromised hop can set any goal text
   a legal target will act on. Bound or validate goal drift.
2. **ASI09 Human-Agent Trust Exploitation.** No approval step exists between
   a RATIFY and delivery. Add an optional hook so a host can require human
   sign-off before a ratified result is acted on.
3. **`Charter.security_tier` is decorative.** It's validated and printed
   into every prompt but nothing branches on it (documented as intentional
   today). For production, make this a deliberate choice: either wire real
   enforcement behind it via a host-supplied policy hook, or keep it
   explicitly advisory and say so more prominently in adoption docs.

## P0 — release & API stability

4. **Publish to PyPI** (`baton-kernel`) — the README currently tells users
   to install from source because a PyPI release doesn't exist yet.
5. **Semantic versioning + a public API stability policy** — what's covered
   by backward-compatibility guarantees vs. what can change between minor
   versions.
6. **Formalize CHANGELOG discipline** against that policy (the file exists;
   the process tying it to SemVer doesn't yet).

## P1 — provider & platform breadth

7. **Verify and document self-hosted runtime support** through the existing
   `openai_compat` provider (vLLM, Ollama, etc.) — the path likely already
   works, it isn't documented as a supported route yet.
8. **Concurrency story.** `run()` is effectively single-threaded per call
   (aside from the dispatch-timeout watchdog thread). Document — or
   provide — guidance for running many charters concurrently, including
   whether `Trace` and `Guards` are safe to share across them.
9. **Retry/backoff policy for transient provider errors.** A 429/5xx today
   ends the run at `dispatch_failure` with no retry — a deliberate honesty
   choice for the kernel, but production users need a documented pattern
   (or an opt-in guard) for wrapping providers with backoff before they
   reach baton.

## P1 — observability

10. **First-class `TraceSink` integrations** (e.g. an OpenTelemetry
    exporter) — the seam (`TraceSink` protocol) already exists; this closes
    the gap from "worked example" to "supported integration."
11. **Per-hop latency and structured cost fields on `Trace`.** Shares the
    exact code change with item 6 of the paper plan — do this once, use it
    in both places.

## P2 — operational hardening

12. **Pluggable persistent trace backends** beyond a local JSONL file (S3,
    a database) — likely stays a documented extension point rather than
    shipped code, consistent with the project's zero-dependency principle.
13. **Load/stress testing guidance** — behavior under hundreds of
    concurrent charters or large artifact payloads.
14. **Formal support policy** — supported Python versions, deprecation
    policy, and a `SECURITY.md` disclosure process.
