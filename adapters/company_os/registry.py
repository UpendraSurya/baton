"""
adapters.company_os.registry — Company OS personas -> kernel AgentSpecs.

The 49-agent problem: if everyone can hand to everyone, every hop is a 49-way
choice and routing quality collapses. registry/agent_registry.json already groups
agents into the six vault layers, so the whitelist rule is simply:

    own layer + the layer below + the gate

which yields a sane whitelist per agent without hand-authoring 49 lists.

This module only READS files. It never imports Company OS code and never opens a
database, so it is safe to exercise in the free tier-1 suite.
"""
import json
import os
import pathlib

from kernel.agent import GATE, WORKER, AgentSpec

# vault/layers/layer-1..6, in order. "the layer below" means the next one here.
LAYERS = ("executive", "operations", "delivery", "quality", "business",
          "security_legal")

# see state.CANONICAL_HOME — this is the repo twin of that constant
CANONICAL_REPO = pathlib.Path.home() / "company-os"
_SANDBOX = pathlib.Path.home() / "unlimited" / "company-os"

# personas are flat in agents/, or in one of these subdirectories
_PERSONA_SUBDIRS = ("", "security", "delivery", "business")


def repo_path():
    """Where the Company OS checkout lives. The sandbox fork by default.

    The canonical checkout is refused: its state DB holds 66 irreplaceable runs
    and development does not happen against it.
    """
    raw = os.environ.get("BATON_COMPANY_OS_REPO")
    path = pathlib.Path(raw).expanduser() if raw else _SANDBOX
    if path == CANONICAL_REPO and os.environ.get("BATON_ALLOW_CANONICAL") != "1":
        raise RuntimeError(
            f"refusing to use the canonical checkout at {path}; build in "
            f"{_SANDBOX} (set BATON_ALLOW_CANONICAL=1 to override deliberately)")
    return path


def load_registry(repo=None):
    repo = pathlib.Path(repo) if repo else repo_path()
    return json.loads((repo / "registry" / "agent_registry.json").read_text())


def _members(reg, layer):
    return tuple(n for n in reg.get(layer, {}) if not n.startswith("_"))


def layer_of(name, reg):
    for layer in LAYERS:
        if name in _members(reg, layer):
            return layer
    return ""


def can_hand_to(name, reg, gate_agent="gate_agent"):
    """own layer + the layer below + the gate, minus the agent itself."""
    layer = layer_of(name, reg)
    if not layer:
        return frozenset({gate_agent})
    i = LAYERS.index(layer)
    reachable = set(_members(reg, layer))
    if i + 1 < len(LAYERS):
        reachable |= set(_members(reg, LAYERS[i + 1]))
    reachable.add(gate_agent)
    reachable.discard(name)
    return frozenset(reachable)


def persona_text(name, repo=None):
    repo = pathlib.Path(repo) if repo else repo_path()
    for sub in _PERSONA_SUBDIRS:
        base = repo / "agents" / sub if sub else repo / "agents"
        path = base / f"{name}.md"
        if path.exists():
            return path.read_text()
    return f"# {name}\n(no persona file)"


def load_agents(repo=None, gate_agent="gate_agent", pool=None):
    """Every registered agent as an AgentSpec. pool restricts both the roster and
    every whitelist, so a small run cannot route to somebody it did not staff."""
    repo = pathlib.Path(repo) if repo else repo_path()
    reg = load_registry(repo)
    names = [n for layer in LAYERS for n in _members(reg, layer)]
    if pool is not None:
        names = [n for n in names if n in pool]

    agents = {}
    for name in names:
        moves = can_hand_to(name, reg, gate_agent)
        if pool is not None:
            moves &= frozenset(pool)
        entry = reg[layer_of(name, reg)][name]
        agents[name] = AgentSpec(
            name=name,
            instructions=persona_text(name, repo),
            can_hand_to=moves,
            role=GATE if name == gate_agent else WORKER,
            model_id=entry.get("model", "") if isinstance(entry, dict) else "")
    return agents
