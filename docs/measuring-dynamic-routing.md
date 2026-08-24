# We tried to measure dynamic agent routing four times. Here is how each one failed.

*30 real briefs · 4 batches · 90 + 120 runs in the two that produced tables ·
one pre-registered successor · August 2026*

Multi-agent frameworks have converged on a shared assumption: that letting agents
decide who runs next beats a graph you drew in advance. `baton` was built to test
it, on real work, with the stop rule agreed before the data came in. Four attempts
later, the honest summary is worse than a negative result:

> **The comparison is untested. The corpus built to test it could not have. And
> the claim as originally stated is unprovable against the strongest fair
> baseline — not for want of power or budget, but by construction.**

Batch 1 measured a rate limiter. Batch 2 was confounded by model. Batch 3 returned
p = 0.774 and was later shown to have been counting the wrong thing. Batch 4
blocked 119 of 120 runs. A fifth attempt — EMBED-1 — is pre-registered: it tests a
weaker, defensible claim, and it can fail. It has **not been run**, and there is no
data from it anywhere in this document.

This is the write-up, because a benchmark that only gets published when it agrees
with you is not a benchmark.

---

## batch3 — the design, and the number it produced

The corpus is 68 recorded jobs from a multi-agent system that had been running
real projects; 30 are checkable, meaning there is a recorded outcome to compare
against. Every brief was run through three arms:

| arm | what differs |
|---|---|
| **static** | the recorded DAG, replayed — an agent may only hand to the successors that edge existed for |
| **dynamic** | any other agent on the staffed team, plus the gate |
| **reputation** | dynamic, plus a line in the prompt stating each agent's historical rework rate |

**The only thing that differs between arms is `can_hand_to`.** Same briefs, same
personas, same gate, same model (`mistral-small-latest`) on every hop. It is a
paired design, which matters for how the result must be read.

| arm | delivered | rate | avg reruns | $/delivered |
|---|---|---|---|---|
| static | 19/30 | 63% | 1.37 | $0.0023 |
| dynamic | 17/30 | 57% | 1.77 | $0.0031 |
| reputation | 21/30 | 70% | 1.40 | $0.0025 |

Reputation looks like a 7-point win over static. It is not. Because the design is
paired, the headline rates are not the test — only briefs where two arms
*disagree* carry information, and there are very few of them:

| comparison | A only | B only | exact McNemar |
|---|---|---|---|
| static vs dynamic | 7 | 5 | **p = 0.774** |
| static vs reputation | 6 | 8 | **p = 0.791** |
| dynamic vs reputation | 3 | 7 | **p = 0.344** |

Reputation's nominal +2 briefs over static is six coin flips landing 4–2.

**12 of 30 briefs produced identical outcomes under all three arms — 10 all-pass,
2 all-fail — and carry zero information.** The whole experiment rests on 18 briefs
split across three pairwise comparisons, and more cannot be bought: 30 checkable
jobs is the population, not a sample of it.

At the time that read as an under-powered experiment. It was something else.

---

## The guard written the same morning

**33 of the 90 runs (37%) ended `ratified_without_deliverable`** — a terminal
reason meaning the gate approved a run in which nothing was produced. That guard
was written hours before the batch; before it existed, the runtime accepted the
gate's approval at face value.

> **Without that guard, all 90 runs would have scored `ratified`. The table would
> have read static 100%, dynamic 100%, reputation 100% — and it would have been
> published as a result.**

Every terminal reason in batch3 is either `ratified` (57) or
`ratified_without_deliverable` (33). Nothing else — so the dominant failure mode is
**the model failing the routing contract**, not routing choices going wrong.

**An eval harness that cannot fail is not an eval harness.** Build the falsifier
before you trust the measurement. That lesson produced batch4, and batch4
demolished batch3.

---

## batch4 — the contract was the variable, and batch3's rates were false positives

