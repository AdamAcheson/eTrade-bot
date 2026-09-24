#!/usr/bin/env python3
"""End-to-end daily session replay: refresh cache -> gate the data -> backtest one
session -> append a row to docs/DAILY_REPLAY_LOG.md.

Why this exists as a script rather than as prose steps in a scheduled agent's
prompt: the routine that ran these steps by hand recorded SUCCEEDED on 2026-09-14
and 2026-09-16 while doing no work at all, because a session that cannot even find
the repo still exits cleanly. Prose steps cannot set an exit code. This can, and
the exit codes are the contract:

    0  replay ran, a row was appended (or the row was already present)
    2  TWELVEDATA_API_KEY missing -- nothing ran, NOT a success
    3  not a trading session (weekend/holiday) -- nothing to do, not a failure
    4  data never became usable -- a row IS appended saying so
    5  a subprocess failed

Anything scheduling this must treat a nonzero exit other than 3 as a failure,
whatever the surrounding session reports about itself.

Usage:
    python3 scripts/daily_replay.py [--day YYYY-MM-DD] [--no-commit] [--attempts 4]
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "docs", "DAILY_REPLAY_LOG.md")
ET = ZoneInfo("America/New_York")
TAG = "_daily"

# NYSE full-day closures. Half days (early close) still trade, so they are not here
# -- a 13:00 close simply yields fewer bars, which check_session_data.py flags on
# its own rather than being silently accepted.
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}


def run(cmd, **kw):
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS_2026


def slv_open_to_close(day: str):
    import csv
    path = os.path.join(ROOT, "data_cache", "historical", "SLV.csv")
    rows = [r for r in csv.DictReader(open(path)) if r["timestamp"][:10] == day]
    if not rows:
        return None
    return (float(rows[-1]["close"]) / float(rows[0]["open"]) - 1) * 100


def summarise(day: str):
    signals = [json.loads(l) for l in open(os.path.join(ROOT, "logs", f"backtest_signals{TAG}.jsonl"))]
    trades_path = os.path.join(ROOT, "reports", f"backtest_trades{TAG}.jsonl")
    trades = [json.loads(l) for l in open(trades_path)] if os.path.exists(trades_path) else []
    rvol = [s["relative_volume"] for s in signals if s.get("relative_volume")]
    rejections = Counter(s["rejection_reason"] for s in signals if s.get("decision") == "REJECTED")
    top = rejections.most_common(1)[0] if rejections else ("none", 0)
    return {
        "signals": len(signals),
        "trades": len(trades),
        "net": sum(t["net_profit"] for t in trades),
        "median_rvol": statistics.median(rvol) if rvol else float("nan"),
        "top_rejection": top[0].replace("REJECTED_", "").lower().replace("_", " "),
        "top_count": top[1],
        "slv": slv_open_to_close(day),
    }


def row_for(day: str, s, note: str) -> str:
    slv = f"{s['slv']:+.2f}%" if s["slv"] is not None else "n/a"
    return (f"| {day} | {slv} | {s['signals']} | {s['trades']} | "
            f"{'+' if s['net'] >= 0 else '-'}${abs(s['net']):,.2f} | {s['median_rvol']:.2f} | "
            f"{s['top_rejection']} ({s['top_count']}) | {note} |")


TALLY_HEADING = "### Running tally"

# First session logged on the shipped $5,000 cash config; earlier rows are on the
# old $100,000 / $25,000-per-trade default. See the log's header.
BASIS_CHANGE = "2026-09-23"


def _summarise(rows) -> str:
    sessions = len(rows)
    trades = net = 0
    active = 0
    for line in rows:
        cells = [c.strip() for c in line.split("|")]
        try:
            n = int(cells[4])
            pnl = float(cells[5].replace("$", "").replace(",", "").replace("**", ""))
        except (IndexError, ValueError):
            # A row that cannot be parsed is skipped rather than fatal. By the time
            # this runs the row is already written, so raising would fail the job
            # AFTER mutating the log -- the one outcome worse than an approximate
            # tally.
            continue
        trades += n
        active += 1 if n else 0
        net += pnl
    return (f"{sessions} sessions logged, {active} with trades, {sessions - active} with zero. "
            f"Cumulative P&L **{'+' if net >= 0 else '-'}${abs(net):,.2f}** across {trades} trades.")


def refresh_tally() -> None:
    """Regenerate the running tally from the table itself.

    It used to be hand-written, and by 2026-09-18 it claimed "+$110.43 across 3
    trades" while the table summed to -$181.81 across 8 -- stale in the flattering
    direction, which is the worst way for a P&L summary to be wrong. Deriving it
    from the rows means it cannot drift again.

    The tally must stay the LAST section of the file: everything from its heading
    to EOF is replaced.
    """
    with open(LOG) as f:
        text = f.read()
    rows = [l for l in text.split("\n") if re.match(r"^\| 20\d\d-\d\d-\d\d \|", l)]
    # Never add dollars across the equity-basis change: a $100,000-basis row is
    # 12.5x a $5,000-basis one, so a single sum would be meaningless.
    current = [l for l in rows if l[2:12] >= BASIS_CHANGE]
    old = [l for l in rows if l[2:12] < BASIS_CHANGE]
    parts = [TALLY_HEADING]
    if current:
        parts.append(f"**$5,000 cash basis (from {BASIS_CHANGE}):** " + _summarise(current))
    if old:
        parts.append(f"**Old $100,000 basis (before {BASIS_CHANGE}; divide by 12.5):** "
                     + _summarise(old))
    parts.append("Far too small a sample to compare against the backtest's ~30% zero-trade "
                 "rate and 2.6 trades per active session; the point remains accumulation.\n")
    body = "\n\n".join(parts)
    head = text.split(TALLY_HEADING)[0] if TALLY_HEADING in text else text.rstrip() + "\n\n"
    with open(LOG, "w") as f:
        f.write(head + body)


def append_row(day: str, row: str) -> bool:
    """Insert after the last existing table row. Returns False if `day` is already
    logged -- re-running must never double-count a session, and a scheduler that
    retries is exactly how that would happen."""
    with open(LOG) as f:
        lines = f.read().split("\n")
    if any(l.startswith(f"| {day} |") for l in lines):
        print(f"{day} already logged -- not appending again")
        return False
    last = max(i for i, l in enumerate(lines) if l.startswith("| 20"))
    lines.insert(last + 1, row)
    with open(LOG, "w") as f:
        f.write("\n".join(lines))
    refresh_tally()
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="session to replay (default: today in ET)")
    ap.add_argument("--attempts", type=int, default=4,
                    help="data-gate retries; Twelve Data consolidates ~80min after close, unevenly")
    ap.add_argument("--no-commit", action="store_true")
    args = ap.parse_args()

    day = args.day or datetime.now(ET).date().isoformat()
    if not is_trading_day(date.fromisoformat(day)):
        print(f"{day} is not a trading session -- nothing to do")
        return 3

    if not os.environ.get("TWELVEDATA_API_KEY"):
        print("TWELVEDATA_API_KEY is not set. No replay ran. THIS IS A FAILURE, not a quiet day.",
              file=sys.stderr)
        return 2

    for attempt in range(1, args.attempts + 1):
        fetched = run(["python3", "scripts/fetch_historical_data_twelvedata.py",
                       "--days", "5", "--refetch-recent", "2"])
        print(fetched.stdout[-2000:])
        if fetched.returncode != 0:
            print(fetched.stderr[-2000:], file=sys.stderr)
            return 5
        gate = run(["python3", "scripts/check_session_data.py", "--day", day])
        print(gate.stdout[-3000:])
        if gate.returncode == 0:
            break
        print(f"data gate failed (attempt {attempt}/{args.attempts})")
    else:
        s = {"signals": 0, "trades": 0, "net": 0.0, "median_rvol": float("nan"),
             "top_rejection": "n/a", "top_count": 0, "slv": slv_open_to_close(day)}
        append_row(day, row_for(day, s, "DATA UNUSABLE -- gate never passed, no replay run"))
        if not args.no_commit:
            commit(day, "data unusable")
        return 4

    bt = run(["python3", "scripts/backtest.py", "--days", "1", "--tag", TAG])
    if bt.returncode != 0:
        print(bt.stderr[-3000:], file=sys.stderr)
        return 5
    print(bt.stdout[-1500:])

    s = summarise(day)
    note = "no entries" if s["trades"] == 0 else f"{s['trades']} trade(s)"
    if append_row(day, row_for(day, s, note)) and not args.no_commit:
        commit(day, note)

    print(f"\n{day}: {s['trades']} trades, net ${s['net']:,.2f}, "
          f"median RVOL {s['median_rvol']:.2f}, top rejection {s['top_rejection']} ({s['top_count']})")
    return 0


def commit(day: str, note: str):
    # data_cache/ goes in alongside the log. The bars this run just fetched are
    # tracked files, and staging only the log meant every CI run re-spent Twelve
    # Data credits on data it had already pulled, while no checkout could ever
    # reproduce the session CI had just logged. The cost is repository size: ~33
    # CSVs grow by one session's bars per run, which is the deliberate trade for
    # a cache that actually accumulates.
    run(["git", "add", "docs/DAILY_REPLAY_LOG.md", "data_cache"])
    r = run(["git", "commit", "-m",
             f"Daily replay {day}: {note}\n\n"
             f"Appended by scripts/daily_replay.py.\n\n"
             f"Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"])
    if r.returncode == 0:
        run(["git", "push", "origin", "HEAD"])


if __name__ == "__main__":
    sys.exit(main())
