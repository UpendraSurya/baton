# We measured dynamic agent routing against a static DAG. Nothing separated.

*30 real projects · 3 arms · 90 runs · one model held constant · August 2026*

Multi-agent frameworks have converged on a shared assumption: that letting agents
decide who runs next beats a graph you drew in advance. It is an appealing idea.
`baton` was built to test it, on real work, with the stop rule agreed before the
data came in.

**The result is negative, and worse than negative — it is inconclusive.** This is
the write-up, because a benchmark that only gets published when it agrees with
you is not a benchmark.

---

## The design

The corpus is 68 recorded jobs from a multi-agent system that had been running
real projects; 30 of them are checkable, meaning there is a recorded outcome to
compare against. Every brief was run through three arms:

| arm | what differs |
|---|---|
| **static** | the recorded DAG, replayed — an agent may only hand to the successors that edge existed for |
| **dynamic** | any other agent on the staffed team, plus the gate |
| **reputation** | dynamic, plus a line in the prompt stating each agent's historical rework rate |

**The only thing that differs between arms is `can_hand_to`.** Same briefs, same
personas, same gate, same model (`mistral-small-latest`) on every hop of every
run. It is a paired design, which matters for how the result must be read.

## The numbers

| arm | delivered | rate | avg reruns | $/delivered |
|---|---|---|---|---|
| static | 19/30 | 63% | 1.37 | $0.0023 |
| dynamic | 17/30 | 57% | 1.77 | $0.0031 |
| reputation | 21/30 | 70% | 1.40 | $0.0025 |

Reputation looks like a 7-point win over static. It is not.

Because the design is paired, the headline rates are not the test. Only briefs
where two arms *disagree* carry any information, and there are very few of them:

| comparison | A only | B only | exact McNemar |
|---|---|---|---|
| static vs dynamic | 7 | 5 | **p = 0.774** |
| static vs reputation | 6 | 8 | **p = 0.791** |
| dynamic vs reputation | 3 | 7 | **p = 0.344** |

Not one comparison comes close to significance. Reputation's nominal +2 briefs
over static is six coin flips landing 4–2.

## Why it is under-powered, in one table

```
outcome pattern (static, dynamic, reputation)
  (T, T, T)   10     all three agree
  (F, F, F)    2     all three agree
  ...8 mixed patterns across 18 briefs
```

**12 of 30 briefs produced identical outcomes under all three arms and carry zero
information.** The entire experiment rests on 18 briefs split across three
pairwise comparisons.

And more briefs cannot be bought. The corpus has 68 recorded jobs; 30 are
checkable. That is the population, not a sample of it. **This benchmark may be
structurally incapable of resolving the question it was built to answer** — which
is itself a finding, and one worth having before spending three more weeks.

---

## The threat to validity that matters more than the p-values

**33 of the 90 runs (37%) ended `ratified_without_deliverable`** — a terminal
reason meaning the gate approved a run in which nothing was actually produced.

That guard was written the morning of the benchmark. Before it existed, the
runtime accepted the gate's approval at face value.

> **Without that guard, all 90 runs would have scored `ratified`. The table would
> have read static 100%, dynamic 100%, reputation 100% — and it would have been
> published as a result.**

Every terminal reason in the batch is either `ratified` (57) or
`ratified_without_deliverable` (33). Nothing else. So the dominant failure mode
here is **the model failing the routing contract**, not routing choices going
wrong — and the arms may be separated mostly by which one happened to survive
more formatting noise.

The most valuable output of this benchmark is not the number. It is that a guard
written hours earlier is the only reason the benchmark measures anything at all.

**An eval harness that cannot fail is not an eval harness.** Build the falsifier
before you trust the measurement.

---

## The competence band: a stronger model makes this benchmark worse

Model choice turned out to control whether the experiment has any signal, in a
way the design did not anticipate.

| model | behaviour | usable? |
|---|---|---|
| `mistral-large` | one-shots every brief; the path is `ceo → gate` under **both** arms | **no** — routing freedom is never exercised, so the arms are identical by construction and the benchmark is blind |
| `mistral-small` | delegates, but fails the routing contract on 37% of runs | **marginal** — signal exists, buried in formatting noise |
| Claude (via subscription) | obeys the contract cleanly — 0 repairs in 5 dispatches | untested at scale here |

A benchmark of orchestration needs a model **strong enough to obey the contract,
weak enough to need to delegate.** That is a narrow band, and no free-tier model
tested sits inside it.

This generalises past this experiment:

> **The measured value of orchestration is a function of the gap between task
> difficulty and single-agent capability.** As models improve, that gap closes,
> and the thing being measured shrinks toward zero. It is not a fixed property of
> the topology.

An agent that can do the whole job alone never routes. So any multi-agent
benchmark that gets *easier* as models improve is measuring its own obsolescence,
and any that gets harder was measuring formatting compliance all along.

---

## What is not concluded

- ❌ **"The static DAG is better."** It is not. p = 0.774.
- ❌ **"Dynamic routing does not work."** Untested at adequate power.
- ❌ **"Reputation is the moat."** p = 0.344 against blind dynamic.
- ✅ **"On 30 real briefs with a contract-marginal model, no arm separated."**

## What shipped anyway

The routing thesis is unproven. The **runtime** is not: every run ends for
exactly one of eight declared reasons, observed across 4,000 adversarial runs,
with zero dependencies and a suite that fails the build if a vendor SDK is ever
imported.

That is what `baton-kernel` is: not a claim that agents should choose their own
successor, but the envelope that makes it safe to find out.

---

## Three ways this project produced a number that looked like evidence and wasn't

Worth stating plainly, because each one survived review at the time:

1. **"static 20%, dynamic 20%"** — that was Google's rate limiter returning
   errors, not agents failing.
2. **A 66-run corpus baseline** — confounded by model: the recorded runs used a
   different model than the arms compared against them.
3. **"All arms 100%"** — averted by hours. The gate was ratifying claims rather
   than deliverables.

The common shape: *a measurement that cannot distinguish success from a
particular kind of failure will report success.* Ask of every green number what
specific failure it would have shown you.

---

*Reproduce: `bench/run.py --arms static,dynamic,reputation`. Raw results,
including per-brief outcomes, are in `bench/results/batch3.json`.*
