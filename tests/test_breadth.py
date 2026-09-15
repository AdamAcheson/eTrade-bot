"""Tests for scripts/analyze_breadth.py.

Effective breadth is the number this project's remaining upside was argued from
(IR = IC x sqrt(breadth)), and it is a formula that looks right while being wrong
in the two ways that matter: a universe whose members never trade together should
count as fully independent, and a universe that always moves as one should count
as a single bet no matter how many names it lists. Both are pinned here, along
with the distinction between the portfolio correlation (no-trade days filled with
zero, which folds in co-activity) and the conditional one (both-traded days only).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import analyze_breadth as ab  # noqa: E402


def trade(ticker, date, net):
    return {"ticker": ticker, "date": date, "net_profit": net}


def test_identical_tickers_collapse_to_one_effective_bet():
    """Two names with the same P&L every day are one bet, not two."""
    days = [f"2026-01-{d:02d}" for d in range(1, 21)]
    pnl = [100, -50, 200, -30, 10, -80, 150, 20, -10, 60] * 2
    trades = [trade(t, d, p) for d, p in zip(days, pnl) for t in ("AAA", "BBB")]
    by = ab.pnl_by_ticker_day(trades)
    xs = [by["AAA"][d] for d in days]
    ys = [by["BBB"][d] for d in days]
    assert ab.corr(xs, ys) == 1.0
    assert ab.effective_breadth(2, 1.0) == 1.0


def test_uncorrelated_pair_keeps_full_breadth():
    assert ab.effective_breadth(2, 0.0) == 2.0
    assert ab.effective_breadth(36, 0.0) == 36.0


def test_partial_correlation_sits_between():
    """The mining/combined comparison turns on this middle range, so pin it
    numerically rather than trusting the direction alone."""
    assert ab.effective_breadth(20, 0.115) == 20 / (1 + 19 * 0.115)
    assert 6.0 < ab.effective_breadth(20, 0.115) < 6.5
    assert 9.5 < ab.effective_breadth(36, 0.077) < 10.0


def test_portfolio_correlation_sees_co_activity_conditional_does_not():
    """AAA and BBB trade on disjoint days. Filling the gaps with zero makes them
    look ANTI-correlated (one is up while the other is flat); restricted to days
    both traded there is no overlap at all. Conflating the two would let a change
    in when the bot trades masquerade as a change in what it trades."""
    trades = (
        [trade("AAA", f"2026-01-{d:02d}", 100) for d in range(1, 11)]
        + [trade("BBB", f"2026-02-{d:02d}", 100) for d in range(1, 11)]
    )
    by = ab.pnl_by_ticker_day(trades)
    all_days = sorted({t["date"] for t in trades})
    filled = ab.corr([by["AAA"].get(d, 0.0) for d in all_days],
                     [by["BBB"].get(d, 0.0) for d in all_days])
    assert filled is not None and filled < 0
    both = sorted(set(by["AAA"]) & set(by["BBB"]))
    assert both == []


def test_corr_declines_to_guess_on_degenerate_input():
    """A flat series has no correlation with anything -- returning 0.0 there would
    quietly inflate effective breadth for a ticker that simply never varied."""
    assert ab.corr([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert ab.corr([1.0, 2.0], [2.0, 4.0]) is None  # fewer than 3 points


def test_pnl_by_ticker_day_sums_multiple_trades_in_a_day():
    trades = [trade("AAA", "2026-01-02", 100), trade("AAA", "2026-01-02", -40),
              trade("AAA", "2026-01-03", 25)]
    by = ab.pnl_by_ticker_day(trades)
    assert by["AAA"] == {"2026-01-02": 60, "2026-01-03": 25}
