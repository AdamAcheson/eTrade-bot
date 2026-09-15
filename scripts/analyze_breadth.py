#!/usr/bin/env python3
"""Measures effective breadth of a backtest run from its trade journal.

The Fundamental Law of Active Management says IR = IC x sqrt(breadth): skill per
bet times the number of INDEPENDENT bets. Nominal breadth (ticker count) is not
breadth -- twenty silver miners moving together are closer to one bet than twenty.
This computes the effective count:

    N_eff = N / (1 + (N-1) * rho_bar)

where rho_bar is the mean pairwise correlation of per-ticker daily P&L.

Two correlations are reported because they answer different questions:

  * portfolio   -- every session day in the window, no-trade days filled with 0.
                   This is what the equity curve actually experiences, and it
                   folds in CO-ACTIVITY: tickers that sit out the same days are
                   correlated even if their trades are not.
  * conditional -- only days where BOTH tickers traded. This isolates whether the
                   SIGNALS are correlated, with the activity effect removed.

Usage:
    python3 scripts/analyze_breadth.py reports/backtest_trades<TAG>.jsonl [...]
"""

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from itertools import combinations


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def pnl_by_ticker_day(trades):
    out = defaultdict(lambda: defaultdict(float))
    for t in trades:
        out[t["ticker"]][t["date"]] += t["net_profit"]
    return {k: dict(v) for k, v in out.items()}


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def effective_breadth(n, rho_bar):
    denom = 1 + (n - 1) * rho_bar
    return n / denom if denom > 0 else float("inf")


def summarise(trades, label):
    by = pnl_by_ticker_day(trades)
    tickers = sorted(by)
    all_days = sorted({t["date"] for t in trades})
    n = len(tickers)

    portfolio, conditional = [], []
    for a, b in combinations(tickers, 2):
        da, db = by[a], by[b]
        c = corr([da.get(d, 0.0) for d in all_days], [db.get(d, 0.0) for d in all_days])
        if c is not None:
            portfolio.append(c)
        both = sorted(set(da) & set(db))
        c2 = corr([da[d] for d in both], [db[d] for d in both])
        if c2 is not None:
            conditional.append(c2)

    net = sum(t["net_profit"] for t in trades)
    day_pnl = defaultdict(float)
    for t in trades:
        day_pnl[t["date"]] += t["net_profit"]
    dv = list(day_pnl.values())
    sharpe = (statistics.mean(dv) / statistics.stdev(dv) * math.sqrt(252)) if len(dv) > 2 and statistics.stdev(dv) else float("nan")

    ranked = sorted((t["net_profit"] for t in trades), reverse=True)
    top5 = sum(ranked[: max(1, len(ranked) // 20)])

    print(f"\n=== {label} ===")
    print(f"tickers {n}   trades {len(trades)}   days traded {len(all_days)}   net ${net:,.0f}")
    print(f"daily-P&L Sharpe (annualised, trading days only): {sharpe:.2f}")
    print(f"top 5% of trades = ${top5:,.0f} ({top5 / net:.0%} of net)" if net else "")
    for name, cs in (("portfolio", portfolio), ("conditional", conditional)):
        if not cs:
            continue
        rb = statistics.mean(cs)
        print(f"{name:12s} mean pairwise corr {rb:+.3f}  (median {statistics.median(cs):+.3f}, "
              f"{len(cs)} pairs)   effective breadth {effective_breadth(n, rb):.1f} of {n}")
    return {"n": n, "net": net, "sharpe": sharpe,
            "rho_portfolio": statistics.mean(portfolio) if portfolio else None,
            "rho_conditional": statistics.mean(conditional) if conditional else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("journals", nargs="+", help="reports/backtest_trades<TAG>.jsonl files")
    args = ap.parse_args()
    for path in args.journals:
        summarise(load(path), path.split("/")[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
