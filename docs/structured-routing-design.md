# Structured routing: roads you can choose to harden

> **Status:** a design proposal. Nothing in this file is implemented yet.
> Written 2026-09-23 on `mobile-branch`, to build later.

## 1. The doubt this answers

Agent systems don't need to be dynamic all the time. Sometimes a linear path
or a small branch beats dynamic handoffs. baton's own benchmarks agree:

- **`bench/swarm_sim.py`:** on a single ticket type, the fixed graph beat every
  dynamic arm (99.6% vs 89.1% for blind dynamic routing).
- **`baton.crossover`:** at zero path variance, a fixed path is optimal and
  routing is pure overhead.

**Question:** how should baton support structure, with the gate and failure
checks still working, without becoming another hand-drawn-graph library?

## 2. The idea: structure that is learned, then chosen by the user

- **Other libraries force a choice up front:** draw a graph (LangGraph, CrewAI
  sequential), or let agents decide everything (handoff SDKs).
- **baton would start dynamic** and watch which handoffs actually deliver.
  Routes that prove themselves can be hardened into fixed "roads", like ant
  trails becoming roads, and loosened again if they start failing.

**Key decision: this is an opt-in feature, not default behaviour.**

- With no action from the user, baton routes dynamically, exactly as today.
- Hardening happens only when the user asks for advice **and** accepts it,
  step by step.
- Ignoring it entirely is fine; nothing breaks and nothing happens
  automatically.

## 3. The three step modes

| mode | who picks the next agent | model routing cost | how it's expressed |
|---|---|---|---|
| **fixed** | nobody: always the same next agent | zero | `can_hand_to={"refunds"}`: a whitelist of one, which baton already supports |
| **branch** | a small code rule (e.g. "bug report → engineering") | zero | a future extension; not in the first version |
| **dynamic** | the agent, from its whitelist | rides on the agent's own call | today's baton |

**Unchanged in every mode:** the gate, the ratify checks
(`require_substance`, `require_coverage`), all ten stop rules, and the trace.
A fixed road still can't end without the gate ratifying real work. Drawn graphs
in other libraries don't give that by default.

## 4. Proposed API (opt-in, about 100 lines in `baton.swarm`)

### Step 1: ask for advice. It only reads, and changes nothing.

```python
from baton import swarm

tips = swarm.suggest_roads(past_traces, min_runs=30, confidence=0.95)
print(tips.render())
# intake -> refunds    fix it?  47/49 ratified runs went this way (lower bound 88%)
# refunds -> gate      fix it?  49/49
# docs                 keep dynamic: no route above 60%
```

### Step 2: apply only the suggestions the user likes

```python
agents = tips.apply(agents, only=["intake"])   # returns a NEW agents dict
```

### Step 3 (optional): get told when a road should be loosened

```python
tips = swarm.suggest_roads(recent_traces, current=agents)
# intake -> refunds    consider unfixing: 12 of the last 20 were rejected
```

## 5. Rules

- **Advice never acts on its own.** `suggest_roads` only reads; `apply`
  returns a copy. The user's original agents are never modified.
- **Conservative statistics.** Suggest a road only when the **lower bound**
  (Wilson interval) of its share of successful runs clears the threshold, not
  the raw average, so a few lucky runs never become a road.
- **Credit comes from `swarm.loop_free`.** Only the route that actually
  delivered counts; misroutes and rework loops don't.
- **Reversible.** The same function flags hardened roads whose rejection rate
  has risen.
- **Every suggestion states its evidence.** For example, "47/49 ratified runs",
  in the same counts-not-adjectives style as `note()`.
- **Zero dependencies, and no change to the runtime.** A fixed road is just a
  one-entry whitelist.

**A possible later runtime improvement:** when an agent's whitelist has
exactly one worker target, skip rendering the routing instructions and hand
off automatically. That saves tokens and removes a failure mode (a malformed
routing block). It is an optimisation, not part of the first version.

## 6. How it's different

| system | how structure is decided |
|---|---|
| LangGraph, CrewAI | a human draws it; it never learns |
| handoff-based SDKs | everything is dynamic, every time |
| **baton (proposed)** | structure is *earned* from evidence, *chosen* by the user, still *verified* by the gate, and *reversible* |

This combination was not found as a built-in in the libraries reviewed in
`docs/patterns-landscape.md`.

## 7. How to test it (when building)

1. **Unit tests:**
   - no suggestion under `min_runs`;
   - a lower-bound threshold, so 3/3 isn't a road;
   - `apply` returns a copy and touches only the chosen agents;
   - misroutes are never credited to a road;
   - "consider unfixing" appears after the rejection rate rises.
2. **A benchmark arm in `bench/swarm_sim.py`, "roads":** run dynamic for N
   tickets, call `suggest_roads`, apply everything, then continue. Expected:
   - **homogeneous:** it reaches the fixed-graph result (≈99.6%) at almost no
     routing cost;
   - **mixed:** intake stays dynamic;
   - **drift:** it flags the billing road for unfixing after the change.
3. **Report honestly** where it doesn't help, as in the other benchmark docs.
