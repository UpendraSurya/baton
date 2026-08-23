"""
baton.plan — what a run will cost and how it can end, BEFORE spending anything.

The budget ceiling is enforced between hops, not inside one: the runtime cannot
know what a call costs until the call returns. So a ceiling smaller than a couple
of hops is not a ceiling, it is a hope. An adversarial sweep (tests/test_adversarial.py)
measured overshoots up to 8x on charters whose ceiling could not cover a single call.

The fix is not a tighter runtime check — it is refusing to start a run whose budget
was never survivable. This module is the pre-flight: it reports worst-case spend,
worst-case hops, unreachable agents and dead ends, all offline and free.

    from baton import plan
    est = plan.estimate(charter, agents, usd_per_call=0.02)
    if not est.feasible:
        print(est.problems)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from baton.agent import AgentSpec
    from baton.charter import Charter
from baton.errors import CharterInvalid

# A hop can spend two calls: the dispatch plus one repair retry.
CALLS_PER_HOP: int = 2
# The hop cap may be exceeded by one forced gate verdict.
FORCED_GATE_HOPS: int = 1


@dataclass
class Estimate:
    worst_case_hops: int
    worst_case_calls: int
    worst_case_usd: float
    ceiling_usd: float
    feasible: bool
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    unreachable: tuple[str, ...] = ()
    dead_ends: tuple[str, ...] = ()

    def raise_if_infeasible(self) -> None:
        """Raise CharterInvalid if this charter was never survivable.

        The reporting form (`feasible`, `problems`) is for a human reading a
        plan. This is the form that stops a run. Until 2026-08-23 only the
        former existed while the module docstring promised the latter, so the
        promise was kept by whichever caller happened to read the flag — which
        is to say, by nobody.
        """
        if self.problems:
            raise CharterInvalid(
                "this charter cannot survive its own budget: "
                + "; ".join(self.problems))

    def render(self) -> str:
        lines = [
            f"worst case : {self.worst_case_hops} hops, "
            f"{self.worst_case_calls} calls, ${self.worst_case_usd:.2f}",
            f"ceiling    : ${self.ceiling_usd:.2f}",
            f"feasible   : {'yes' if self.feasible else 'NO'}",
        ]
        for p in self.problems:
            lines.append(f"  problem  {p}")
        for w in self.warnings:
            lines.append(f"  warning  {w}")
        return "\n".join(lines)


def reachable_from(entry: str, agents: Mapping[str, AgentSpec],
                   charter: Charter) -> set[str]:
    """Every agent the run can actually arrive at, following whitelists."""
    seen, frontier = {entry}, [entry]
    while frontier:
        name = frontier.pop()
        spec = agents.get(name)
        if spec is None:
            continue
        for nxt in spec.can_hand_to & charter.agent_pool:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    # the runtime can always route to the gate, whatever the whitelists say
    seen.add(charter.gate_agent)
    return seen


def estimate(charter: Charter, agents: Mapping[str, AgentSpec],
             usd_per_call: float = 0.0) -> Estimate:
    """Worst-case cost and shape of a run, before it starts.

    usd_per_call is the caller's expected cost of ONE model call. Pass the most
    expensive model in the pool — this is a ceiling check, not a forecast.
    """
    charter.validate()

    hops = charter.max_hops + FORCED_GATE_HOPS
    calls = hops * CALLS_PER_HOP
    worst_usd = calls * usd_per_call

    problems, warnings = [], []

    reach = reachable_from(charter.entry_agent, agents, charter)
    unreachable = tuple(sorted(set(charter.agent_pool) - reach))
    if unreachable:
        warnings.append(
            f"unreachable from {charter.entry_agent}: {', '.join(unreachable)} "
            "— staffed but the run can never arrive there")

    dead_ends = tuple(sorted(
        name for name in charter.agent_pool
        if name != charter.gate_agent
        and not (agents[name].can_hand_to & charter.agent_pool)))
    if dead_ends:
        warnings.append(
            f"dead ends (no legal move but the gate): {', '.join(dead_ends)}")

    if usd_per_call > 0:
        if usd_per_call > charter.budget_ceiling_usd:
            problems.append(
                f"a single call (${usd_per_call:.4f}) costs more than the whole "
                f"ceiling (${charter.budget_ceiling_usd:.2f}) — the ceiling cannot "
                "be enforced, it is breached on hop 1")
        elif usd_per_call * CALLS_PER_HOP > charter.budget_ceiling_usd:
            problems.append(
                f"one hop with a repair retry (${usd_per_call * CALLS_PER_HOP:.4f}) "
                f"exceeds the ceiling (${charter.budget_ceiling_usd:.2f}) — expect "
                "overshoot, since the ceiling is checked between hops")
        elif worst_usd > charter.budget_ceiling_usd:
            warnings.append(
                f"worst case ${worst_usd:.2f} exceeds the ceiling "
                f"${charter.budget_ceiling_usd:.2f}; the run will halt early on "
                "budget rather than finish")

    if charter.gate_agent not in reach:
        warnings.append("the gate is not on any whitelist; the runtime will still "
                        "force-route to it, but no agent can choose it")

    return Estimate(worst_case_hops=hops, worst_case_calls=calls,
                    worst_case_usd=worst_usd,
                    ceiling_usd=charter.budget_ceiling_usd,
                    feasible=not problems,
                    problems=tuple(problems), warnings=tuple(warnings),
                    unreachable=unreachable, dead_ends=dead_ends)
