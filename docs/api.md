# API reference

Every name in `baton.__all__` (46 of them). Generated from the source by `scripts/gen_api_docs.py` — if you add a public symbol and do not regenerate, `verify.sh` fails.

## Running a job

### `run`

```python
run(charter: 'Charter', agents: 'Mapping[str, AgentSpec]', dispatch: 'Dispatch', *, trace: 'Optional[TraceSink]' = None, guards: 'Optional[Guards]' = None, trace_id: 'Optional[str]' = None, usd_per_call: 'float' = 0.0) -> 'RunResult'
```

Run a multi-agent task in which the agents choose who goes next.

### `Charter`

```python
brief: str
entry_agent: str
gate_agent: str
agent_pool: frozenset[str]
acceptance_criteria: tuple[str, ...]
budget_ceiling_usd: float = 5.0
max_hops: int = 12
max_wall_seconds: float = 3600.0
max_dispatch_seconds: float = 300.0
security_tier: str = 'M'
max_rejects_per_agent: int = 2
pressure_threshold: float = 0.8
```

Charter(brief: 'str', entry_agent: 'str', gate_agent: 'str', agent_pool: 'frozenset[str]', acceptance_criteria: 'tuple[str, ...]', budget_ceiling_usd: 'float' = 5.0, max_hops: 'int' = 12, max_wall_seconds: 'float' = 3600.0, max_dispatch_seconds: 'float' = 300.0, security_tier: 'str' = 'M', max_rejects_per_agent: 'int' = 2, pressure_threshold: 'float' = 0.8)

### `AgentSpec`

```python
name: str
instructions: str
can_hand_to: frozenset[str] = frozenset()
role: Role = 'worker'
model_id: str = ''
```

One agent, as the runtime sees it.

### `RunResult`

```python
trace_id: str
terminal_reason: str
hops: int
spend_usd: float
gate_summary: str = ''
note: str = ''
path: tuple[str, ...] = ()
coverage: tuple[str, ...] = ()
artifacts: tuple[ArtifactRef, ...] = ()
last_baton: Optional[Baton] = None
trace: Optional[TraceSink] = None
```

The outcome of a run. `terminal_reason` is always one of TERMINAL_REASONS.

### `Dispatch`

The entire contract between baton and whatever runs a model.

### `GATE`

`'gate'`

### `WORKER`

`'worker'`

### `worker`

```python
worker(name: 'str', instructions: 'str', can_hand_to: 'Iterable[str]' = ()) -> 'AgentSpec'
```

Convenience constructor for a worker agent.

### `gate`

```python
gate(name: 'str', instructions: 'str', can_hand_to: 'Iterable[str]' = ()) -> 'AgentSpec'
```

Convenience constructor for the gate agent — the only role that can end a run.

## What agents send

### `Baton`

```python
trace_id: str
hop: int
from_agent: str
to_agent: str
goal: str
rationale: str = ''
artifacts: tuple[ArtifactRef, ...] = ()
open_questions: tuple[str, ...] = ()
stall_notice: str = ''
flags: tuple[str, ...] = ()
```

Baton(trace_id: 'str', hop: 'int', from_agent: 'str', to_agent: 'str', goal: 'str', rationale: 'str' = '', artifacts: 'tuple[ArtifactRef, ...]' = (), open_questions: 'tuple[str, ...]' = (), stall_notice: 'str' = '', flags: 'tuple[str, ...]' = ())

### `Decision`

```python
kind: Kind
to: str = ''
goal: str = ''
rationale: str = ''
summary: str = ''
reason: str = ''
artifacts: tuple[ArtifactRef, ...] = ()
coverage: tuple[str, ...] = ()
nothing_found: bool = False
```

Decision(kind: 'Kind', to: 'str' = '', goal: 'str' = '', rationale: 'str' = '', summary: 'str' = '', reason: 'str' = '', artifacts: 'tuple[ArtifactRef, ...]' = (), coverage: 'tuple[str, ...]' = (), nothing_found: 'bool' = False)

### `Kind`

The four verbs an agent may send. HANDOFF and PROPOSE_DONE are the worker's; RATIFY and REJECT are the gate's. Nothing else is a decision.

### `ArtifactRef`

```python
path: str
description: str = ''
preview: str = ''
content: str = ''
content_chars: int = 0
truncated: bool = False
```

ArtifactRef(path: 'str', description: 'str' = '', preview: 'str' = '', content: 'str' = '', content_chars: 'int' = 0, truncated: 'bool' = False)

### `parse_decision`

```python
parse_decision(text: 'str') -> 'Decision'
```

The LAST parseable block wins — agents show their working, then commit.

### `validate_decision`

```python
validate_decision(decision: 'Decision', agent: 'AgentSpec', charter: 'Charter', *, enforce_target: 'bool' = True) -> 'Decision'
```

Return the decision, or raise. enforce_target=False removes guard B (the post-parse whitelist check) and exists so the anti-vacuity suite can measure it alone. It never disables the ROLE check — that is a different guard, and letting one switch turn off two is how guards start covering for each other.

### `ParseFailure`

No usable routing block was found in the agent's output.

## Bounds and guards

### `Guards`

```python
hops: bool = True
budget: bool = True
cycle: bool = True
reject_cap: bool = True
validate_target: bool = True
render_legal_moves: bool = True
require_artifacts: bool = True
require_coverage: bool = True
require_substance: bool = True
require_independent_work: bool = True
wall_clock: bool = True
preflight: bool = True
```

Every defence, individually switchable.