Two more guards were added after batch3: `require_coverage` (the gate must name
one existing artifact per acceptance criterion, in order) and `require_substance`
(an artifact must contain work, not a placeholder). They were written because
three run shapes were observed and recorded: a run ratified after delivering
**three planning memos** against criteria naming a deployed web app, a 3D viewer,
an ETL pipeline, an ML model, Stripe billing and GDPR handling; a run ratified
with nine artifacts of which six were 29–39 characters, `Dockerfile.stripe`
reading in full `# Dockerfile.stripe content shown above`; and a run ratified
after the gate cited **one file path for all six criteria**.

batch4 re-ran the same 30 frozen briefs on the same model
(`mistral-small-latest`), with a fourth arm added — `escape`, a bounded escape
hatch off the recorded DAG. 120 runs, zero infrastructure errors, $0.2216.

| arm | delivered | rate | avg reruns |
|---|---|---|---|
| static | 0/30 | 0% | 1.80 |
| dynamic | **1/30** | 3% | 1.77 |
| reputation | 0/30 | 0% | 1.40 |
| escape | 0/30 | 0% | 1.23 |

Terminal reasons across all 120: `ratified_without_coverage` **98**,
`ratified_without_deliverable` 21, `ratified` 1. The single delivery was
`chairman-website` on the dynamic arm — 3 hops, 0 reruns.

**No model change. No brief change. No code change to routing. Only the ratify
contract changed** — static 19/30 → 0/30, dynamic 17/30 → 1/30, reputation
21/30 → 0/30.

A 63-point collapse from tightening two guards is not a small correction to the
old numbers. It says they were mostly counting work that does not survive being
asked *"which artifact satisfies criterion 2?"*

> **batch3's delivery rates — the numbers Week 1's verdict was computed from —
> were substantially false positives.**

This does not overturn "nothing separated the arms" — p = 0.774 still stands, and
batch4 gives no reason to think a fair re-run would separate them. It changes what
that verdict was a verdict *about*: the three arms were compared on a "delivered"
that included memos and stubs. So the routing comparison is not merely
inconclusive, **it is untested**, and saying otherwise would be the exact failure
this project keeps catching in its own gates.

The escape arm was never measured either. With one delivery in the entire batch
there is at most one discordant pair, and the paired tables return p = 1.000 or
n/a. **That is a floor effect, not a null result.**

---

## Orchestration is only measurable inside a window

batch3 and batch4 are mirror images. A missing guard let nearly every run pass and
the arms were indistinguishable at ~100%; a new guard made nearly every run fail
and the arms were indistinguishable at ~0%. Both times the comparison died, and
both times the tell was identical: **the arms agreeing on everything.**

| model | behaviour | usable? |
|---|---|---|
| `mistral-large` | one-shots every brief; the path is `ceo → gate` under **both** arms | **no** — routing freedom never exercised, arms identical by construction |
| `mistral-small` | routes, but its gate cannot name one artifact per criterion — 98 of 120 runs died there | **no** — below the floor the honest contract requires |
| Claude (via subscription) | obeys the contract cleanly — 0 repairs in 5 dispatches | untested at scale here |

> **Too strong a model and every arm succeeds; too weak and every arm fails.
> Either way the arms agree and the experiment carries no information.** Any
> benchmark of routing, planning or multi-agent structure has this window, and
> almost none of them report where their model sat in it.

The next delivery-based batch would need a model landing at roughly 40–80%
delivery — and **the contract must not be loosened to get there**: that is buying
a measurable experiment by making it measure the wrong thing. The measured value
of orchestration is a function of the gap between task difficulty and single-agent
capability, and that gap closes as models improve: a multi-agent benchmark that
gets *easier* as models improve is measuring its own obsolescence, and one that
gets harder was measuring formatting compliance all along.

---

## The corpus was homogeneous, so the null was designed in

Recomputed directly from `bench/briefs/frozen.json`, the 30 briefs break down as
**maintenance_bugfix 14, api_integration_automation 6, studio_build 4,
full_stack_app 3, document_extraction 2, fde 1.**

