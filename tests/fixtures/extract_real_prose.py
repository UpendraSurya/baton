"""
Pull real agent output out of the ~/unlimited fork corpus into a checked-in
fixture. Run once; the JSON is committed so the suite is offline and free.

Why: a parser suite built only from clean hand-written JSON blocks passes while
the parser is useless on what models actually emit. See dev-notes:
feedback_synthetic_fixtures_too_clean.

READ-ONLY, and only ever from the SANDBOX db (~/.unlimited-os/state.db).
The canonical ~/.company-os/state.db is never opened.
"""
import json
import pathlib
import sqlite3
import sys

FORK_DB = pathlib.Path.home() / ".unlimited-os" / "state.db"
OUT = pathlib.Path(__file__).with_name("real_agent_prose.json")
LIMIT = 40


def main():
    if not FORK_DB.exists():
        print(f"fork corpus not found at {FORK_DB}", file=sys.stderr)
        return 1
    con = sqlite3.connect(f"file:{FORK_DB}?mode=ro", uri=True)
    rows = con.execute(
        "select node, summary from deliverables "
        "where summary is not null and length(summary) > 120 "
        "order by seq limit ?", (LIMIT,)).fetchall()
    con.close()
    samples = [{"agent": n, "text": s} for n, s in rows]
    OUT.write_text(json.dumps(samples, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(samples)} real agent samples to {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
