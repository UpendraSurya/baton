"""
baton.packet — the packet that travels between agents, and the decision that
mints the next one.

Artifacts carry a PATH, a one-line description, the producing agent's own 2-3
line preview, and optionally the CONTENT itself.

The original design carried paths only, on the assumption that every agent has
file tools scoped to a shared workspace and can open anything it is pointed at.
That holds inside a host application; it is false for a library user whose agents
share no filesystem. Live evidence (2026-08-16): with paths only, a worker sent
PROPOSE_DONE naming a file it had never written, and the gate ratified the
worker's *claim* about a deliverable that did not exist anywhere.

So content may travel — but only for the freshest artifacts. `runtime._merge_artifacts`
strips content from carried artifacts and keeps it only on ones produced this hop,
which bounds per-hop cost by one deliverable instead of by the whole history.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    HANDOFF = "HANDOFF"
    PROPOSE_DONE = "PROPOSE_DONE"
    RATIFY = "RATIFY"
    REJECT = "REJECT"


# One deliverable's worth. Past this the packet stops being a packet.
MAX_CONTENT_CHARS: int = 8000


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    description: str = ""
    preview: str = ""
    content: str = ""          # the work product itself, when there is no shared disk

    def __post_init__(self) -> None:
        if len(self.content) > MAX_CONTENT_CHARS:
            object.__setattr__(
                self, "content",
                self.content[:MAX_CONTENT_CHARS] + "\n… [truncated]")

    def without_content(self) -> ArtifactRef:
        """The same reference, reduced to a pointer. Used when an artifact stops
        being this hop's deliverable and becomes history."""
        return ArtifactRef(self.path, self.description, self.preview)

    def to_dict(self) -> dict:
        return {"path": self.path, "description": self.description,
                "preview": self.preview, "content": self.content}

    def render(self) -> str:
        head = f"- `{self.path}`"
        if self.description:
            head += f" — {self.description}"
        if self.preview:
            body = "\n".join("    " + ln for ln in self.preview.strip().splitlines())
            head += "\n" + body
        if self.content:
            head += ("\n\n  <<<CONTENT of " + self.path + ">>>\n"
                     + self.content + "\n  <<<END " + self.path + ">>>")
        return head


@dataclass(frozen=True)
class Baton:
    trace_id: str
    hop: int
    from_agent: str
    to_agent: str
    goal: str
    rationale: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()
    open_questions: tuple[str, ...] = ()
    stall_notice: str = ""
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id, "hop": self.hop,
            "from_agent": self.from_agent, "to_agent": self.to_agent,
            "goal": self.goal, "rationale": self.rationale,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "open_questions": list(self.open_questions),
            "stall_notice": self.stall_notice, "flags": list(self.flags),
        }

    def render(self) -> str:
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
    artifacts: tuple[ArtifactRef, ...] = ()

    def to_dict(self) -> dict:
        return {"decision": self.kind.value, "to": self.to, "goal": self.goal,
                "rationale": self.rationale, "summary": self.summary,
                "reason": self.reason,
                "artifacts": [a.to_dict() for a in self.artifacts]}
