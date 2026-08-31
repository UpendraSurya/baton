"""
baton.charter — the pre-flight constraint envelope.

This is what replaces the topology. A topology froze the ROUTE and, incidentally,
carried the budget cap, the security tier and the agent roster. Deleting the route
would have deleted those too, so they move here: bounds without edges.

acceptance_criteria are required and are written BEFORE the run, so the gate agent
cannot be talked into lowering the bar halfway through.

Every field here is an enforced bound EXCEPT security_tier, which is advisory: it
is validated and shown to the model, and nothing in baton acts on it. See the
comment on that field.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baton.errors import CharterInvalid

if TYPE_CHECKING:                     # avoids a cycle; agent does not import charter
    from baton.agent import AgentSpec

TIERS: tuple[str, ...] = ("L", "M", "H")


@dataclass(frozen=True)
class Charter:
    brief: str
    entry_agent: str
    gate_agent: str
    agent_pool: frozenset[str]
    acceptance_criteria: tuple[str, ...]
    budget_ceiling_usd: float = 5.0
    max_hops: int = 12
    # Killer 5: a run that never comes back. Two bounds, because they catch
    # different things — the deadline covers slow accumulation across hops, and
    # max_dispatch_seconds covers ONE hop that never returns, which a deadline
    # checked between hops can never notice.
    max_wall_seconds: float = 3600.0
    max_dispatch_seconds: float = 300.0
    # ADVISORY, NOT ENFORCED. Validated against TIERS, serialised, and printed
    # into every agent prompt — and nothing else in this library reads it. No
    # guard, budget, whitelist or terminal reason branches on it. It is here
    # because the host application this was written against classifies its own
    # agents that way and the model is told which tier it is under. The bounds
    # that are actually enforced are agent_pool, AgentSpec.can_hand_to,
    # budget_ceiling_usd, max_hops, max_wall_seconds and max_dispatch_seconds.
    # tests/test_security_tier_is_advisory.py fails if this stops being true in
    # either direction.
    security_tier: str = "M"
    max_rejects_per_agent: int = 2
    pressure_threshold: float = 0.8

    def validate(self) -> None:
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
        if self.max_wall_seconds <= 0:
            raise CharterInvalid("max_wall_seconds must be > 0")
        if self.max_dispatch_seconds <= 0:
            raise CharterInvalid("max_dispatch_seconds must be > 0")
        if self.security_tier not in TIERS:
            raise CharterInvalid(f"security_tier must be one of {TIERS}")
        if self.max_rejects_per_agent < 1:
            raise CharterInvalid("max_rejects_per_agent must be >= 1")
        if not 0 < self.pressure_threshold <= 1:
            raise CharterInvalid("pressure_threshold must be in (0, 1]")

    def legal_moves_for(self, agent: AgentSpec) -> tuple[str, ...]:
        """The agent's own whitelist, intersected with this run's pool, sorted.

        Sorted because an unstable order rewrites the prompt on every hop and
        quietly destroys prompt-cache hits."""
        return tuple(sorted(agent.can_hand_to & self.agent_pool))

    def to_dict(self) -> dict:
        return {"brief": self.brief, "entry_agent": self.entry_agent,
                "gate_agent": self.gate_agent,
                "agent_pool": sorted(self.agent_pool),
                "acceptance_criteria": list(self.acceptance_criteria),
                "budget_ceiling_usd": self.budget_ceiling_usd,
                "max_hops": self.max_hops,
                "max_wall_seconds": self.max_wall_seconds,
                "max_dispatch_seconds": self.max_dispatch_seconds, "security_tier": self.security_tier,
                "max_rejects_per_agent": self.max_rejects_per_agent,
                "pressure_threshold": self.pressure_threshold}