### `TERMINAL_REASONS`

`('ratified', 'budget_exhausted', 'hops_exhausted', 'stalled', 'dispatch_failure', 'charter_violation', 'reject_cap_reached', 'ratified_without_deliverable', 'ratified_without_coverage', 'time_exhausted')`

### `BatonError`

Base for every error this kernel raises.

### `CharterInvalid`

The pre-flight envelope does not describe a runnable run.

### `IllegalTarget`

The named next agent is outside can_hand_to ∩ charter.agent_pool.

### `DispatchResult`

```python
text: str = ''
cost_usd: float = 0.0
in_tokens: int = 0
out_tokens: int = 0
error: str = ''
model_id: str = ''
```

What one agent execution produced. `error` non-empty ends the run.

## Prompts

### `render_prompt`

```python
render_prompt(agent: 'AgentSpec', baton: 'Baton', charter: 'Charter', *, pressure: 'bool' = False, include_legal_moves: 'bool' = True, nudge: 'str' = '') -> 'str'
```

persona -> baton -> charter bounds -> routing contract -> (nudge).

### `render_routing_contract`

```python
render_routing_contract(agent: 'AgentSpec', charter: 'Charter', *, include_legal_moves: 'bool' = True) -> 'str'
```

The block appended to every prompt. Generated from THIS agent's whitelist.

### `repair_nudge`

```python
repair_nudge(reason: 'str', agent: 'AgentSpec', charter: 'Charter') -> 'str'
```

One terse correction. Nothing else is re-sent — a full re-render doubles the cost of a hop that has already failed once.

## Traces

### `MemoryTrace`

Same interface, no file. For tests and dry runs that do not need durability.

### `TraceSink`

What runtime.run needs from a trace. Implement this to send hops to your own observability stack instead of a file.

## Planning

### `estimate`

```python
estimate(charter: 'Charter', agents: 'Mapping[str, AgentSpec]', usd_per_call: 'float' = 0.0) -> 'Estimate'
```

Worst-case cost and shape of a run, before it starts.

### `Estimate`

```python
worst_case_hops: int
worst_case_calls: int
worst_case_usd: float
ceiling_usd: float
feasible: bool
problems: tuple[str, ...] = ()
warnings: tuple[str, ...] = ()
unreachable: tuple[str, ...] = ()
dead_ends: tuple[str, ...] = ()
```

Estimate(worst_case_hops: 'int', worst_case_calls: 'int', worst_case_usd: 'float', ceiling_usd: 'float', feasible: 'bool', problems: 'tuple[str, ...]' = (), warnings: 'tuple[str, ...]' = (), unreachable: 'tuple[str, ...]' = (), dead_ends: 'tuple[str, ...]' = ())

### `reachable_from`

```python
reachable_from(entry: 'str', agents: 'Mapping[str, AgentSpec]', charter: 'Charter') -> 'set[str]'
```

Every agent the run can actually arrive at, following whitelists.

### `crossover`

`baton.crossover`

baton.crossover — should you route at all?

### `Crossover`

```python
rho_star: float
verdict: str
best_static: StaticPath
path_variance: float
n_tasks: int
pool_size: int
perception_accuracy: float
hops_per_routed_task: float
routing_calls_per_task: float
price_ratio: Optional[float]
dynamic_cost_per_success: float
exact_search: bool
candidates: tuple[StaticPath, ...]
```

The answer to "should this workload be routed?", with its working.

### `StaticPath`

```python
path: frozenset[str]
success: float
rho_star: float
cost_per_success: float
```

One candidate fixed path and what routing must beat in it.

## Reputation

### `Reputation`

A read-only mapping of agent name -> AgentRecord, plus the two views a router actually uses: `ranked()` and `note()`.

### `AgentRecord`

```python
agent: str
runs: int
revisions: int
```

One agent's track record. `runs` is here so a caller can see that a 50% rate is 1-in-2 rather than 39-in-78.

### `from_traces`

```python
from_traces(traces: 'Iterable[Iterable[Mapping[str, object]]]', *, min_runs: 'int' = 3) -> 'Reputation'
```

Build a Reputation from baton's own trace records — `Trace.records()` from past runs, one list per run.

### `from_tallies`

```python
from_tallies(runs: 'Mapping[str, int]', revisions: 'Mapping[str, int]', *, min_runs: 'int' = 3) -> 'Reputation'
```

Build a Reputation from two counts: pieces of work produced per agent, and pieces of that work that came back for rework.

## Swarm routing

### `swarm`

`baton.swarm`

baton.swarm — stigmergic routing memory: handoff trails that runs reinforce and time erodes.

### `Colony`

Per-edge trails over a team of agents, updated one observed run at a time.

### `Trail`

```python
src: str
dst: str
strength: float
runs: int
ratified: int
```

One edge's evidence. `strength` is the recency-weighted signal a router samples on; `runs` and `ratified` are the raw counts a reader can check it against, and they never decay. `ratified` counts runs this edge DELIVERED by — on the loop-free route of a ratified run — not merely runs it appeared in.

## Metadata

### `__version__`

`'0.1.0'`

## Ungrouped

These are public but not yet placed in a section:

- `Trace` — 
- `plan` — baton.plan — what a run will cost and how it can end, BEFORE spending anything.
- `reputation` — baton.reputation — per-agent reliability, computed from what happened.
- `RoleViolation` — A worker used a gate-only verb, or the gate used a worker-only verb.
- `TraceCorrupt` — The trace file changed underneath us — append-only was violated.
