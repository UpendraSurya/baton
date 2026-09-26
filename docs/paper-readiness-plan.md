# Paper-readiness plan

Turns the audit gaps into a sequenced plan for the claim: *baton's gated
routing reduces false completion claims vs. existing agent frameworks, at
comparable or lower cost.* Nothing here is implemented yet — this is the plan
to approve before any code changes.

Priority key: **P0** blocks the paper's core validity claim, **P1** is
needed for a credible submission, **P2** strengthens it further.

## P0 — foundational integrity (do these first, in order)

1. **Execution-based ground-truth verifier.** Replace "success = gate cited a
   non-stub artifact" with a verifier that checks real end state per task
   type — run the tests, hit the endpoint, diff the expected output. This
   verifier must be the *sole* arbiter of success/failure for every
   framework compared, applied identically regardless of which system
   produced the artifact. Today's `require_coverage`/`require_substance`
   guards (`src/baton/runtime.py`) stay as baton's internal routing safety
   net; they must not double as the paper's ground truth.
2. **Independent task corpus.** Do not reuse the private "unlimited fork"
   corpus (`~/.unlimited-os/state.db`) — it was also used to design baton's
   own guards, which makes any result on it circular. Build the corpus from
   (a) a slice of an existing public benchmark with machine-checkable
   ground truth, satisfying the benchmark-independence requirement in the
   same step, plus (b) any custom tasks, designed and frozen *before* the
   first paid call, with the design rationale written down.
3. **Pre-registration.** Hypothesis, primary metric, gates, and a stated
   retirement/failure condition, written and hashed before running anything
   — the same discipline already used for `EMBED-1` in
   `docs/measuring-dynamic-routing.md`.

## P0 — fair comparison infrastructure

4. **Framework adapters.** Thin adapters for at least two other frameworks
   (e.g. LangGraph and one of CrewAI/AutoGen) that run the identical task
   set through the identical verifier and logger.
5. **Version-controlled experiment config.** One YAML/JSON schema covering
   model, temperature, seed, task set, framework, and guard-ablation flags —
   a single source of truth consumed by one runner script for every arm, so
   no arm gets an implicit advantage from a stray default.
6. **Uniform cost/latency logger.** Add per-hop latency capture to
   `baton.trace` (currently only a timestamp is stamped — no elapsed time),
   and build the equivalent wrapper for the other frameworks so $ cost,
   input/output tokens, call count, and latency are captured the same way
   on every arm.

## P1 — metrics & statistics

7. **Metrics module.** Compute false-positive rate, false-negative rate,
   true success rate, cost-per-success, and latency-per-task per
   framework/model from raw logs — never hand-computed for a table.
8. **Repeatability.** Plumb a `seed` parameter through providers where the
   API supports it (none currently expose one). Run each task multiple
   times per arm and report mean ± variance, keeping the paired-test
   methodology (McNemar / bootstrap CI) already used in this project.
9. **Ablation matrix.** Script that runs baton with each `Guards` flag
   toggled off individually (`Guards.without(...)` already supports this)
   against the same corpus, producing a per-guard contribution table to
   the false-positive rate.

## P1 — reproducibility & documentation

10. **One-command pipeline.** A script that runs every arm/model/ablation
    and regenerates every table and figure from raw JSONL logs.
11. **Ship the corpus and raw logs.** `docs/measuring-dynamic-routing.md`
    currently cites `bench/briefs/frozen.json` and
    `bench/results/batch3.json`/`batch4.json` — none of these exist in the
    repo today. Replace that with a corpus and logs a reviewer can actually
    open.
12. **Paper-facing docs.** Task design rationale, task-selection method, how
    to reproduce, and an explicit scope statement separating this
    cost/reliability claim from the routing-quality claim the project's own
    prior measurement already found unproven — so it doesn't read as a
    moved goalpost.

## P2 — strengthens the result

13. **Figure/table generation** wired into the one-command pipeline.
14. **Independent check on the verifier** — a second verification method or
    blind spot-check on a sample, to pre-empt the "verifier co-evolved with
    baton's own failures" bias objection raised in the audit.