All six types are "build or fix some software," staffed from near-identical
rosters (`backend_engineer` appears in all 30; `ceo`, `cto`, `architecture_agent`,
`qaf_director`, `budget_agent` and `master_intake_agent` in 25 each) and wanting a
near-identical order.

That matters more than any p-value here:

> **Dynamic routing can only beat a hand-authored graph when the best next agent
> depends on the input.** If one route is right for every item, the authored graph
> is optimal by construction — and dynamic routing can only match it, or lose by
> mis-routing.

So p = 0.774 was not an under-powered measurement of a real effect. It was a
**correct measurement of an effect the design excluded.** It also explains the
12 of 30 concordant briefs: on a corpus with one right answer, arms that differ
only in how far they may deviate from it will mostly agree. Fixing this needs a
corpus where different items genuinely want different specialists — which the
recorded corpus, by its nature, does not contain.

---

## The objection that reframes everything

Even with a heterogeneous corpus there is a deeper problem, and it is not
statistical. **The strongest fair baseline is not a fixed DAG.** LangGraph and its
peers support conditional edges, so the strongest hand-authored graph is one whose
router node itself calls an LLM — handed the item, the roster, and the worker's
*full untruncated output*.

That router's information set is then a **superset** of the worker's. Both make
the same kind of choice with the same model, but the supervisor sees everything
the worker saw plus what the worker produced. On decision quality, agent-chosen
routing can at best tie.

> **The founding claim — "agent-chosen routing outperforms a hand-authored graph"
> — is therefore unprovable at any budget. Not for lack of power, not for lack of
> corpus, but by construction.**

Beating a *fixed* pipeline on heterogeneous input is provable for a few dollars,
and it is a straw-man victory nobody should care about. This flips only if someone
exhibits a mechanism by which the worker's *private* context — something that
cannot be serialized into a router call at reasonable cost — carries routing
signal. No such mechanism exists in baton today: the packet already carries
everything forward except artifact contents, and the supervisor baseline receives
those in full.

---

## A correction to this project's own record

An earlier claim made here was that *"a hand-authored router degrades as targets
multiply, but a whitelist does not."* **That is wrong when whitelists are wide.**

`render_routing_contract` in `src/baton/contract.py` prints
`charter.legal_moves_for(agent)` — the agent's whitelist intersected with the run
pool — straight into the prompt, and the bench's own dynamic arm sets that
whitelist to everyone. So baton's chooser faces the same N-way choice, with the
same N-length prompt, as a supervisor does. The scaling advantage exists only when
whitelists are **narrow**, and authoring narrow whitelists is itself hand-authoring
work — distributed across agent definitions rather than centralised in a router.
That is an authoring-economics argument, not an accuracy one, and it should never
have been offered as the winnable axis.

---

## EMBED-1 — what is being pre-registered instead

**Designed 2026-08-24. Not run. There is no EMBED-1 data anywhere in this
document.**

If the founding claim is unprovable, the question becomes what the strongest
*defensible* claim is:

> Routing decided by the agent holding the work is **non-inferior** in accuracy to
> a dedicated LLM router (within 5 percentage points) and **strictly cheaper** —
> zero extra calls per hop instead of one, and no central prompt that must know
> every agent.

A Pareto claim with a real failure mode: if embedded routing proves ≥5pp *worse*,
baton's bet is falsified and the honest conclusion becomes *give the routing
decision to a supervisor* — a sentence already pre-committed.

**The primary metric is per-item routing *decision* accuracy** — did the arm's
chosen next agent fall inside that item's frozen human-labelled acceptable set?
Scored by string membership: no LLM judge anywhere in the scoring path, and no
delivery required. That is what survives what killed batches 1–4 — a model too
weak to build a Stripe integration can still be scored on whether it picked the
billing specialist, so batch4's floor effect cannot recur.

| arm | what it is |
|---|---|
| **A — embedded** | work assignment and routing contract in one prompt; one output carries both. Baton's own repair ladder applies. |
| **B — supervisor router** | same agent, same prompt, routing contract removed. A *separate* router call then receives the item, the roster verbatim, and the worker's full output. The strongest fair baseline; every information asymmetry favours B. |
| **C — fixed pipeline** | every item to the corpus's plurality specialist. Not a competitor — a manipulation check. |

