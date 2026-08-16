"""
adapters.company_os.charter — where the constraint envelope comes from now that
topologies are gone.

The topology library carried three things that were never about flow: a cost
estimate, a security tier and an agent roster. Those TABLES are harvested here
and the EDGES are thrown away. Constraints survive; edges don't.

Acceptance criteria are the caller's job — in Company OS they come from a cheap
pre-flight pass, precisely so the gate cannot be talked into lowering the bar
afterwards. This module refuses to invent them.
"""
import json
import pathlib
import re

from kernel.charter import Charter

from adapters.company_os.registry import LAYERS, load_registry, repo_path

DEFAULT_BUDGET_USD = 5.0
DEFAULT_TIER = "M"
# a hop is roughly an agent, so cap it near the old topology's node count
_HOPS_PER_NODE = 1.5
_BUDGET_MULTIPLIER = {"low": 0.5, "mid": 1.0, "high": 1.5}


def _library(repo=None):
    repo = pathlib.Path(repo) if repo else repo_path()
    return json.loads((repo / "registry" / "topology_library.json").read_text())


def _high_estimate(text):
    """'12-30' -> 30.0; '3-8' -> 8.0; '~0' -> 0.0. The ceiling, not the hope."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(text))]
    return max(nums) if nums else DEFAULT_BUDGET_USD


def priors(project_type, repo=None):
    """Budget ceiling, security tier and node list harvested from the tables."""
    lib = _library(repo)
    base = lib.get("base_topologies", {}).get(project_type, {})
    tier = DEFAULT_TIER
    for name, spec in lib.get("security_preflight", {}).get("tiers", {}).items():
        if project_type in str(spec.get("when", "")):
            tier = name.split("_")[0]
            break
    return {"budget_ceiling_usd": _high_estimate(
                base.get("est_cost_usd", DEFAULT_BUDGET_USD)) or DEFAULT_BUDGET_USD,
            "security_tier": tier,
            "est_days": base.get("est_days", 1),
            "nodes": tuple(base.get("nodes", []))}


def charter_for(brief, *, project_type="maintenance_bugfix", budget="mid",
                pool=None, acceptance_criteria=(), repo=None, max_hops=None,
                budget_ceiling_usd=None, entry_agent="ceo",
                gate_agent="gate_agent"):
    """Build a Charter. Human overrides always win."""
    if not acceptance_criteria:
        raise ValueError(
            "acceptance_criteria are required and must be written BEFORE the "
            "run — the gate judges against them and nothing else")

    p = priors(project_type, repo)
    reg = load_registry(repo)
    known = {n for layer in LAYERS for n in reg.get(layer, {})
             if not n.startswith("_")}

    if pool is None:
        pool = set(p["nodes"]) & known
    pool = set(pool) & known
    pool.update({entry_agent, gate_agent})

    hops = max_hops or max(4, int(len(pool) * _HOPS_PER_NODE))
    ceiling = budget_ceiling_usd or p["budget_ceiling_usd"]
    if budget_ceiling_usd is None:
        ceiling *= _BUDGET_MULTIPLIER.get(budget, 1.0)

    return Charter(brief=brief, entry_agent=entry_agent, gate_agent=gate_agent,
                   agent_pool=frozenset(pool),
                   acceptance_criteria=tuple(acceptance_criteria),
                   budget_ceiling_usd=round(ceiling, 2), max_hops=hops,
                   security_tier=p["security_tier"])
