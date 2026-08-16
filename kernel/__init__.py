"""
baton — a small execution kernel for multi-agent systems in which the agents
choose who runs next.

Public surface is deliberately narrow: build a Charter, hand run() a dict of
AgentSpecs and a dispatch callable, read the RunResult.
"""
from kernel.agent import GATE, WORKER, AgentSpec
from kernel.baton import ArtifactRef, Baton, Decision, Kind
from kernel.errors import (BatonError, CharterInvalid, IllegalTarget,
                           ParseFailure, RoleViolation, TraceCorrupt)

__all__ = ["AgentSpec", "GATE", "WORKER", "ArtifactRef", "Baton", "Decision",
           "Kind", "BatonError", "CharterInvalid", "IllegalTarget",
           "ParseFailure", "RoleViolation", "TraceCorrupt"]
