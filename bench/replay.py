#!/usr/bin/env python3
"""
bench/replay.py — TIER 2. Costs real money. Never run from verify.sh.

Replays recorded briefs from the ~/unlimited fork corpus through the dynamic
kernel and prints cost + outcome beside what the static topology actually did.

    python3 bench/replay.py --list                 # what is available, free
    python3 bench/replay.py --dry-run -n 5         # full wiring, stub model, free
    python3 bench/replay.py --confirm-spend -n 5   # REAL calls, real money

STOP RULE (project level): if dynamic is worse on BOTH cost and outcome across
10 briefs, the finding is "the static DAG was right". Write that down as the
result. Do not patch around it.
"""
import argparse
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from baton.adapters.company_os import charter as C          # noqa: E402
from baton.adapters.company_os import dispatch as D         # noqa: E402
from baton.adapters.company_os import registry as R         # noqa: E402
from baton.runtime import DispatchResult, run        # noqa: E402
from baton.trace import Trace                        # noqa: E402

FORK_DB = pathlib.Path.home() / ".unlimited-os" / "state.db"
TRACE_DIR = pathlib.Path(__file__).resolve().parent / "traces"


def recorded(limit):
    """(project_id, project_type, brief, recorded_usd, recorded_state)."""
    con = sqlite3.connect(f"file:{FORK_DB}?mode=ro", uri=True)
    rows = con.execute("""
        select p.project_id, p.project_type, p.brief_json, p.state,
               coalesce((select sum(usd) from cost_ledger c
                         where c.project_id = p.project_id), 0.0)
        from projects p order by p.created_ts limit ?""", (limit,)).fetchall()
    con.close()
    out = []
    for pid, ptype, brief_json, state, usd in rows:
        try:
            brief = json.loads(brief_json)
            text = (brief.get("raw") or brief.get("brief")
                    or json.dumps(brief)[:800]) if isinstance(brief, dict) else str(brief)
        except ValueError:
            text = str(brief_json)[:800]
        out.append((pid, ptype, text.strip() or pid, float(usd or 0.0), state))
    return out


def criteria_for(brief):
    """Replay uses a fixed, honest bar rather than a fresh model pass, so the
    comparison is not quietly graded on a different scale each run."""
    return ("the deliverable exists and is named",
            "it addresses the brief as written",
            "no acceptance criterion is left unevidenced")


def replay_one(pid, ptype, brief, dispatch):
    ch = C.charter_for(brief, project_type=ptype,
                       acceptance_criteria=criteria_for(brief))
    agents = R.load_agents(pool=ch.agent_pool)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    trace = Trace(TRACE_DIR / f"{pid}.jsonl", trace_id=pid)
    return run(ch, agents, dispatch, trace=trace)


STUB_PATHS = [f"workspace/dry-run-{i}.md" for i in range(12)]
STUB_BODY = ("The dry-run deliverable, in full. It is long enough to be "
             "real work rather than a claim about it.")


def stub_dispatch(agent, baton, prompt):
    """Wiring proof only. Hands down one layer, then proposes done."""
    if agent.is_gate:
        # Coverage and a real-sized deliverable, because a well-behaved gate
        # names what satisfies each criterion. A stub that omits it models a
        # BROKEN gate and would prove the wiring works by driving the failure
        # path — the same trap the empty-artifact stub fell into.
        # DISTINCT paths, one per criterion. A stub citing a single artifact
        # for everything models a gate gaming its own coverage check — which
        # the runtime now refuses, so the stub would again have been proving
        # the wiring works by driving the failure path.
        body = ('{"decision": "RATIFY", "summary": "dry run", '
                '"coverage": %s}' % json.dumps(STUB_PATHS))
    elif baton.hop >= 2:
        # Attaches a deliverable, because a well-behaved agent does. A stub that
        # proposes done with nothing attached models a BROKEN agent, and since
        # 2026-08-21 the runtime correctly refuses to call that a delivery
        # (Guards.require_artifacts) — so this stub would have been proving the
        # wiring works by driving it down the failure path.
        body = ('{"decision": "PROPOSE_DONE", "summary": "dry run", '
                '"artifacts": %s}' % json.dumps(
                    [{"path": q, "description": "stub", "content": STUB_BODY}
                     for q in STUB_PATHS]))
    else:
        to = sorted(agent.can_hand_to)[0]
        body = ('{"decision": "HANDOFF", "to": "%s", "goal": "dry run", '
                '"rationale": "dry run"}' % to)
    return DispatchResult(text="```handoff\n" + body + "\n```",
                          cost_usd=0.0, in_tokens=len(prompt) // 4, out_tokens=40)


def table(rows):
    print(f"{'project':<28} {'type':<22} {'static $':>9} {'dyn $':>8} "
          f"{'hops':>5}  outcome")
    for pid, ptype, static_usd, r in rows:
        print(f"{pid[:28]:<28} {ptype[:22]:<22} {static_usd:>9.2f} "
              f"{r.spend_usd:>8.2f} {r.hops:>5}  {r.terminal_reason}")
    if rows:
        s = sum(x[2] for x in rows)
        d = sum(x[3].spend_usd for x in rows)
        won = sum(1 for x in rows if x[3].terminal_reason == "ratified")
        print(f"\ntotal static ${s:.2f}   dynamic ${d:.2f}   "
              f"ratified {won}/{len(rows)}")
        if d >= s and won < len(rows) / 2:
            print("\nSTOP RULE: dynamic is worse on cost AND outcome. "
                  "Write that down as the finding.")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm-spend", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("-n", type=int, default=5)
    args = ap.parse_args(argv)

    if args.selftest:
        rows = recorded(1)
        assert rows, "fork corpus is empty"
        r = replay_one(rows[0][0], rows[0][1], rows[0][2], stub_dispatch)
        assert r.terminal_reason in ("ratified", "hops_exhausted"), r.terminal_reason
        print("bench/replay.py --selftest ok")
        return 0

    if args.list:
        for pid, ptype, _brief, usd, state in recorded(args.n):
            print(f"{pid:<32} {ptype:<24} ${usd:>7.2f}  {state}")
        return 0

    if not (args.dry_run or args.confirm_spend):
        print("refusing to run: pass --dry-run (free) or --confirm-spend "
              "(costs money)", file=sys.stderr)
        return 2

    dispatch = stub_dispatch if args.dry_run else D.real_dispatch
    rows = []
    for pid, ptype, brief, usd, _state in recorded(args.n):
        rows.append((pid, ptype, usd, replay_one(pid, ptype, brief, dispatch)))
    table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
