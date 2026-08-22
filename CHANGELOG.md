# Changelog

All notable changes to `baton-kernel`. Format follows [Keep a Changelog];
this project uses [Semantic Versioning].

## [0.1.0] — 2026-08-22

First release. The runtime is the product; the routing thesis it was built to
test came back **inconclusive**, and that result ships with it rather than
being quietly left out — see "What is proven, and what is not" in the README.

### Added
- **The kernel.** `run(charter, agents, dispatch)` — legal-move enforcement, a
  budget ceiling, a hop cap, a ping-pong detector, and a gate agent that is the
  only role permitted to end a run.
- **Eight terminal reasons, exhaustively.** No path out of the loop returns
  "it just stopped". All eight observed across 4,000 adversarial runs.
- **`baton.plan`** — pre-flight hop and spend estimate before a run costs money.
- **Providers, zero-dependency.** `gemini` (Google's own `:generateContent`
  shape) and `openai_compat` + `mistral` (the `/v1/chat/completions` shape that
  Mistral, Groq and Cerebras all speak). That is a wire format, not an OpenAI
  dependency: no vendor SDK is imported, and a test fails the build if one ever is.
- **`baton.reputation`** — per-agent reliability from traces or from two tallies,
  with the sample size attached and a prompt line that names only agents which
  have actually needed rework.
- **`baton.adapters.company_os`** — a worked example of binding a host
  application, shipped so it can be read. It requires that checkout on disk and
  says so with `HostUnavailable` when it is absent.
- **Pluggable trace sinks** (`TraceSink`) — send hops to your own observability
  stack instead of a file. Traces are append-only, and the append is *checked*:
  a truncation or an edit underneath a run fails loudly.
- Type hints throughout and `py.typed`.

### Notable behaviours
- **A ratify with no artifact is not a delivery.** The gate's approval is
  checked against the artifact set mechanically rather than asked about in a
  prompt. Without this guard, all 90 benchmark runs scored `ratified` and the
  arms were indistinguishable at 100% each.
- **`cost_from_tokens` raises `UnmeteredModel`** for a model with no rate card.
  It never returns 0.0 — a free-looking model never trips a budget ceiling.
- **Budget exhaustion halts flat, with no final gate call.** The hop cap does
  get one. A call made past the ceiling spends money the charter forbade.
- **Every guard in `baton.runtime.Guards` switches off individually.** That is
  not debug scaffolding: the anti-vacuity suite deletes each guard alone,
  because two overlapping guards cover for each other and leave a green suite
  that proves nothing.

[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/
[Semantic Versioning]: https://semver.org/spec/v2.0.0.html
