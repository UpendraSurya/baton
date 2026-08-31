# baton

[![no dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](pyproject.toml)

A Python library for multi-agent systems where **the agents choose who runs next**.

## Install

`baton-kernel` is **not on PyPI yet**, so install from source — a `pip install
baton-kernel` would 404 today, and this README will not tell you to run a command
that fails:

```bash
git clone https://github.com/upendrasurya/baton && cd baton
pip install .
```

## See it work — free, offline, no API key, two seconds

```bash
python3 examples/triage.py
```

```
billing   ratified   intake -> refunds     -> gate   $0.0000
bug       ratified   intake -> engineering -> gate   $0.0000
confused  ratified   intake -> docs        -> gate   $0.0000
```

Three tickets, one charter, one agent pool — and three different paths through it.
No edge was ever drawn between `intake` and `refunds`; intake decided at runtime.
Add `--live` to run the same charter against a real model (~$0.002).

Instead of a pre-baked DAG, each agent ends its output with one fenced `handoff`
block naming the next agent. The kernel makes that safe: legal-move enforcement,
a budget ceiling, a hop cap, a ping-pong detector, and a gate agent that is the
only role permitted to end a run.

## What's proven

Four things are true of this library today, each backed by a test or a
reproducible measurement — not by the routing idea it was built to test (see
[What's not proven yet](#whats-not-proven-yet) below).

**Zero dependencies, and it stays that way.** `dependencies = []` in
`pyproject.toml`. Providers talk HTTP through `urllib` from the standard
library — no vendor SDK, no transitive tree. `tests/test_isolation.py` fails
the build the moment that list grows past empty, and separately walks every
import in `baton/*.py` with `ast` to keep a vendor SDK from entering even as a
lazy, function-body import.

**A runtime that always stops, for a declared reason.** Legal-move
enforcement, a budget ceiling, a hop cap, a ping-pong detector, and a gate
agent that is the only role permitted to end a run — see
[The stop rules](#the-stop-rules) below for the full table of ten. An
adversarial suite drives deliberately hostile agents at it over **6,200
randomised runs** — 4,000 of them the termination property itself, 2,000
checking the backstop is never reached with every guard on, 200 checking a
gate outside the pool is always refused — and it has never produced a run
that ended any other way; eight of the ten reasons arise spontaneously in
that suite, and the two newest — `ratified_without_coverage` and
`time_exhausted` — are covered by targeted tests rather than by the fuzzer.
`tests/test_anti_vacuity.py` deletes each guard alone and asserts the
observed behaviour changes, so two guards covering for each other cannot hide
behind a green suite.

**The footprint, measured rather than asserted** — `pip install` into an
empty venv, Python 3.10 on an M-series Mac:

| | |
|---|---|
| wheel | **58 KB** (`baton_kernel-0.1.0-py3-none-any.whl`, 59,298 bytes) |
| packages added to the environment | **1** — its own; `Requires-Dist` is empty |
| cold import, installed | **~18 ms** (5 runs: 16.8 / 15.7 / 19.6 / 16.9 / 20.1) |

Reproduce it:

```bash
python3 -m build --wheel && python3 -m venv /tmp/v && /tmp/v/bin/pip install dist/*.whl
/tmp/v/bin/pip list --format=freeze          # baton-kernel, pip, setuptools
/tmp/v/bin/python -X importtime -c "import baton" 2>&1 | tail -1
```

Numbers move with machine and interpreter, so run it on yours rather than
trusting these. What does not move is the package count.

**It runs live against real providers.** `baton/providers/` ships `gemini`,
`mistral` and an OpenAI-compatible client (`openai_compat`, which also covers
Groq) — each talking raw HTTPS through `urllib`, each with a test suite that
injects a fake transport and inspects the exact request built. This is not
theoretical: a real run against Groq came back `HTTP 403` behind Cloudflare
because `urllib`'s default `Python-urllib/3.x` user agent is blocklisted, a
bug the scripted suite could not see (every provider test injects its own
transport) and a live call did — fixed and covered by
`tests/test_user_agent.py`. `python3 examples/smoke_gemini.py` runs one real
hop for about $0.004 against a live model, and `python3 examples/triage.py
--live` does the same for the routing demo above.

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

# What was ratified, without reading the trace:
result.coverage     # ('draft.md', 'draft.md') — one artifact per criterion, in order
result.artifacts    # the ArtifactRefs the gate checked that coverage against
                    # both are empty for every terminal reason except 'ratified'
```

`coverage` is **positional**: entry *i* answers criterion *i*. A gate that cannot
answer one of them writes `null` (or `""`) in that slot rather than skipping it —
skipping shifts every later entry up by one, and the array goes on looking
plausible while citing the wrong artifact for everything past the gap. Either way
the run is refused, but only the aligned form can say *which* criterion is
unanswered, and that is usually the thing you needed:

```
ratified_without_coverage: the gate ratified 3 acceptance criteria while naming
work for 2. Nothing is cited for: 'R-002 a deployment file'
```
```python

# The hop-by-hop record. `records()` is a METHOD, not a property:
for record in result.trace.records():
    print(record["event"], record.get("agent", ""))
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

**Once a run starts, it ends with exactly one of ten terminal reasons.** There
is no path out of the loop that returns "it just stopped".

| Killer | Defence | Terminal reason |
|---|---|---|
| never-done | `max_hops`, then one forced gate verdict | `hops_exhausted` |
| budget spiral | pressure line at 80%, hard halt at 100% | `budget_exhausted` |
| ping-pong | stall notice, forced gate, then stop | `stalled` |
| endless rejection | 2 rejects per proposing agent | `reject_cap_reached` |
| unroutable output | 2 attempts, then the gate judges | `charter_violation` |
| transport failure | no retry — a timeout is not a parse error | `dispatch_failure` |
| empty delivery | the ratified artifact set is checked, not asked about | `ratified_without_deliverable` |
| unevidenced delivery | RATIFY must name which artifact satisfies which criterion | `ratified_without_coverage` |
| a run that never comes back | `max_wall_seconds`, and `max_dispatch_seconds` for one hop that hangs | `time_exhausted` |
| — | the gate ratified, with something to point at | `ratified` |

Budget halts *without* a final gate call, unlike hops: a call made past the
ceiling would spend money the charter forbade.

**"Once a run starts" is a real boundary, not a hedge.** A charter that could
never have run at all — an empty pool, a gate agent with the wrong role, a
budget that cannot survive its own first hop — is refused *before* that point,
by raising `CharterInvalid`, not by manufacturing a `RunResult` for a run that
took zero hops. `run()` checks this three ways, all before any trace exists:
`charter.validate()`, the agent-roster check, and — only when you pass
`usd_per_call` — `plan.estimate(...).raise_if_infeasible()`. Once a trace has
been opened, `CharterInvalid` cannot happen again; every path out from there is
one of the ten reasons above. A caller who only handles `RunResult` needs one
`try/except CharterInvalid` around the call, the same way a config error is
handled anywhere else — not a wider `except` in the run loop it never reaches.

## What's not proven yet

The founding idea behind this library — that agents choosing their own
successor beats a graph you drew yourself — is **not** one of the claims
above, and this section is not hiding that.

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

The write-up, including why the measurement is hard and why a follow-up
attempt (`EMBED-1`) tests a deliberately weaker, defensible claim instead of
re-running this one, is in
[docs/measuring-dynamic-routing.md](docs/measuring-dynamic-routing.md). If you
want agents to route themselves anyway, [What's proven](#whats-proven) above
is the envelope that makes it safe to try — it just is not, itself, evidence
that you should prefer it to a graph you drew yourself.

## Known limitations

Two things that will bite you, documented here so they are limitations and not
mysteries.

### The routing contract makes the model escape its work product as JSON

`render_routing_contract` asks the agent for one JSON object carrying both the
routing decision **and** its entire free-text work product as a string field.
Long outputs break it: an unescaped quote or a raw newline inside that field and
the whole reply fails to parse.

Measured on 400 items, one model (`mistral-medium-latest`), one task. Arm A
failure rate by output-length quartile:

| quartile | contract failures |
|---|---|
| shortest 25% | 0.0% |
| 2nd | 0.0% |
| 3rd | 0.0% |
| longest 25% | **20.9%** |

Failures had a median of 610 output tokens; successes 247. Overall compliance
was 94.0% — the deficit is not spread evenly, it is entirely in long outputs.
A variant that moved the prose **out** of the envelope, leaving a fenced block
containing only `{"to": "<agent>"}`, failed **0 of 400** at a median of 70
output tokens, with accuracy non-inferior (84.8% vs 83.0%, McNemar p = 0.44).

**What that does and does not establish.** The mechanism is well supported —
the dose-response curve is flat-flat-flat-then-steep, and removing the
requirement removes the failures. The accuracy comparison is post-hoc and not
pre-registered, and the cheaper variant carries less: the structured artifact
no longer travels in the envelope, so if your downstream agents need that
payload handed to them, this moves the problem rather than solving it. That is
why the shipped contract is unchanged.

**What to do about it.** Keep per-hop work products short, or give agents a
shared disk and let `ArtifactRef.path` carry the deliverable instead of
`content`. `repair_nudge` already retries a malformed reply, and a run that
cannot recover ends at the `dispatch_failure` terminal reason rather than
silently losing the answer.

### `Charter.security_tier` is advisory — baton enforces nothing

It is validated against `("L", "M", "H")`, serialised, and printed into every
agent prompt. That is all it does. **No guard, budget, whitelist or terminal
reason branches on it**, and nothing in this library restricts what a
high-tier run may attempt. It exists because the host application this adapter
was written against classifies its own agents that way, and the model is told
which tier it is operating under.

If you need a real control, the enforced bounds are the ones in
[The stop rules](#the-stop-rules): `agent_pool`, `AgentSpec.can_hand_to`,
`budget_ceiling_usd`, `max_hops`, `max_wall_seconds`, `max_dispatch_seconds`.
Do not read `security_tier` as one of them.

## Testing

`bash verify.sh` — free, offline, deterministic. Tier 2 (`bench/replay.py
--confirm-spend`) costs real money and is never run by the gate.

Every guard in `baton.runtime.Guards` can be switched off individually — either
by constructing one explicitly (`Guards(hops=False)`) or, to turn one more off
an instance you already hold without losing what it already has configured,
`guards.without("hops")`. That is not a debug convenience:
`tests/test_anti_vacuity.py` deletes each one alone and asserts the behaviour
changes, because two overlapping guards cover for each other and leave a green
suite that proves nothing.

Parser fixtures are real recorded agent output pulled from the sandbox corpus
(`tests/fixtures/extract_real_prose.py`), not hand-written JSON — a parser suite
built only from clean canned blocks passes while the parser is useless on what
models actually emit.
