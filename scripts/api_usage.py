#!/usr/bin/env python3
"""How much of baton's public API does a consuming project actually use?

    python3 scripts/api_usage.py ~/tenderline/tenderline ~/migration-stress/migration_stress

Point it at the LIBRARY directory of each consumer (not the repo root — test and
fixture code would flatter the numbers).

This exists to be run twice: once before the user guide is written and once
after, on the same projects, so "the docs made baton more usable" is a measured
claim rather than an impression. Freeze the method or the comparison is mush.
"""
from __future__ import annotations

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import baton                                                      # noqa: E402
from baton.runtime import TERMINAL_REASONS, Guards                # noqa: E402
import dataclasses                                                # noqa: E402

PUBLIC = set(baton.__all__) - {"__version__"}
GUARDS = {f.name for f in dataclasses.fields(Guards)}


def sources(root: pathlib.Path):
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in str(p)]


def measure(root: pathlib.Path) -> dict:
    loc = importing = refs = 0
    symbols, reasons, guards = set(), set(), set()

    for path in sources(root):
        text = path.read_text(encoding="utf-8")
        loc += len(text.splitlines())
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        local = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "baton":
                for alias in node.names:
                    local.add(alias.asname or alias.name)
                    symbols.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "baton":
                        local.add(alias.asname or alias.name)
                        symbols.add(alias.name)
        if local:
            importing += 1
        refs += sum(1 for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and n.id in local)

        # Terminal reasons and guard names are compared as string literals: a
        # project that never names `hops_exhausted` is not handling it.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in TERMINAL_REASONS:
                    reasons.add(node.value)
            if isinstance(node, ast.keyword) and node.arg in GUARDS:
                guards.add(node.arg)

    return {"loc": loc, "files": len(sources(root)), "importing": importing,
            "refs": refs, "symbols": symbols, "reasons": reasons, "guards": guards}


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    totals = {"loc": 0, "refs": 0, "symbols": set(), "reasons": set(), "guards": set()}
    for arg in argv:
        root = pathlib.Path(arg).expanduser()
        m = measure(root)
        pct = 100 * m["refs"] / m["loc"] if m["loc"] else 0
        print(f"\n{root}")
        print(f"  library LOC                 {m['loc']}")
        print(f"  files importing baton       {m['importing']}/{m['files']}")
        print(f"  baton reference sites       {m['refs']}  ({pct:.1f}% of lines)")
        print(f"  public symbols used         {len(m['symbols'] & PUBLIC)}/{len(PUBLIC)}"
              f"  {sorted(m['symbols'] & PUBLIC)}")
        print(f"  terminal reasons handled    {len(m['reasons'])}/{len(TERMINAL_REASONS)}"
              f"  {sorted(m['reasons'])}")
        print(f"  guards configured           {len(m['guards'])}/{len(GUARDS)}"
              f"  {sorted(m['guards'])}")
        for key in ("loc", "refs"):
            totals[key] += m[key]
        for key in ("symbols", "reasons", "guards"):
            totals[key] |= m[key]

    if len(argv) > 1:
        pct = 100 * totals["refs"] / totals["loc"] if totals["loc"] else 0
        print(f"\nCOMBINED  {totals['refs']} reference sites in {totals['loc']} "
              f"library lines ({pct:.1f}%)")
        print(f"          {len(totals['symbols'] & PUBLIC)}/{len(PUBLIC)} public symbols, "
              f"{len(totals['reasons'])}/{len(TERMINAL_REASONS)} terminal reasons, "
              f"{len(totals['guards'])}/{len(GUARDS)} guards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
