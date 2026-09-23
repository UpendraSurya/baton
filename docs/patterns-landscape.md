# Agent routing patterns, who ships them, and where baton fits

*A map of how multi-agent libraries decide "who works next", plus a $0
simulated comparison of the patterns themselves. Library features were checked
against their public documentation in September 2026. Sources are at the end.
These frameworks change fast, so re-check before quoting.*

---

## 1. The patterns

Every multi-agent library answers one question: **when an agent finishes, who
runs next, and who decides the run is over?** The answers fall into a small
number of patterns.

| # | pattern | who decides the next agent | who ends the run | extra model calls for routing |
|---|---|---|---|---|
| 1 | **Sequential pipeline** | the developer, at build time | the last step | none |
| 2 | **Conditional branch / router** | a function or classifier call over the state; edges drawn by hand | the branch's last step | 0–1 per decision |
| 3 | **Concurrent / fan-out** | everyone runs; an aggregator combines | the aggregator | 1 aggregator |
| 4 | **Loop** | the developer; repeats until a condition holds | the condition or an iteration cap | none |
| 5 | **Round-robin group chat** | fixed rotation | a termination condition or critic | none, but every member speaks every round |
| 6 | **Selector group chat** | an LLM picks the next speaker after every message | a termination condition | 1 per turn |
| 7 | **Supervisor / manager / agents-as-tools** | a central LLM calls specialists like tools | the supervisor | 1 per step, plus review |
| 8 | **Peer handoff** | the current agent, via a handoff tool call | whichever agent answers | none: the decision rides on the agent's own call |
| 9 | **Magentic / ledger orchestrator** | an orchestrator keeping a task and progress ledger | the orchestrator | 1+ per step |
| 10 | **Event-driven / publish-subscribe** | whoever listens for the event | a terminal event | none |

## 2. Which library ships which pattern

| library | patterns shipped | names in the library |
|---|---|---|
| **LangGraph** | 1, 2, 4, 7, 8 | `StateGraph` edges; `add_conditional_edges`; `Command(goto=...)`; `langgraph-supervisor`; `langgraph-swarm` (peer handoff tools) |
| **OpenAI Agents SDK** | 7, 8 | handoffs (`transfer_to_<agent>` tools); agents-as-tools (manager pattern) |
| **AutoGen (AgentChat)** | 1, 2, 3, 4, 5, 6, 8, 9 | `RoundRobinGroupChat`; `SelectorGroupChat`; `Swarm` + `HandoffMessage`; `GraphFlow` (DiGraph); `MagenticOneGroupChat` |
| **CrewAI** | 1, 2, 7, 10 | `Process.sequential`; `Process.hierarchical` (manager LLM); Flows with `@start` / `@listen` / `@router` |
| **Google ADK** | 1, 3, 4, 7, 8 | `SequentialAgent`, `ParallelAgent`, `LoopAgent`; LLM-driven `transfer_to_agent`; `AgentTool` |
| **Microsoft Agent Framework / Semantic Kernel** | 1, 3, 6, 8, 9 | Sequential, Concurrent, Handoff, Group Chat, Magentic orchestrations |
| **LlamaIndex** | 8, 10 | `AgentWorkflow` (agents hand off to each other); event-driven Workflows |
| **Strands Agents (AWS)** | 1, 2, 7, 8 | Graph, Workflow, agents-as-tools, Swarm |
| **baton** | 8, plus what is in §3 | agent-chosen `HANDOFF` inside a `can_hand_to` whitelist; a gate-only `RATIFY`; `baton.swarm` learned routing |

**Name clash to know about.** `langgraph-swarm`, AutoGen's `Swarm`, Strands'
Swarm and OpenAI's original *Swarm* are all **peer handoff** (pattern 8). None
of them is swarm-intelligence learning. `baton.swarm` is the other meaning:
ant-colony trails learned across runs. Say "stigmergic" or "learned
trail-based" routing so readers don't confuse the two.

---

## 3. What baton does that the others do not ship by default

Each row below was checked against the documentation listed in the sources.
The right-hand column says what the others *have*, so nothing here claims they
lack something they ship.

