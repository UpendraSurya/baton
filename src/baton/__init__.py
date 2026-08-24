"""
baton — an execution kernel for multi-agent systems where the agents choose who
runs next.

Instead of a pre-baked graph, each agent ends its output with one fenced
`handoff` block naming the next agent. This library is the runtime around that
decision: legal-move enforcement, a budget ceiling, a hop cap, a ping-pong
detector, and a gate agent that is the only role allowed to end a run.

    from baton import AgentSpec, Charter, run
    from baton.providers import gemini

    agents = {
        "writer": AgentSpec("writer", "You draft copy.", {"editor", "gate"}),
        "editor": AgentSpec("editor", "You tighten copy.", {"writer", "gate"}),
        "gate":   AgentSpec("gate", "You verify against the criteria.",
                            {"writer", "editor"}, role="gate"),
    }
    charter = Charter(
        brief="Write a launch announcement",
        entry_agent="writer", gate_agent="gate",
        agent_pool=frozenset(agents),
        acceptance_criteria=("under 200 words", "no marketing cliches"),
        budget_ceiling_usd=0.50, max_hops=8,
    )

    result = run(charter, agents, gemini.provider())
    print(result.terminal_reason, result.path, result.spend_usd)

`run` always terminates for exactly one reason from `TERMINAL_REASONS`. There is
no code path that returns "it just stopped".

Zero dependencies, and it stays that way: providers talk HTTP through urllib and
the test suite fails the build if anything imports a vendor SDK.
"""
from baton.agent import GATE, WORKER, AgentSpec, gate, worker
from baton.charter import Charter
from baton.contract import (parse_decision, render_prompt,
                            render_routing_contract, repair_nudge,
                            validate_decision)
from baton.errors import (BatonError, CharterInvalid, IllegalTarget,
                          ParseFailure, RoleViolation, TraceCorrupt)
from baton import plan, reputation
from baton.packet import ArtifactRef, Baton, Decision, Kind
from baton.plan import Estimate, estimate, reachable_from
from baton.reputation import AgentRecord, Reputation, from_tallies, from_traces
from baton.runtime import (TERMINAL_REASONS, Dispatch, DispatchResult, Guards,
                           RunResult, run)
from baton.trace import MemoryTrace, Trace, TraceSink

__version__ = "0.1.0"

__all__ = [
    # the five nouns
    "AgentSpec", "Baton", "Charter", "Trace", "MemoryTrace",
    # running one
    "run", "RunResult", "DispatchResult", "Guards", "TERMINAL_REASONS",
    # the seam between baton and any model, framework or remote service.
    # Exported because two consumer projects each had to annotate their dispatch
    # `Callable` or `object` while naming `Dispatch` in the docstring beside it.
    "Dispatch",
    # the packet's parts
    "ArtifactRef", "Decision", "Kind", "GATE", "WORKER",
    # building an agent without spelling out role=
    "worker", "gate",
    # the wire format, both directions. `parse_decision` reads what a model
    # emitted; `Decision.render()` writes it. Anyone building a deterministic
    # agent — a gate that runs tests rather than asking a model — needs both.
    "render_prompt", "render_routing_contract", "parse_decision",
    "validate_decision", "repair_nudge",
    # the extension point for keeping a run's record somewhere of your choosing
    "TraceSink",
    # pre-flight
    "plan", "estimate", "reachable_from", "Estimate",
    # evidence about the agents themselves
    "reputation", "Reputation", "AgentRecord", "from_traces", "from_tallies",
    # failures
    "BatonError", "CharterInvalid", "IllegalTarget", "ParseFailure",
    "RoleViolation", "TraceCorrupt",
    "__version__",
]
