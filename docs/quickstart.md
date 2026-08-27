# Quickstart

`baton-kernel` is not on PyPI yet, so install from source:

```bash
git clone https://github.com/upendrasurya/baton && cd baton
pip install .
```

Nothing else installs. `Requires-Dist` is empty and a test fails the build if
that ever changes.

## The smallest thing that works

Four agents, a budget, and a gate. Nobody draws the edges.

```python
from baton import AgentSpec, Charter, run
from baton.providers import mistral        # or gemini, or openai_compat

agents = {
    "researcher": AgentSpec("researcher", "You gather facts and cite them.",
                            {"writer", "editor", "gate"}),
    "writer":     AgentSpec("writer", "You draft copy.",
                            {"researcher", "editor", "gate"}),
    "editor":     AgentSpec("editor", "You tighten copy.",
                            {"writer", "researcher", "gate"}),
    "gate":       AgentSpec("gate", "You judge against the acceptance criteria.",
                            {"researcher", "writer", "editor"}, role="gate"),
}

charter = Charter(
    brief="Write a launch announcement for baton",
    entry_agent="researcher",
    gate_agent="gate",
    agent_pool=frozenset(agents),
    acceptance_criteria=("under 120 words", "no marketing cliches"),
    budget_ceiling_usd=0.25,
    max_hops=8,
)

result = run(charter, agents, mistral.provider())   # reads MISTRAL_API_KEY
print(result.terminal_reason, result.path, result.spend_usd)
# ratified ('researcher', 'writer', 'gate') 0.0039
```

## Three things to understand

### 1. The run always ends, and always says why

```python
result.terminal_reason   # one of exactly ten
```

| reason | what happened |
|---|---|
| `ratified` | the gate approved, with work to point at |
| `hops_exhausted` | hit `max_hops`, then one forced gate verdict |
| `budget_exhausted` | hit `budget_ceiling_usd`. Halts *without* a final call |
| `stalled` | ping-pong detected, forced gate, then stop |
| `reject_cap_reached` | an agent was rejected twice |
| `charter_violation` | output could not be routed after two attempts |
| `dispatch_failure` | the transport failed. No retry — a timeout is not a parse error |
| `time_exhausted` | `max_wall_seconds`, or one hop hanging past `max_dispatch_seconds` |
| `ratified_without_deliverable` | the gate approved nothing |
| `ratified_without_coverage` | the gate did not say which work met which criterion |

There is no eleventh, and no path that returns "it just stopped".

### 2. An agent cannot hand to someone not on its list

The third argument to `AgentSpec` is its whitelist. **An agent is never on its
own list**, and anything absent is not an option the model is offered:

```python
AgentSpec("registry", "You look up company filings.", {"pain", "gate"})
# registry cannot reach `contact` — a scraped address must never become an
# outreach address. The rule is a MISSING EDGE, local to the agent it
# constrains, not a sentence in a central prompt that a model may ignore.
```

Measured on 400 routing decisions: a supervisor router given the same task
*plus the worker's full output* chose an illegal target on 46 of them. An agent
routing under a whitelist chose one 0 times — it was never offered the option.

### 3. What was ratified, without reading the trace

```python
result.coverage    # ('draft.md', 'draft.md') — one entry per criterion, in order
result.artifacts   # the ArtifactRefs the gate checked coverage against
```

`coverage` is **positional**: entry *i* answers criterion *i*. A gate that
cannot answer one writes `null` in that slot rather than skipping it — skipping
shifts every later entry up by one and the array goes on looking plausible while
citing the wrong artifact for everything past the gap.

## Running it without a model

Every provider takes an injectable transport, and `Dispatch` is just a callable,
so a scripted agent needs no network and no key:

```python
from baton import Decision, DispatchResult, run
from baton.packet import Kind

def scripted(agent, packet, prompt):
    return DispatchResult(
        text=Decision(kind=Kind.HANDOFF, to="gate", goal="done",
                      rationale="finished").render())

run(charter, agents, scripted)      # $0, offline, deterministic
```

`Decision.render()` is the inverse of `parse_decision` and round-trip safe with
it. Use it for fixtures, replay harnesses, and gates that run tests instead of
asking a model.

Set `BATON_FORBID_REAL_DISPATCH=1` and any real provider call raises instead of
spending. Put it in your test suite's environment.

## Next

- `docs/api.md` — every public symbol
- `docs/measuring-dynamic-routing.md` — what has and has not been proven
- `examples/triage.py` — a 4-agent support desk you can run