| baton | what the others ship |
|---|---|
| **Only a designated gate agent can end a run.** A worker can only *propose* completion. | Most end when an agent returns a final answer, or on a termination condition you configure. |
| **Mechanical ratification checks.** A `RATIFY` must cite an existing, non-stub artifact for every acceptance criterion, or the run is refused (`ratified_without_deliverable` / `ratified_without_coverage`). | Validation is available (output guardrails, custom termination conditions, structured outputs), but you add and design it yourself. |
| **Every run returns one of ten named terminal reasons.** Hitting a cap is a result, not an exception. | Caps exist everywhere. LangGraph's `recursion_limit` (default 25 steps) raises `GraphRecursionError`; the OpenAI Agents SDK's `max_turns` (default 10) raises `MaxTurnsExceeded`. |
| **A ping-pong detector, a reject cap, a wall-clock deadline, and a budget ceiling in dollars**, each individually switchable and tested to be non-vacuous. | Turn and message caps are standard. Budget-in-dollars as a stop rule was not found as a built-in in the docs reviewed. |
| **Learned routing across runs** (`baton.swarm`): handoff trails from past traces, with evaporation. | Not found as a built-in in any library reviewed. Routing is decided per run, by an LLM or by rules. |
| **A pre-flight "should you route at all?" rule** (`baton.crossover`). | Not found as a built-in. |
| **Zero runtime dependencies**, enforced by a test. | Not measured here. |

**What baton does *not* have that others do:** parallel fan-out, streaming,
checkpointing and resume, human-in-the-loop approvals, a visual graph editor,
tool and MCP ecosystems, and large communities. It is a routing kernel, not a
full framework.

---

## 4. The comparison: patterns in one simulated world

`python3 bench/patterns.py` ($0, about 5 seconds) models each pattern's control
flow in one shared world.

**What the patterns share:**

- the same tickets;
- the same chance of a model misreading a ticket;
- the same worker quality;
- the same LLM judge. It accepts good work 95% of the time, a plausible wrong
  answer 15% of the time, and an empty "I'm done" stub 30% of the time;
- the same budget of 10 model calls per ticket.

**The one asymmetry is baton's real behaviour:** its gate refuses stubs
mechanically before the judge sees them.

It compares **patterns, not libraries**. No library is installed or called.

**Uniform world** (misreads are random), 5,000 tickets:

| pattern | truly delivered | reported success | **silent failures** | calls per delivery |
|---|---|---|---|---|
| sequential | 28.3% | 100% | 71.7% | 3.53 |
| conditional router | 63.6% | 100% | 36.4% | 3.14 |
| concurrent fan-out | 80.3% | 100% | 19.7% | 4.98 |
| round-robin + critic | **94.4%** | 97.3% | **2.9%** | 4.97 |
| selector group chat | 88.7% | 97.0% | 8.3% | **3.80** |
| supervisor | 87.6% | 95.6% | 8.1% | 4.84 |
| peer handoff | 70.7% | 100% | 29.3% | 3.05 |
| **baton** | 89.1% | 94.8% | **5.7%** | 4.88 |
| baton + swarm | 88.5% | 94.3% | 5.8% | 4.91 |

A **silent failure** is a ticket the system *reported* as done that was not
actually resolved. It is the costly kind of failure, because nobody goes back
to fix it.

### What this shows, including where baton loses

- **Round-robin wins on quality, not on cost.** It is best on delivery (94.4%)
  and on silent failures (2.9%), because every desk attempts every ticket. It
  pays every agent on every round, and every agent reads the whole chat, which
  this bench does not charge for.
- **Selector group chat wins on cost among patterns that verify.** It needs
  3.80 calls per delivery against baton's 4.88, and its delivery rate is tied
  with baton's (p = 0.17).
- **Patterns with no verifier are cheap and report 100% success.** Sequential,
  conditional, fan-out and peer handoff never say "I failed", so 20–72% of their
  "successes" are silent failures.
- **Baton has the fewest silent failures of the patterns that send each ticket
  to one agent at a time:** 5.7%, against 8.1–8.3% for supervisor and group
  chat, and 29.3% for peer handoff. This comes from one mechanism, refusing
  stubs mechanically.
- **Swarm adds nothing in this world.** With three desks and a judge in the
  loop, there is almost nothing left for learning to fix. It helped in
  `bench/swarm_sim.py`, where misroutes were costlier; it does not help here,
  and this page does not claim otherwise.

### How sensitive the baton result is

The advantage exists only when agents sometimes claim to be done without doing
the work, and judges are lenient about it. Silent failures, uniform world,
3,000 tickets per cell:

| share of bad attempts that are stubs | judge accepts a stub | baton | supervisor | group chat |
|---|---|---|---|---|
| 0% | any | 6.8% | 6.8% | 7.1% |
| 25% | 30% | 6.1% | 7.5% | 7.8% |
| 50% | 30% | 5.6% | 8.2% | 8.4% |
| 50% | 50% | 5.6% | 9.5% | 9.7% |

