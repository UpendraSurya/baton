# Reinforcement learning basics, taught through baton

This page teaches the core ideas of bandits and reinforcement learning, using
baton's own routing problem as the example. Everything here runs: the two
learners are `Thompson` and `QRouter` in [`bench/swarm_sim.py`](../bench/swarm_sim.py),
and `python3 bench/swarm_sim.py` measures them against `swarm` and the other
arms for $0.

---

## 1. The problem, in RL words

Every routing system answers the same question: **"the baton is here; who
should get it next?"** Reinforcement learning names the parts:

| RL term | in baton |
|---|---|
| **agent** (the learner) | the router: intake, or whatever code picks the next agent |
| **state** *s* | what the router knows now: the ticket's perceived label, and which desks were already tried |
| **action** *a* | the agent to hand to, which must be on the `can_hand_to` whitelist |
| **reward** *r* | +1 when the gate ratifies; a cost for every desk tried along the way |
| **episode** | one run, from `run_start` to `run_end` |
| **policy** π | the rule that maps state to action. `static`, `dynamic`, `swarm`, `thompson` and `q-learning` are all policies |

The goal is a policy that earns the most reward, which here means the most
deliveries for the fewest hops. The complication is that the router learns
*only from what its own choices showed it*. It never sees the answer key.

---

## 2. Explore versus exploit

This is the central dilemma, and every arm in the benchmark resolves it
differently.

- **Exploit:** pick what looks best so far.
- **Explore:** try something else, because "looks best" rests on limited
  evidence.

Only exploiting locks you onto an early lucky guess. Only exploring never uses
what you learned. Three standard answers, all present in this repo:

| method | how it explores | where |
|---|---|---|
| **ε-greedy** | with probability ε (0.1), pick uniformly at random; otherwise pick the best | `QRouter.pick` |
| **floor / softmax-style** | every option keeps a minimum weight; choose in proportion to weight | `swarm.Colony.weights` (the `explore` floor) |
| **Thompson sampling** | sample a plausible value for each option from your uncertainty, and pick the best sample | `Thompson.pick` |

What the benchmark showed: exploration has a **price**. On `homogeneous`, where
one desk is always right, every learner loses deliveries to its own exploring.
The ε-greedy arm loses the most (93.1% against 99.6% for the fixed graph),
because ε never shrinks. A common fix is to decay ε over time. Try it: it is
one line.

---

## 3. Bandits: one decision, no future

A **multi-armed bandit** is RL with a single decision per episode: pull a slot
machine's arm, get paid or not, repeat. Intake's *first* pick is a bandit per
label.

### Thompson sampling (the `thompson` arm)

For each (label, desk), keep a **Beta(a, b)** distribution, your belief about
that desk's chance of delivering:

- start at **Beta(1, 1)**, which is uniform: no idea yet;
- give the prior desk `TS_PRIOR = 2` extra successes, since the model's
  reading of the ticket counts for something;
- when the desk delivers, `a += 1`; when it is a misroute, `b += 1`.

To choose, **draw one random sample from each desk's Beta and take the
largest.** A desk you know well has a narrow Beta, so its samples sit near its
true rate. A desk you barely know has a wide Beta and sometimes draws high, so
it gets tried. Exploration comes from uncertainty itself, with no ε to tune.
This is why Thompson sampling is a strong default in practice.

**Non-stationarity.** Plain counts never forget, so a desk that *became* bad
keeps its old good record. That is exactly how the `reputation` arm lost the
`drift` scenario. `TS_DISCOUNT = 0.97` shrinks every count back toward the
prior on each observation. The effective memory is about
`1 / (1 − 0.97) ≈ 33` recent tickets per label. This is the bandit version of
swarm's `evaporation`.

---

## 4. Reinforcement learning: decisions that affect later decisions

Intake does not make one choice per ticket. After a misroute, it chooses again,
from a *different state*, because now it knows one desk has failed. A bandit
ignores that structure; RL uses it.

### Value functions

**Q(s, a)** is the expected total future reward of taking action *a* in state
*s*, then acting well afterwards. If you knew Q, the policy would be trivial:
in every state, take the action with the highest Q.

### The Bellman idea and the TD target

The value of an action is its immediate reward **plus** the value of where it
leaves you:

```
Q(s, a)  ≈  r  +  γ · max over a' of Q(s', a')
```

- `r` is the reward for this step: `−Q_STEP_COST` for trying a desk, plus 1 if
  it delivered.
- `γ` (`Q_GAMMA = 0.9`) is the **discount**: reward later is worth a bit less
  than reward now.
- `s'` is the next state (label, desks tried *including* this one).

The right-hand side is the **TD target** (temporal difference). Using your own
current estimate of the next state inside the target is called
**bootstrapping**. It lets value flow backward through a chain of decisions
without waiting for the true answer.

### The update rule (tabular Q-learning)

```
Q(s, a)  ←  Q(s, a)  +  α · (target − Q(s, a))
```

`α` (`Q_LR = 0.1`) is the **learning rate**: how far to move toward each new
target. With a small α, estimates are stable but slow to adapt; with a large α,
they are fast but noisy. In `QRouter.learn`, the three cases are:

