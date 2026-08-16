"""
kernel.agent — what baton knows about an agent, which is deliberately little.

An AgentSpec carries no execution machinery: no model client, no subprocess, no
tools. Running it is the caller's job (see runtime.run's `dispatch` argument).
That is the whole reason this package has no dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

WORKER: Literal["worker"] = "worker"
GATE: Literal["gate"] = "gate"
ROLES: tuple[str, ...] = (WORKER, GATE)

Role = Literal["worker", "gate"]


@dataclass(frozen=True)
class AgentSpec:
    """One agent, as the runtime sees it.

    Args:
        name: unique identifier; the key the runtime routes on.
        instructions: the persona/system prompt, used verbatim.
        can_hand_to: agents this one may hand to, before the charter's pool is
            intersected in. Any iterable; coerced to a frozenset.
        role: ``"worker"`` may HANDOFF/PROPOSE_DONE; ``"gate"`` may RATIFY/REJECT
            and is the only role that can end a run.
        model_id: advisory only — the dispatch callable decides what to call.
    """

    name: str
    instructions: str
    can_hand_to: frozenset[str] = frozenset()
    role: Role = WORKER
    model_id: str = ""

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")
        if not isinstance(self.can_hand_to, frozenset):
            object.__setattr__(self, "can_hand_to", frozenset(self.can_hand_to))

    @property
    def is_gate(self) -> bool:
        return self.role == GATE


def worker(name: str, instructions: str,
           can_hand_to: Iterable[str] = ()) -> AgentSpec:
    """Convenience constructor for a worker agent."""
    return AgentSpec(name=name, instructions=instructions,
                     can_hand_to=frozenset(can_hand_to), role=WORKER)


def gate(name: str, instructions: str,
         can_hand_to: Iterable[str] = ()) -> AgentSpec:
    """Convenience constructor for the gate agent — the only role that can end a run."""
    return AgentSpec(name=name, instructions=instructions,
                     can_hand_to=frozenset(can_hand_to), role=GATE)
