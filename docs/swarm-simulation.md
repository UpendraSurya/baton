# The trail-aware arm, in simulation

*9 arms · 5 scenarios · 300 tickets × 5 seeds each · 67,500 runs through the real
runtime · $0 · `python3 bench/swarm_sim.py` · September 2026*

> **Update:** two rival learners were added after this page was first written:
> Thompson sampling (a bandit) and tabular Q-learning (reinforcement learning).
> See [Swarm against a bandit and an RL router](#swarm-against-a-bandit-and-an-rl-router)
> below, and [rl-basics.md](rl-basics.md) for how both work. The seven original
> arms' numbers are unchanged, because the pairing is deterministic.

This is the next step named in the README: put a trail-aware arm (`baton.swarm`)
beside the existing `static`, `dynamic` and `reputation` arms and measure it. It
runs **in simulation, with a gate that knows the right answer**. The one thing
that makes this measurable for free also limits what it can show:

> **What this shows:** whether the trail *arithmetic* routes better than the other
> policies when routing is done by code.
> **What it does not show:** whether a *model* reading "handing to docs ratified in
> 28 of 35 past runs" in its prompt routes better. That needs real calls (tier 2).
> Nothing here is evidence for it.

Full tables are in [`bench/results/swarm_sim.md`](../bench/results/swarm_sim.md).
The world, the arms and the pairing are described in the docstring of
[`bench/swarm_sim.py`](../bench/swarm_sim.py).

## Setup in one paragraph

Tickets have a hidden class (billing / bug / how-to). Each class is resolved by
exactly one desk, and even that desk does acceptable work only 85% of the time.
Intake sees a *perceived* label drawn from a confusion matrix, plus the prior
mapping from label to desk that a model would get from the personas. The gate is
an oracle: it ratifies only good work from the right desk, sends wrong-desk work
back to intake to be re-routed, and sends weak work back to its desk. `max_hops`
is 6, so a run can survive one misroute but not two. Without that limit every
re-routing arm eventually lands on the right desk and the arms agree at the
ceiling: the window problem from
[measuring-dynamic-routing.md](measuring-dynamic-routing.md). Every ticket's
class, label and work-quality draws are identical across arms, so outcomes are
compared per ticket with an exact McNemar test.

## Headline: delivered, and McNemar against blind dynamic

| scenario | static | dynamic | reputation | **swarm** | swarm-flat | swarm-cold | oracle |
|---|---|---|---|---|---|---|---|
| homogeneous | **99.6%** | 89.1% | 85.2% | 98.2% (p<0.001) | 99.1% | 97.5% | 99.6% |
| mixed | 34.3% | 77.2% | 79.6% | 79.1% (p=0.045) | 72.2% | 74.1% | 99.6% |
| systematic | 34.3% | 78.7% | 79.9% | **84.9%** (p<0.001) | 71.3% | 77.5% | 99.6% |
| misled | 47.8% | 68.5% | 69.7% | **80.8%** (p<0.001) | 71.4% | 77.5% | 99.6% |
| drift, 2nd half | 32.1% | 66.8% | 58.4% | **82.1%** | 65.3% | 83.5% | 99.5% |
| $/delivered, drift | $0.0167 | $0.0056 | $0.0059 | **$0.0048** | $0.0068 | $0.0051 | $0.0033 |

## What it says

1. **One ticket class: keep the graph.** In `homogeneous` the drawn DAG equals the
   oracle and every routed arm is worse. `swarm` never beats `static` there
   (+0/−21). This matches `crossover`: with no variation between tickets,
   routing is pure overhead. The trail's job in this case is only to reduce the
   damage, and it does: 98.2% against blind dynamic's 89.1%.
2. **Mixed tickets with honest noise: learning barely helps.** With uniform
   perception errors and a prior that is right for every label, trails, rework
   rates and blind choice end up within 2.5 points of each other. `swarm` beats
   `dynamic` by +1.9 points at p = 0.045. Across four comparisons, that is not a
   finding to lean on.
3. **When the prior is wrong in a *consistent* way, trails help most.** In
   `systematic` (+6.2 points), `misled` (+12.3) and `drift` (+15.3 in the second
   half), `swarm` beats `dynamic` at p < 0.001. It is the only learned arm that
   does so everywhere. `reputation` falls *below* blind dynamic in `drift`
   (58.4% vs 66.8% in the second half). It scores agents, not handoffs, so the
   old billing desk keeps a clean record for the classes it still serves.
4. **The gain comes from re-routing, not the first pick.** `swarm`'s first-pick
   accuracy is about the same as `dynamic`'s, and sometimes lower (54.6% vs
   57.1% in `misled`). Sampling from the colony is exploration, and exploration
   costs accuracy on the first pick. Where the swarm arm pulls ahead is after a
   misroute: it re-routes to the desk its trails say delivers, while `dynamic`
   picks at random.
