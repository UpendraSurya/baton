"""
kernel.baton — the packet that travels between agents, and the decision that
mints the next one.

Artifacts carry a PATH, a one-line description and the producing agent's own
2-3 line preview — never file contents. Agents already have file tools scoped to
the workspace, so the packet only needs enough for the receiver to decide whether
opening the file is worth it. That is what keeps per-hop cost flat instead of
growing with every earlier turn.
"""
from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    HANDOFF = "HANDOFF"
    PROPOSE_DONE = "PROPOSE_DONE"
    RATIFY = "RATIFY"
    REJECT = "REJECT"


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    description: str = ""
    preview: str = ""

    def to_dict(self):
        return {"path": self.path, "description": self.description,
                "preview": self.preview}

    def render(self):
        head = f"- `{self.path}`"
        if self.description:
            head += f" — {self.description}"
        if self.preview:
            body = "\n".join("    " + ln for ln in self.preview.strip().splitlines())
            head += "\n" + body
        return head


@dataclass(frozen=True)
class Baton:
    trace_id: str
    hop: int
    from_agent: str
    to_agent: str
    goal: str
    rationale: str = ""
    artifacts: tuple = ()
    open_questions: tuple = ()
    stall_notice: str = ""
    flags: tuple = ()

    def to_dict(self):
        return {
            "trace_id": self.trace_id, "hop": self.hop,
            "from_agent": self.from_agent, "to_agent": self.to_agent,
            "goal": self.goal, "rationale": self.rationale,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "open_questions": list(self.open_questions),
            "stall_notice": self.stall_notice, "flags": list(self.flags),
        }

    def render(self):
        """The baton as the receiving agent sees it. Empty sections are omitted —
        every line here is re-sent on every hop."""
        out = [f"## Your baton (hop {self.hop}, from {self.from_agent})",
               "", f"**Goal:** {self.goal}"]
        if self.rationale:
            out += [f"**Why you:** {self.rationale}"]
        if self.artifacts:
            out += ["", "**Artifacts available to you** (open them if useful):"]
            out += [a.render() for a in self.artifacts]
        if self.open_questions:
            out += ["", "**Open questions:**"]
            out += [f"- {q}" for q in self.open_questions]
        if self.stall_notice:
            out += ["", f"> **Stall notice:** {self.stall_notice}"]
        if self.flags:
            out += ["", f"> **Flags:** {', '.join(self.flags)}"]
        return "\n".join(out)


@dataclass(frozen=True)
class Decision:
    kind: Kind
    to: str = ""
    goal: str = ""
    rationale: str = ""
    summary: str = ""
    reason: str = ""
    artifacts: tuple = ()

    def to_dict(self):
        return {"decision": self.kind.value, "to": self.to, "goal": self.goal,
                "rationale": self.rationale, "summary": self.summary,
                "reason": self.reason,
                "artifacts": [a.to_dict() for a in self.artifacts]}
