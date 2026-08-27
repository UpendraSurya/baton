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

import json

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
    # How much work this artifact carried WHEN IT WAS PRODUCED. Set once and
    # preserved through without_content(), because an artifact stops carrying
    # its content the moment it becomes history — and a gate judging the whole
    # set at the end would otherwise see only empty pointers and be unable to
    # tell a 6KB webhook from "# content shown above".
    content_chars: int = 0
    # Whether the content was cut to fit MAX_CONTENT_CHARS. The cut is correct —
    # this packet is re-sent every hop — but a truncated patch or config is not a
    # smaller one, it is a corrupt one, and a consumer that cannot tell reads the
    # `git apply` failure as an agent that cannot write a diff. Reported from
    # outside by migration-stress.
    truncated: bool = False

    def __post_init__(self) -> None:
        if not self.content_chars and self.content:
            object.__setattr__(self, "content_chars", len(self.content.strip()))
        if len(self.content) > MAX_CONTENT_CHARS:
            object.__setattr__(
                self, "content",
                self.content[:MAX_CONTENT_CHARS] + "\n… [truncated]")
            object.__setattr__(self, "truncated", True)

    def without_content(self) -> ArtifactRef:
        """The same reference, reduced to a pointer. Used when an artifact stops
        being this hop's deliverable and becomes history.

        `truncated` is carried across: losing the content must not also lose the
        fact that what it carried was already incomplete."""
        return ArtifactRef(self.path, self.description, self.preview,
                           content_chars=self.content_chars,
                           truncated=self.truncated)

    def to_dict(self) -> dict:
        return {"path": self.path, "description": self.description,
                "preview": self.preview, "content": self.content,
                "content_chars": self.content_chars,
                "truncated": self.truncated}

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
    # RATIFY only: one artifact path per acceptance criterion, in order. The
    # gate has to say WHICH work satisfies WHICH requirement, because "looks
    # good to me" is the failure mode a gate exists to prevent.
    coverage: tuple[str, ...] = ()
    # PROPOSE_DONE only. A worker that genuinely looked and found nothing —
    # "no such file", "zero matches", "no migration needed" — is a legitimate
    # completion with no artifact to attach, and is not the same claim as
    # "trust me, it's done" with evidence that does not exist. Both arrive with
    # empty `artifacts`; only this field tells them apart, and only THIS one is
    # allowed through with none. See baton.contract.validate_decision.
    nothing_found: bool = False

    def to_dict(self) -> dict:
        return {"decision": self.kind.value, "to": self.to, "goal": self.goal,
                "rationale": self.rationale, "summary": self.summary,
                "reason": self.reason, "coverage": list(self.coverage),
                "artifacts": [a.to_dict() for a in self.artifacts],
                "nothing_found": self.nothing_found}

    def render(self, *, preamble: str = "") -> str:
        """This decision as the text an agent would emit — the inverse of
        `parse_decision`, and round-trip safe with it.

        baton could read its own wire format and not write it, so everyone
        writing a deterministic agent — a gate that runs tests instead of asking
        a model, a scripted fixture, a replay harness — hand-rolled the fence
        and the JSON. Fifteen files in this repo and its two consumer projects
        did exactly that, which is fifteen independent chances to get the format
        wrong and no single place to fix it.

        Empty fields are omitted rather than sent as "": a HANDOFF carrying
        `"coverage": []` invites a reader to think coverage was considered.
        `nothing_found: False` is the default for every decision that never
        uses it and would otherwise clutter all three other verbs' wire form.
        """
        body = {k: v for k, v in self.to_dict().items()
                if v not in ("", [], None, False)}
        body["decision"] = self.kind.value          # never dropped, even if falsy
        head = f"{preamble.strip()}\n\n" if preamble.strip() else ""
        return head + "```handoff\n" + json.dumps(body, ensure_ascii=False) + "\n```"
