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
from baton.agent import GATE, WORKER, AgentSpec
from baton.charter import Charter
from baton.errors import (BatonError, CharterInvalid, IllegalTarget,
                          ParseFailure, RoleViolation, TraceCorrupt)
from baton.packet import ArtifactRef, Baton, Decision, Kind
from baton.runtime import (TERMINAL_REASONS, DispatchResult, Guards, RunResult,
                           run)
from baton.trace import MemoryTrace, Trace

__version__ = "0.1.0"

__all__ = [
    # the five nouns
    "AgentSpec", "Baton", "Charter", "Trace", "MemoryTrace",
    # running one
    "run", "RunResult", "DispatchResult", "Guards", "TERMINAL_REASONS",
    # the packet's parts
    "ArtifactRef", "Decision", "Kind", "GATE", "WORKER",
    # failures
    "BatonError", "CharterInvalid", "IllegalTarget", "ParseFailure",
    "RoleViolation", "TraceCorrupt",
    "__version__",
]
