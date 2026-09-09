"""Tests for generate_feedback_suggestions -- which had NO coverage, and was
consequently emitting statistically meaningless guidance in every backtest report:
an "entries around 15:00 outperformed 11:00 (67% vs 16%)" line computed from a
three-trade bucket, and an RVOL comparison that fired on win rates differing only
in rounding. It also mis-classified RVOL by keeping one value per ticker rather
than per trade. Suggestions that read as evidence are worse than no suggestions."""

from datetime import datetime, timedelta

from models.signal import Signal
from models.trade import ExitReason, Trade
from reporting.daily_report import MIN_GROUP_SAMPLE, generate_feedback_suggestions

BASE = datetime(2026, 3, 2, 10, 0)


def trade(n, hour=10, profit=1.0, ticker="AG", mfe=0.0, reason=ExitReason.TARGET_HIT):
    ts = BASE.replace(hour=hour) + timedelta(minutes=n)
    return Trade(
        ticker=ticker, date=ts.date().isoformat(), entry_time=ts, entry_price=10.0,
        shares=100, initial_stop=9.5, initial_target=11.0,
        exit_time=ts + timedelta(minutes=30), exit_price=10.5, exit_reason=reason,
        gross_profit=profit, net_profit=profit, maximum_favorable_excursion=mfe,
    )


def signal_for(t, rvol):
    return Signal(
        timestamp=t.entry_time, ticker=t.ticker, benchmark="SIL",
        price=10.0, bid=9.99, ask=10.0, spread=0.01,
        vwap=9.9, ema_9=9.9, ema_20=9.8, atr=0.15, atr_percent=1.5,
        relative_volume=rvol, opening_range_high=10.1, opening_range_low=9.8,
        benchmark_price=50.0, benchmark_vwap=49.9, benchmark_ema_9=49.9,
        benchmark_ema_20=49.8, entry_candidate=True,
    )


def test_no_trades_yields_nothing():
    assert generate_feedback_suggestions([], []) == []


def test_hour_comparison_suppressed_below_minimum_sample():
    # The exact shape of the old bug: a tiny "good" hour against a larger bad one.
    winners = [trade(i, hour=15, profit=1.0) for i in range(3)]
    losers = [trade(i, hour=11, profit=-1.0) for i in range(30)]
    out = generate_feedback_suggestions(winners + losers, [])
    assert not any("15:00" in s for s in out), out


def test_hour_comparison_reported_once_both_sides_are_large_enough():
    winners = [trade(i, hour=15, profit=1.0) for i in range(MIN_GROUP_SAMPLE)]
    losers = [trade(i, hour=11, profit=-1.0) for i in range(MIN_GROUP_SAMPLE)]
    out = generate_feedback_suggestions(winners + losers, [])
    hour_msgs = [s for s in out if "15:00" in s]
    assert len(hour_msgs) == 1
    # Counts must be shown so the reader can judge the strength themselves.
    assert str(MIN_GROUP_SAMPLE) in hour_msgs[0]


def test_rvol_comparison_suppressed_below_minimum_sample():
    hi = [trade(i, profit=1.0) for i in range(3)]
    lo = [trade(i + 100, profit=-1.0) for i in range(30)]
    sigs = [signal_for(t, 2.0) for t in hi] + [signal_for(t, 1.0) for t in lo]
    out = generate_feedback_suggestions(hi + lo, sigs)
    assert not any("RVOL" in s for s in out), out


def test_rvol_comparison_suppressed_when_gap_is_trivial():
    # Equal win rates -- the old code fired on differences that were pure rounding.
    hi = [trade(i, profit=1.0 if i % 2 else -1.0) for i in range(MIN_GROUP_SAMPLE)]
    lo = [trade(i + 100, profit=1.0 if i % 2 else -1.0) for i in range(MIN_GROUP_SAMPLE)]
    sigs = [signal_for(t, 2.0) for t in hi] + [signal_for(t, 1.0) for t in lo]
    out = generate_feedback_suggestions(hi + lo, sigs)
    assert not any("RVOL" in s for s in out), out


def test_rvol_is_classified_per_trade_not_per_ticker():
    """Regression: RVOL used to be stored as {ticker: rvol}, keeping only the last
    signal per ticker -- so every trade in a ticker was bucketed by one value."""
    hi = [trade(i, profit=1.0, ticker="AG") for i in range(MIN_GROUP_SAMPLE)]
    lo = [trade(i + 100, profit=-1.0, ticker="AG") for i in range(MIN_GROUP_SAMPLE)]
    # Same ticker throughout; only the per-entry RVOL distinguishes the groups.
    sigs = [signal_for(t, 2.0) for t in hi] + [signal_for(t, 1.0) for t in lo]
    out = generate_feedback_suggestions(hi + lo, sigs)
    rvol_msgs = [s for s in out if "RVOL" in s]
    assert len(rvol_msgs) == 1, out
    assert "100%" in rvol_msgs[0] and "0%" in rvol_msgs[0]


def test_per_ticker_warning_needs_a_real_sample():
    few = [trade(i, profit=-1.0, ticker="AG") for i in range(3)]
    assert not any("AG" in s for s in generate_feedback_suggestions(few, []))

    many = [trade(i, profit=-1.0, ticker="AG") for i in range(12)]
    assert any("AG" in s and "12 trades" in s for s in generate_feedback_suggestions(many, []))


def test_stop_review_hint_needs_a_real_sample():
    few = [trade(i, profit=-1.0, mfe=0.5, reason=ExitReason.STOP_HIT) for i in range(5)]
    assert not any("ATR stop" in s for s in generate_feedback_suggestions(few, []))

    many = [trade(i, profit=-1.0, mfe=0.5, reason=ExitReason.STOP_HIT)
            for i in range(MIN_GROUP_SAMPLE)]
    assert any("ATR stop" in s for s in generate_feedback_suggestions(many, []))