**With no fake completions, baton is no better.** The advantage scales with how
often agents fake completion, and in real runs they do. baton's own batch 3
found 33 of 90 real runs (37%) were ratified by an LLM gate with *nothing*
delivered ([measuring-dynamic-routing.md](measuring-dynamic-routing.md)).

---

## 5. What you can honestly claim

**Supported by this repo:**

1. "baton is a zero-dependency routing kernel where agents choose who runs
   next, and only a verifying gate can end a run."
2. "Every run ends with one of ten named reasons, checked across 6,200
   adversarial randomised runs, and hitting a limit returns a result instead of
   raising."
3. "In a simulated comparison of nine routing patterns with the same model
   quality and the same fallible judge, baton's mechanical ratification checks
   gave the fewest silent failures of any pattern that routes to one agent at a
   time. That was 5.7% against 8–8.3% for supervisor and group chat, and 29% for
   peer handoff, at similar cost to a supervisor. The gap grows the more agents
   fake completion; when they don't, there is no gap."
4. "baton adds learned, trail-based routing across runs (`baton.swarm`). In
   simulation it beats blind dynamic routing when models misread tickets in a
   consistent way. It has not yet been tested with a real model."

**Not supported:** "faster", "cheaper than X", "better than LangGraph / CrewAI /
AutoGen", or any headline delivery-rate win. The simulation compares patterns,
not those libraries. A library user who adds their own validator can close the
silent-failure gap, and round-robin and group chat beat baton on delivery and
on cost respectively.

---

## 6. Draft LinkedIn post (claims limited to §5)

> I built **baton**, a small Python kernel for multi-agent systems where the
> agents choose who runs next instead of following a graph you drew.
>
> The part I care about most is how runs *end*. In most multi-agent setups, a
> run is "done" when some agent says so. Across baton's own real runs, a model
> acting as the judge approved 37% of them when nothing had been delivered at
> all. So in baton only a gate agent can end a run, and it has to cite a real,
> non-empty artifact for every acceptance criterion or the runtime refuses the
> approval.
>
> I mapped the 10 routing patterns that LangGraph, the OpenAI Agents SDK,
> AutoGen, CrewAI, Google ADK and others ship. Then I simulated all of them in
> one world, with the same model quality and the same fallible judge. Among
> patterns that hand work to one agent at a time, baton had the fewest *silent
> failures* (tasks reported done that weren't): 5.7%, against about 8% for
> supervisor and group chat and 29% for plain peer handoff.
>
> Honest caveats: it's a simulation of patterns, not a benchmark of those
> libraries. You can add validators to any of them. And when agents never fake
> completion, the gap disappears.
>
> Also inside: zero dependencies, ten named stop reasons for every run, and an
> experimental ant-colony-style router that learns which handoffs actually
> deliver.
>
> Code, benchmarks and the write-ups of the experiments that *didn't* work:
> [link]

---

## Sources

- LangGraph: [supervisor vs swarm patterns](https://focused.io/lab/multi-agent-orchestration-in-langgraph-supervisor-vs-swarm-tradeoffs-and-architecture) · [langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py) · [langgraph-supervisor reference](https://reference.langchain.com/python/langgraph-supervisor) · [GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)
- OpenAI Agents SDK: [handoffs](https://openai.github.io/openai-agents-python/handoffs/) · [running agents / max_turns](https://openai.github.io/openai-agents-python/running_agents/) · [orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)
- AutoGen: [teams](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/tutorial/teams.html) · [SelectorGroupChat](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/selector-group-chat.html) · [Swarm](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/swarm.html) · [GraphFlow](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/graph-flow.html) · [Magentic-One](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/magentic-one.html)
- CrewAI: [processes](https://docs.crewai.com/en/concepts/processes) · [hierarchical process](https://docs.crewai.com/how-to/hierarchical-process)
- Google ADK: [multi-agent patterns](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/) · [multi-agent systems guide](https://cloud.google.com/blog/topics/developers-practitioners/building-collaborative-ai-a-developers-guide-to-multi-agent-systems-with-adk)
- Microsoft: [Semantic Kernel agent orchestration](https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/agent-orchestration/) · [Agent Framework 1.0](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/)
- LlamaIndex: [multi-agent patterns](https://developers.llamaindex.ai/python/framework/understanding/agent/multi_agent/) · [AgentWorkflow](https://www.llamaindex.ai/blog/introducing-agentworkflow-a-powerful-system-for-building-ai-agent-systems)
- Strands Agents: [multi-agent patterns](https://strandsagents.com/docs/user-guide/concepts/multi-agent/multi-agent-patterns/)