5. **The context key matters.** `swarm-flat` (one colony for all labels) roughly
   matches `static` on `homogeneous` but falls below blind dynamic in `mixed`,
   `systematic` and `drift`. A trail answers "from here, to whom?", and "here"
   has to include what the router believes the ticket is.
6. **The prior limits how fast evidence can win.** In `misled` the "howto" colony
   learned the right answer (intake→engineering: strength 0.43, 70/103 ratified;
   intake→docs: 0.02, 70/161), yet the first pick moved only to 55/45. The
   heuristic weighs the model's prior desk 5:1. Sweeping `--prior`:

   | other-desk weight | homogeneous | mixed | systematic | misled | drift |
   |---|---|---|---|---|---|
   | 0.2 (default) | 98.2% | 79.1% | 84.9% | 80.8% | 85.7% |
   | 0.5 | 97.4% | 76.5% | 82.3% | 80.5% | 85.2% |
   | 0.8 | 98.0% | 76.3% | 81.6% | 76.2% | 84.1% |

   Trusting the prior more was never worse in this world. That comes from
   perception here being at least 70% accurate, not from any general law.

## Two bugs this found in `baton.swarm`, both fixed before these numbers

- **A relative exploration floor made evaporation pointless.** Weights were
  unchanged by scaling every trail, so a stale route kept its share of traffic
  forever. The floor is now absolute, as in the Max-Min Ant System's tau_min.
  `test_evaporation_alone_moves_the_odds` covers it.
- **Misroutes were being credited.** A run that went intake→docs, was sent back,
  then intake→engineering and was ratified also rewarded intake→docs. The trail
  therefore learned the mistake, and `note()` counted it as a ratification.
  Credit now goes only to the run's **loop-free route** (ant routing's loop
  elimination), and the abandoned detours are penalised.

  The effect on the swarm arm was not all positive:

  | | homogeneous | mixed | systematic | misled | drift |
  |---|---|---|---|---|---|
  | before | 96.3% | 78.8% | 84.5% | **84.5%** | 84.1% |
  | after | **98.2%** | 79.1% | 84.9% | 80.8% | **85.7%** |

  `misled` lost 3.7 points and has not been explained yet. The fix stays anyway,
  because the old rule made `note()` state something false. A model reading
  that note would be told a misroute had delivered, and a library whose
  guarantee is "a claim must point at something real" cannot ship that.

## Swarm against a bandit and an RL router

Swarm was the first learning method tried here. The
fair question is whether a textbook method does better. Both rivals read only
the trace, exactly as swarm does, and both run with untuned defaults.

| scenario | swarm | thompson | q-learning | thompson vs swarm | q-learning vs swarm |
|---|---|---|---|---|---|
| homogeneous | **98.2%** | 97.2% | 93.1% | +14/−29, p=0.032 | +9/−86, p<0.001 |
| mixed | 79.1% | 76.3% | **80.9%** | +97/−139, p=0.007 | +126/−99, p=0.083 |
| systematic | 84.9% | 84.9% | **87.5%** | +120/−120, p=1.000 | +111/−71, p=0.004 |
| misled | 80.8% | 81.7% | **84.5%** | +164/−150, p=0.463 | +150/−94, p<0.001 |
| drift (2nd half) | 82.1% | **82.3%** | 74.3% | +67/−86, p=0.145 (full run) | +57/−114, p<0.001 (full run) |

- **Thompson sampling ties or loses against swarm.** Swarm is not a lucky pick.
- **Q-learning wins when misreads are systematic** (+2.6 and +3.7 points).
  Its state includes *which desks were already tried*, so it learns re-routes
  separately from first picks.
- **Q-learning loses on `homogeneous` and `drift`**, because fixed ε-greedy
  exploration and one-step-at-a-time unlearning are expensive there.
- **No learner wins everywhere.** The obvious next experiment is swarm or
  Thompson keyed on `(label, tried)`: Q-learning's state with swarm's
  forgetting. It is exercise 2 in [rl-basics.md](rl-basics.md).

## What would change this verdict

- **A real router.** Everything above uses a code policy, `colony.choose`. The
  claim that matters for baton is that a model, given `annotate()`'s notes,
  routes better than the same model without them. That is a tier-2 run, and
  the corpus problems in measuring-dynamic-routing.md apply to it unchanged.
- **Quality that varies with the ticket, not only with the desk.** Here the right
  desk always succeeds 85% of the time. If a desk were good at some bugs and bad
  at others, one trail per label would average over that.
- **A world less favourable to learning.** The oracle gate makes every
  ratification true. A real gate that sometimes ratifies wrongly (batch 3) would
  deposit trail on bad routes, and nothing here measures how fast that would
  mislead the colony.
