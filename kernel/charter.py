"""
kernel.charter — the pre-flight constraint envelope.

This is what replaces the topology. A topology froze the ROUTE and, incidentally,
carried the budget cap, the security tier and the agent roster. Deleting the route
would have deleted those too, so they move here: bounds without edges.

acceptance_criteria are required and are written BEFORE the run, so the gate agent
cannot be talked into lowering the bar halfway through.
"""
from dataclasses import dataclass

from kernel.errors import CharterInvalid

TIERS = ("L", "M", "H")


@dataclass(frozen=True)
class Charter:
    brief: str
    entry_agent: str
    gate_agent: str
    agent_pool: frozenset
    acceptance_criteria: tuple
    budget_ceiling_usd: float = 5.0
    max_hops: int = 12
    security_tier: str = "M"
    max_rejects_per_agent: int = 2
    pressure_threshold: float = 0.8

    def validate(self):
        """Raise CharterInvalid unless this describes a runnable run."""
        if not self.brief.strip():
            raise CharterInvalid("brief is empty")
        if not self.agent_pool:
            raise CharterInvalid("agent_pool is empty — nobody can run")
        if self.entry_agent not in self.agent_pool:
            raise CharterInvalid(
                f"entry_agent {self.entry_agent!r} is not in agent_pool")
        if self.gate_agent not in self.agent_pool:
            raise CharterInvalid(
                f"gate_agent {self.gate_agent!r} is not in agent_pool")
        if not self.acceptance_criteria:
            raise CharterInvalid(
                "acceptance_criteria are required — the gate needs a bar set "
                "before the run, not after")
        if self.budget_ceiling_usd <= 0:
            raise CharterInvalid("budget_ceiling_usd must be > 0")
        if self.max_hops < 1:
            raise CharterInvalid("max_hops must be >= 1")
        if self.security_tier not in TIERS:
            raise CharterInvalid(f"security_tier must be one of {TIERS}")
        if self.max_rejects_per_agent < 1:
            raise CharterInvalid("max_rejects_per_agent must be >= 1")
        if not 0 < self.pressure_threshold <= 1:
            raise CharterInvalid("pressure_threshold must be in (0, 1]")

    def legal_moves_for(self, agent):
        """The agent's own whitelist, intersected with this run's pool, sorted.

        Sorted because an unstable order rewrites the prompt on every hop and
        quietly destroys prompt-cache hits."""
        return tuple(sorted(agent.can_hand_to & self.agent_pool))

    def to_dict(self):
        return {"brief": self.brief, "entry_agent": self.entry_agent,
                "gate_agent": self.gate_agent,
                "agent_pool": sorted(self.agent_pool),
                "acceptance_criteria": list(self.acceptance_criteria),
                "budget_ceiling_usd": self.budget_ceiling_usd,
                "max_hops": self.max_hops, "security_tier": self.security_tier,
                "max_rejects_per_agent": self.max_rejects_per_agent,
                "pressure_threshold": self.pressure_threshold}
