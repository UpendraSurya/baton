"""
adapters.company_os.state — a finished run, written into a Company OS state DB.

Only ever a SANDBOX database. The canonical home holds 66 irreplaceable runs
worth $53.98 of real history and this module will not open it.

The schema is Company OS's own (registry/state_schema.sql), so a baton run shows
up in the existing scoreboard and cost tooling with no changes there.
"""
import json
import pathlib
import sqlite3
import time

# The one place in the source that names the canonical home: the guard
# that refuses it. Everything else imports this, so verify.sh can insist
# no other file constructs the path at all.
CANONICAL_HOME = pathlib.Path.home() / ".company-os"
_DEFAULT_HOME = pathlib.Path.home() / ".unlimited-os"

_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL, node TEXT, type TEXT NOT NULL,
    payload    TEXT, ts TEXT NOT NULL)
"""
_COST_DDL = """
CREATE TABLE IF NOT EXISTS cost_ledger (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL, node TEXT NOT NULL, model_id TEXT NOT NULL,
    in_tokens  INTEGER, out_tokens INTEGER, usd REAL, ts TEXT NOT NULL)
"""


def _home(home=None):
    path = pathlib.Path(home).expanduser() if home else _DEFAULT_HOME
    # resolve() is non-strict, so this also catches a symlink pointing at
    # canonical and a path that does not exist yet.
    if path.resolve() == CANONICAL_HOME.resolve():
        raise RuntimeError(
            f"refusing to write to the canonical state DB at {path} — "
            "66 irreplaceable runs live there; use ~/.unlimited-os")
    return path


def persist(result, project_id, home=None):
    """Write a RunResult's trace into state.db. Returns the number of rows."""
    base = _home(home)
    base.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(base / "state.db")
    con.execute(_EVENTS_DDL)
    con.execute(_COST_DDL)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows = 0

    for rec in (result.trace.records() if result.trace else []):
        if rec.get("event") == "dispatch" and rec.get("cost_usd"):
            con.execute(
                "insert into cost_ledger (project_id, node, model_id, in_tokens,"
                " out_tokens, usd, ts) values (?,?,?,?,?,?,?)",
                # NOT "baton" — the router is not a model, and a row labelled
                # with it looks attributed while being unattributable.
                (project_id, rec.get("agent", ""), rec.get("model_id") or "unknown",
                 rec.get("in_tokens", 0), rec.get("out_tokens", 0),
                 rec.get("cost_usd", 0.0), rec.get("ts", now)))
            rows += 1
        con.execute(
            "insert into events (project_id, node, type, payload, ts)"
            " values (?,?,?,?,?)",
            (project_id, rec.get("agent"), f"baton_{rec.get('event')}",
             json.dumps(rec), rec.get("ts", now)))
        rows += 1

    con.execute("insert into events (project_id, node, type, payload, ts)"
                " values (?,?,?,?,?)",
                (project_id, result.path[-1] if result.path else None,
                 "baton_terminal",
                 json.dumps({"terminal_reason": result.terminal_reason,
                             "hops": result.hops, "spend_usd": result.spend_usd,
                             "path": list(result.path),
                             "gate_summary": result.gate_summary}), now))
    rows += 1
    con.commit()
    con.close()
    return rows