| what happened at this step | target |
|---|---|
| this desk delivered | `1 − cost` (terminal) |
| this was the last pick and nothing delivered | `−cost` (terminal) |
| misroute, and the run continued | `−cost + γ · max Q(next state)` |

### Why Q-learning won `systematic` and `misled`

Its state includes **which desks were already tried**. After "docs failed on a
how-to ticket", Q-learning has a separate value table for exactly that
situation, and learns that engineering is almost always right *from there*.
Swarm and Thompson key only on the label, so they learn a blend of first picks
and re-routes. When misreads are systematic, that extra state is worth 2.6–3.7
points over swarm (p ≤ 0.004).

### Why it lost `drift` and `homogeneous`

- A fixed α = 0.1 **does** forget slowly. But ε-greedy explores uniformly,
  including desks it has good reason to believe are useless. That costs
  deliveries whenever the answer is stable.
- The Q table is initialised from the prior (0.5 for the prior desk). After the
  drift, it must unlearn that one update at a time.

---

## 5. Credit assignment

A run is a *sequence* of choices with *one* outcome at the end. Which choice
earned it? This is the **credit assignment problem**, and baton hit it for
real: the first version of `swarm` rewarded misroutes that happened to be
followed by a delivery. Three answers now exist in this repo:

1. **Loop elimination** (`swarm.loop_free`, from ant routing). Credit only the
   route that remains after detours are cut out.
2. **Monte Carlo credit.** Credit each step with the whole episode's return.
   Simple, but it rewards misroutes that were followed by recovery.
3. **TD with backward updates** (`QRouter.learn`). Each step is credited with
   its own reward plus the estimated value of where it led. Updating the last
   step first means one episode's delivery reaches its first pick immediately.

**Eligibility traces** (TD(λ)) blend 2 and 3. They are the next thing to read
once this page makes sense.

---

## 6. Results so far (300 tickets × 5 seeds, default settings, nothing tuned)

Share of tickets delivered; the drift column is the second half only, after the
change.

| scenario | dynamic | swarm | thompson | q-learning |
|---|---|---|---|---|
| homogeneous | 89.1% | **98.2%** | 97.2% | 93.1% |
| mixed | 77.2% | 79.1% | 76.3% | **80.9%** |
| systematic | 78.7% | 84.9% | 84.9% | **87.5%** |
| misled | 68.5% | 80.8% | 81.7% | **84.5%** |
| drift (2nd half) | 66.8% | 82.1% | **82.3%** | 74.3% |

Head-to-head, exact McNemar, `+a/−b` = tickets only the first arm / only the
second arm delivered:

| scenario | thompson vs swarm | q-learning vs swarm |
|---|---|---|
| homogeneous | +14/−29, p = 0.032 | +9/−86, p < 0.001 |
| mixed | +97/−139, p = 0.007 | +126/−99, p = 0.083 |
| systematic | +120/−120, p = 1.000 | +111/−71, p = 0.004 |
| misled | +164/−150, p = 0.463 | +150/−94, p < 0.001 |
| drift | +67/−86, p = 0.145 | +57/−114, p < 0.001 |

**What this means:**

- **Thompson sampling does not beat swarm here.** It ties in three scenarios
  and loses in two. Swarm was not a lucky find; a principled rival does no
  better on this problem.
- **Q-learning wins where state matters** (systematic, misled) **and loses
  where exploration or forgetting matter** (homogeneous, drift).
- **No arm wins everywhere.** Which learner is best depends on the workload:
  how varied it is, how the model misreads, and how fast the world changes.
  That is the same lesson as the crossover rule, one level up.
- **These are untuned defaults.** Tuning each arm on this benchmark and then
  reporting the benchmark would be grading your own homework. If you tune,
  tune on some seeds and report on others.

---

## 7. Exercises (each one is a small edit to `bench/swarm_sim.py`)

1. **Decaying ε.** Replace `Q_EPSILON` with `max(0.01, 0.3 * 0.99 ** episodes)`.
   Does Q-learning catch up with swarm on `homogeneous`, and what happens on
   `drift`?
2. **A stateful swarm.** Key the colony on `(label, tried)` instead of `label`.
   Does swarm gain what Q-learning gained on `misled`?
3. **Thompson with a state.** Same idea: per `(label, frozenset(tried))`.
4. **UCB1.** Pick the desk that maximises `mean + sqrt(2 ln N / n)`. It is
   deterministic where Thompson samples; compare the two.
5. **Hold-out tuning.** Tune `TS_DISCOUNT` on seeds 0–2, then report on seeds
   3–4 only.

## 8. What to read next

- Sutton & Barto, *Reinforcement Learning: An Introduction* (free online).
  Chapter 2 covers bandits, chapter 6 TD and Q-learning, chapter 12
  eligibility traces.
- Lattimore & Szepesvári, *Bandit Algorithms* (free online). Chapter 36 covers
  Thompson sampling.
- Russo et al., *A Tutorial on Thompson Sampling* (2018).
- Boyan & Littman, *Packet Routing in Dynamically Changing Networks: A
  Reinforcement Learning Approach* (1994). This is Q-routing, the RL cousin of
  AntNet and the closest classic relative of baton's problem.
