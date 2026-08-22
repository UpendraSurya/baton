# baton

[![no dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](pyproject.toml)

A Python library for multi-agent systems where **the agents choose who runs next**.

```bash
pip install baton-kernel
```

Instead of a pre-baked DAG, each agent ends its output with one fenced `handoff`
block naming the next agent. The kernel makes that safe: legal-move enforcement,
a budget ceiling, a hop cap, a ping-pong detector, and a gate agent that is the
only role permitted to end a run.

## What is proven, and what is not

Dynamic routing was measured against the static DAG it replaces, on 30 real
projects, three arms, 90 runs, one model held constant:

| arm | delivered | vs static (exact McNemar) |
|---|---|---|
| static DAG | 19/30 (63%) | — |
| blind dynamic | 17/30 (57%) | p = 0.774 |
| reputation-aware dynamic | 21/30 (70%) | p = 0.791 |

**Nothing separated.** 12 of the 30 briefs produced identical outcomes under all
three arms and carry no information at all. So this library does not claim that
agents choosing their own successor beats a graph you drew yourself — its own
benchmark declined to show that.

What *is* proven is the **runtime**: every run ends for exactly one of eight
declared reasons, demonstrated over 4,000 adversarial runs with all eight
observed. If you want agents to route themselves, this is the envelope that
makes it safe to try. The write-up, including why the measurement is hard, is
in [docs/measuring-dynamic-routing.md](docs/measuring-dynamic-routing.md).

**Zero dependencies, and it stays that way.** Providers talk HTTP through `urllib`
from the standard library — no vendor SDK, no transitive tree. A test fails the
build if that ever changes.

```
baton/              the core: Agent, Baton, Charter, Runtime, Trace
baton/providers/    gemini, mistral, openai_compat — HTTPS via urllib, no SDK
baton/adapters/     bind the kernel to a host application
```

Design and rejected alternatives:
`~/dev-notes/projects/baton/2026-08-15_baton-dynamic-handoff-kernel-design.md`

## Using it

```python
from baton import AgentSpec, Charter, run
from baton.providers import gemini

agents = {
    "researcher": AgentSpec("researcher", "You gather facts.",
                            {"writer", "editor", "gate"}),
    "writer":     AgentSpec("writer", "You draft copy.",
                            {"researcher", "editor", "gate"}),
    "editor":     AgentSpec("editor", "You tighten copy.",
                            {"writer", "researcher", "gate"}),
    "gate":       AgentSpec("gate", "You judge against the criteria.",
                            {"researcher", "writer", "editor"}, role="gate"),
}

charter = Charter(
    brief="Write a launch announcement for baton",
    entry_agent="researcher", gate_agent="gate",
    agent_pool=frozenset(agents),
    acceptance_criteria=("under 120 words", "no marketing cliches"),
    budget_ceiling_usd=0.25, max_hops=8,
)

result = run(charter, agents, gemini.provider())   # reads GEMINI_API_KEY
print(result.terminal_reason, result.path, result.spend_usd)
# ratified ('researcher', 'writer', 'gate') 0.0039
```

Nobody wrote that route. The agents chose it.

Try it: `python3 examples/smoke_gemini.py` (needs `GEMINI_API_KEY`, costs ~$0.004).

## Why not LangGraph

LangGraph gives you a graph and asks you to draw the edges. baton deletes the
edges and gives you a **Charter** instead — a budget ceiling, a hop cap, an agent
pool and acceptance criteria — then lets the agents route inside that envelope.
The engineering is not the decision (an LLM emitting `transfer_to_writer` is
trivial); it is the runtime around it: legal-move enforcement, loop detection, a
cost meter that refuses to run unmetered, and a gate agent that is the only role
permitted to end a run.

## The stop rules

Every run ends with exactly one of eight terminal reasons. There is no path out
of the loop that returns "it just stopped".

| Killer | Defence | Terminal reason |
|---|---|---|
| never-done | `max_hops`, then one forced gate verdict | `hops_exhausted` |
| budget spiral | pressure line at 80%, hard halt at 100% | `budget_exhausted` |
| ping-pong | stall notice, forced gate, then stop | `stalled` |
| endless rejection | 2 rejects per proposing agent | `reject_cap_reached` |
| unroutable output | 2 attempts, then the gate judges | `charter_violation` |
| transport failure | no retry — a timeout is not a parse error | `dispatch_failure` |
| empty delivery | the ratified artifact set is checked, not asked about | `ratified_without_deliverable` |
| — | the gate ratified, with something to point at | `ratified` |

Budget halts *without* a final gate call, unlike hops: a call made past the
ceiling would spend money the charter forbade.

## Testing

`bash verify.sh` — free, offline, deterministic. Tier 2 (`bench/replay.py
--confirm-spend`) costs real money and is never run by the gate.

Every guard in `baton.runtime.Guards` can be switched off individually. That is
not a debug convenience: `tests/test_anti_vacuity.py` deletes each one alone and
asserts the behaviour changes, because two overlapping guards cover for each
other and leave a green suite that proves nothing.

Parser fixtures are real recorded agent output pulled from the sandbox corpus
(`tests/fixtures/extract_real_prose.py`), not hand-written JSON — a parser suite
built only from clean canned blocks passes while the parser is useless on what
models actually emit.
