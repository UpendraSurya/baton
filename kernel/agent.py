"""
kernel.agent — what the kernel knows about an agent, which is deliberately little.

An AgentSpec carries no execution machinery: no model client, no subprocess, no
tools. Running it is the caller's job (see runtime.run's `dispatch` argument).
That is the whole reason kernel/ has no dependencies.
"""
from dataclasses import dataclass

WORKER = "worker"
GATE = "gate"
ROLES = (WORKER, GATE)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    instructions: str                       # the persona text, verbatim
    can_hand_to: frozenset = frozenset()    # legal next agents, before charter intersection
    role: str = WORKER                      # worker | gate
    model_id: str = ""                      # advisory; the dispatcher decides

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")
        if not isinstance(self.can_hand_to, frozenset):
            object.__setattr__(self, "can_hand_to", frozenset(self.can_hand_to))

    @property
    def is_gate(self):
        return self.role == GATE
