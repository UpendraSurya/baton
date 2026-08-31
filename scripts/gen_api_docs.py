#!/usr/bin/env python3
"""Generate docs/api.md from baton.__all__.

Generated, not written, for one reason: a hand-written reference drifts the day
someone adds a symbol, and the drift is invisible — the page still looks
complete. This walks `__all__`, so a new public name appears here or the check
in verify.sh fails.
"""
from __future__ import annotations

import dataclasses
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import baton  # noqa: E402

GROUPS = [
    ("Running a job", ["run", "Charter", "AgentSpec", "RunResult", "Dispatch",
                       "GATE", "WORKER", "worker", "gate"]),
    ("What agents send", ["Baton", "Decision", "Kind", "ArtifactRef",
                          "parse_decision", "validate_decision", "ParseFailure"]),
    ("Bounds and guards", ["Guards", "TERMINAL_REASONS", "BatonError",
                           "CharterInvalid", "IllegalTarget", "DispatchResult"]),
    ("Prompts", ["render_prompt", "render_routing_contract", "repair_nudge"]),
    ("Traces", ["MemoryTrace", "JsonlTrace", "TraceSink"]),
    ("Planning", ["estimate", "Estimate", "reachable_from"]),
    ("Reputation", ["Reputation", "AgentRecord", "from_traces", "from_tallies"]),
    ("Metadata", ["__version__"]),
]


def first_para(obj) -> str:
    # Only classes, routines and modules carry a docstring of their own.
    # inspect.getdoc falls back to the TYPE's docstring for anything else, so a
    # plain value like __version__ ('0.1.0') rendered CPython's documentation for
    # `str` into the reference — text that is an interpreter implementation
    # detail and can differ between the 3.10/3.11/3.12 lanes CI runs. A
    # generated file whose content depends on the interpreter cannot be checked
    # for drift: it matched on the author's 3.10 and was reported stale by every
    # CI lane on the first push.
    if not (inspect.isclass(obj) or inspect.isroutine(obj) or inspect.ismodule(obj)):
        return ""
    d = inspect.getdoc(obj) or ""
    return d.split("\n\n")[0].replace("\n", " ").strip() if d else ""


def sig(obj) -> str:
    try:
        return f"{obj.__name__}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        return getattr(obj, "__name__", str(obj))


def render() -> str:
    out = ["# API reference", "",
           f"Every name in `baton.__all__` ({len(baton.__all__)} of them). "
           "Generated from the source by `scripts/gen_api_docs.py` — if you add "
           "a public symbol and do not regenerate, `verify.sh` fails.", ""]
    seen = set()
    for title, names in GROUPS:
        rows = [n for n in names if n in baton.__all__]
        if not rows:
            continue
        seen |= set(rows)
        out += [f"## {title}", ""]
        for n in rows:
            obj = getattr(baton, n)
            out.append(f"### `{n}`")
            out.append("")
            if inspect.isclass(obj):
                if dataclasses.is_dataclass(obj):
                    out += ["```python"]
                    for f in dataclasses.fields(obj):
                        t = getattr(f.type, "__name__", str(f.type))
                        d = "" if f.default is dataclasses.MISSING else f" = {f.default!r}"
                        out.append(f"{f.name}: {t}{d}")
                    out += ["```", ""]
            elif callable(obj):
                out += ["```python", sig(obj), "```", ""]
            else:
                out += [f"`{obj!r}`", ""]
            p = first_para(obj)
            if p:
                out += [p, ""]
    # No `startswith("__")` filter here: `__version__` is in __all__ and is a
    # public symbol like any other. Excluding it made the reference silently
    # incomplete while the "is it complete" check passed — the first thing the
    # new docs gate caught, on the run that introduced it.
    missing = [n for n in baton.__all__ if n not in seen]
    if missing:
        out += ["## Ungrouped", "",
                "These are public but not yet placed in a section:", ""]
        out += [f"- `{n}` — {first_para(getattr(baton, n))[:90]}" for n in missing]
        out += [""]
    return "\n".join(out)


if __name__ == "__main__":
    dest = Path(__file__).resolve().parent.parent / "docs/api.md"
    dest.write_text(render(), encoding="utf-8")
    print(f"wrote {dest} ({len(render().splitlines())} lines, "
          f"{len(baton.__all__)} symbols)")