**Power:** 400 primary items (plus 60 hop-1 triage items for calibration), paired,
assuming a discordance rate ψ ≈ 0.15 — **~82% power at δ = 5pp**, one-sided
α = 0.05, **minimum detectable effect ~4.8pp**. Contrast with batch3's effective N
of 12 discordant pairs.

**Gates, both checked before A vs B is ever examined:**

- **Heterogeneity gate.** Arm C — the fixed pipeline — must score **≤ 35%**. If a
  single fixed route does well, the corpus is homogeneous and **the comparison
  never runs.** batch3's failure, written into the protocol as a pre-condition.
- **Compliance gate.** Each arm's legal-decision rate must be **≥ 90%**. Below
  that the model is outside the window: escalate along the frozen ladder
  (`mistral-medium` → Haiku 4.5 → Sonnet 5), at most twice, restarting the ledger
  each time. **Never loosen either arm's contract to pass this gate.**

**The fourth fake number, named in advance.** Arm B's bare router JSON is easier
to emit than arm A's combined output, so compliance noise could masquerade as
routing signal — exactly batch3's disease. Pre-registered sensitivity analysis:
recompute the difference excluding items where either arm failed at parse level
rather than choice level. If the primary and sensitivity conclusions disagree, the
frozen verdict wording is *"the difference is contract-compliance-driven, not
routing-driven"*, and the compliance gap itself becomes the reported finding.

**The retirement clause.** If EMBED-1 comes back inconclusive, that is the third
inconclusive result on this question, and the pre-committed response is
**retirement, not a fifth attempt** — the routing-quality claim is withdrawn from
the README permanently, leaving what is already proven: the bounded runtime, zero
dependencies, and the measured routing-overhead number, which survives any
accuracy outcome.

Corpus, labels, both arms' prompt templates and the analysis script are hashed
before the first paid call; any post-freeze edit restarts the ledger. API budget
~$15; the binding cost is 6–8 hours of human labelling.

---

## What is not concluded

- ❌ **"The static DAG is better."** It is not. p = 0.774 — on a corpus and a
  contract that could not have shown otherwise.
- ❌ **"Dynamic routing does not work."** Untested. Not "tested and null."
- ❌ **"Reputation is the moat."** p = 0.344 against blind dynamic, on rates now
  known to be inflated.
- ❌ **"A bounded escape hatch helps."** Never measured — floor effect.
- ✅ **"Four attempts to measure this produced no usable comparison, and we can
  now name why each one failed."**

## What shipped anyway

The routing thesis is unproven. The **runtime** is not: every run ends for exactly
one of **ten** declared reasons, checked across thousands of adversarial randomised
runs, with zero dependencies and a suite that fails the build if a vendor SDK is
ever imported. That is what `baton-kernel` is — not a claim that agents should
choose their own successor, but the envelope that makes it safe to find out.

---

## Four times this project produced a number that looked like evidence and wasn't

Each one survived review at the time:

1. **"static 20%, dynamic 20%"** — Google's rate limiter returning errors, not
   agents failing.
2. **A 66-run corpus baseline** — confounded by model: the recorded runs used a
   different model than the arms compared against them.
3. **"All arms 100%"** — averted by hours. The gate was ratifying claims rather
   than deliverables.
4. **"static 63%, dynamic 57%, reputation 70%"** — published, then invalidated by
   batch4. The gate was ratifying memos and 30-character stubs.

The common shape: *a measurement that cannot distinguish success from a
particular kind of failure will report success.* Ask of every green number what
specific failure it would have shown you — then check that the thing you varied
was the thing you meant to vary.

---

*Reproduce: batch3 is `bench/run.py --arms static,dynamic,reputation`; batch4
added a fourth, `escape`. Per-brief outcomes
are in `bench/results/batch3.json` and `bench/results/batch4.json`. Corpus
composition above is recomputed from `bench/briefs/frozen.json`.*
