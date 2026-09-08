"""Regression test for a real bug found while investigating why ORB_PULLBACK_
CONTINUATION barely ever matched in real backtests (0.4% of the ~6000 times it was
attempted, vs. VWAP_RECLAIM matching 230x more often): unlike every other step in
detect_opening_range_breakout_pullback (breakout, pullback, higher-low), the final
confirmation-candle check required the confirmation to be bars[-1] specifically --
the single most-recently-closed bar -- rather than searching the bars since the
higher low the way the other steps do. A real confirmation candle that landed one
5-minute cycle early or late relative to exactly when the bot happened to evaluate
was being scored as a miss even though the pattern genuinely played out."""

from strategy.setups import detect_opening_range_breakout_pullback

from factories import bar, base_stock_bars


def test_confirmation_matches_even_when_not_the_last_bar():
    # base_stock_bars() is the known-good 6-bar ORB pullback+confirmation sequence
    # (see test_signals.py's scenario 1: opening range, breakout, pullback,
    # higher low, confirmation candle as the LAST bar). Append one more bar that
    # is NOT itself a valid confirmation (red, closes below the higher-low bar's
    # high) -- under the old bars[-1]-only logic this would flip the match to
    # False even though a real confirmation already happened one bar earlier.
    bars = base_stock_bars() + [bar(30, 10.40, 10.42, 10.35, 10.36, 60_000)]

    result = detect_opening_range_breakout_pullback(
        bars,
        opening_range_high=10.12,
        opening_range_bar_count=2,
        pullback_max_atr_from_breakout=1.0,
        atr_value=0.15,
        ema_9=10.20,
        vwap_value=10.28,
    )

    assert result.matched
    assert result.setup_type == "ORB_PULLBACK_CONTINUATION"
    # Entry is still at the CURRENT (latest) bar's close, not the historical
    # confirmation bar's close.
    assert result.entry_price == 10.36
    assert result.structure_stop_reference == 10.14


def test_no_confirmation_anywhere_still_fails():
    # Same setup, but no bar after the higher low ever closes above its high.
    bars = base_stock_bars()[:5] + [
        bar(25, 10.28, 10.31, 10.27, 10.30, 95_000),  # green, but doesn't clear 10.32
        bar(30, 10.30, 10.31, 10.25, 10.26, 60_000),
    ]

    result = detect_opening_range_breakout_pullback(
        bars,
        opening_range_high=10.12,
        opening_range_bar_count=2,
        pullback_max_atr_from_breakout=1.0,
        atr_value=0.15,
        ema_9=10.20,
        vwap_value=10.28,
    )

    assert not result.matched
    assert result.reason == "no_confirmation_candle"
