# Changelog

All notable changes to `baton-kernel`. Format follows [Keep a Changelog];
this project uses [Semantic Versioning].

## [Unreleased]

### Added
- **`baton.swarm`** — stigmergic routing memory. A `Colony` keeps a trail per
  handoff edge (Ant System style): ratified runs deposit `deposit / hops` on the
  edges they took, failed runs erode theirs by `penalty`, every run evaporates
  all trails. `weights()` / `choose()` give the transition probabilities over a
  whitelist with an absolute exploration floor (MMAS tau_min); `note()` /
  `annotate()` put counted route history into personas; `from_reputation()`
  turns rework rates into the heuristic term. Advisory only — `can_hand_to` is
  never touched. Unfinished traces are skipped, not scored as failures.
  `examples/swarm_routing.py` shows adaptation after the right route changes.
  Credit goes to a ratified run's **loop-free route** only (`swarm.loop_free`),
  so a misroute that was sent back is never counted as a delivery.
- **`bench/swarm_sim.py`** — a tier-1 ($0, deterministic) paired benchmark of
  seven router policies through the real runtime with an oracle gate, across
  five scenarios, with exact McNemar tests. Results and caveats in
  `docs/swarm-simulation.md`; invariants held by `tests/test_swarm_sim.py`.
- **`baton.crossover`** — a pre-flight that answers "should this workload be
  routed at all?" from a task log, a measured perception accuracy and the
  hop-to-routing-call price ratio. Returns the threshold `rho*` and a verdict.
  Closed form, no fitted parameters; it predicted the empirical winner in 30/30
  cells of a frozen 620-item sweep. `perception_accuracy` has no default — the
  library never invents a number to decide on.

## [0.1.0] — 2026-08-31

First release. The runtime is the product; the routing thesis it was built to
test came back **inconclusive**, and that result ships with it rather than
being quietly left out — see "What is proven, and what is not" in the README.

### Added
- **The kernel.** `run(charter, agents, dispatch)` — legal-move enforcement, a
  budget ceiling, a hop cap, a ping-pong detector, and a gate agent that is the
  only role permitted to end a run.
- **Ten terminal reasons, exhaustively.** No path out of the loop returns
  "it just stopped". A 4,000-run adversarial suite has never produced a run that
  ended any other way; eight of the ten arise spontaneously in it, and the two
  added last — `ratified_without_coverage` and `time_exhausted` — are covered by
  targeted tests (`test_ratify_is_checked.py`, `test_runtime_wall_clock.py`)
  rather than by the fuzzer.
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

### Fixed

Four defects that only real use found, and two that only a machine other than
the author's could find.

- **Providers send a `User-Agent`.** Groq was entirely unreachable without one —
  Cloudflare rejects `Python-urllib/3.x` with HTTP 403 code 1010. 400 tests could
  not see it, because every provider test injects a transport. A library whose
  transport story is "stdlib urllib, no SDK" has to identify itself.
- **`estimate()` makes an unreachable agent `feasible=False`,** not a warning
  beside a cheerful verdict.
- **`PROPOSE_DONE` may say "I looked and found nothing".** It previously raised
  `IllegalTarget` when carrying no artifacts, so an agent that legitimately found
  nothing had no legal way to report it.
- **`Guards.without()`** switches one guard off without hand-building the other
  nine.
- **Tests no longer depend on the author's own machine.** `cost_usd()` prices
  against the host's rate card, so the five tests that call it now carry the same
  `needs_host` guard the other adapter tests already used. They passed on one
  laptop and errored on every other machine.
- **The generated API reference no longer embeds interpreter internals.** An
  undocumented `class Kind(str, Enum)` inherited `Enum`'s docstring on 3.10 and
  `str`'s on 3.11+, so `docs/api.md` could not be drift-checked against the
  version matrix that checks it.

### Documented

- **Known limitations, with numbers.** The routing contract asks the model to
  escape its work product as JSON; measured contract-failure rate by output-length
  quartile is 0.0% / 0.0% / 0.0% / 20.9%. The mechanism, both over-claim caveats
  and the workaround are in the README. The contract itself is unchanged.
- **`Charter.security_tier` is advisory** — validated, serialised and shown to the
  model, and enforced by nothing. A field with that name that enforces nothing is
  a trap, so `tests/test_security_tier_is_advisory.py` now fails if anything starts
  reading it *or* if the disclaimer is removed.
- **Install instructions match reality.** `tests/test_readme_install.py` holds the
  docs and a `PUBLISHED_TO_PYPI` flag in agreement in both directions, so the
  README cannot go back to instructing an install that 404s.

### Notable behaviours
- **A ratify with no artifact is not a delivery.** The gate's approval is
  checked against the artifact set mechanically rather than asked about in a
  prompt. Without this guard, all 90 benchmark runs scored `ratified` and the
  arms were indistinguishable at 100% each.
- **A ratify must say WHICH artifact satisfies WHICH criterion.** Existence was
  never the property that mattered: a gate was observed ratifying three planning
  memos against criteria naming a deployed app, an ETL pipeline and Stripe
  billing, and separately ratifying a `Dockerfile` whose entire content was
  `# Dockerfile.stripe content shown above`. `require_coverage` makes the claim
  falsifiable by a machine; `require_substance` refuses an artifact set whose
  descriptions outweigh its contents. One artifact cannot answer three
  independent criteria.
- **A run that never comes back is a stop reason, not a hang.** `max_wall_seconds`
  bounds the run and `max_dispatch_seconds` bounds a single hop, because a
  socket timeout bounds one call and a run multiplies it by hops.
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
