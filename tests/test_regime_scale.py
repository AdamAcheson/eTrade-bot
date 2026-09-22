"""trend_days backs the regime-scaled sizing experiment. The property that matters
is the lookahead one: the trend window must end at the PRIOR close, never today's."""

import json
import os
import tempfile

import pytest

from data.market_regime import trend_days


def _cache(series):
    """Write a daily series to a temp cache dir in the shape fetch_daily returns."""
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "daily_TEST.json"), "w") as fh:
        json.dump({day: {"open": c, "close": c} for day, c in series.items()}, fh)
    return d


def _days(n, closes):
    return {f"2024-01-{i + 1:02d}": c for i, c in enumerate(closes[:n])}


def test_window_ends_at_the_prior_close_not_today():
    # flat for 4 sessions, then a huge jump on the LAST day. With lookback=2 the
    # jump day itself must NOT qualify (its window ends at the prior close, which
    # is still flat); the day after would, if one existed.
    cache = _cache(_days(5, [100.0, 100.0, 100.0, 100.0, 200.0]))
    good = trend_days("TEST", lookback=2, threshold_pct=5.0, cache_dir=cache)
    assert "2024-01-05" not in good


def test_qualifies_the_day_after_the_move_is_settled():
    cache = _cache(_days(6, [100.0, 100.0, 100.0, 100.0, 200.0, 200.0]))
    good = trend_days("TEST", lookback=2, threshold_pct=5.0, cache_dir=cache)
    assert "2024-01-06" in good


def test_threshold_gates_on_the_size_of_the_move():
    # Deliberately not testing the exact boundary: 105/100 - 1 evaluates to
    # 5.000000000000004 in binary floating point, so "exactly 5%" is not a
    # well-defined case and nothing in the experiment depends on which way it
    # falls. Test clearly-under and clearly-over instead.
    cache = _cache(_days(4, [100.0, 100.0, 104.0, 104.0]))
    assert trend_days("TEST", lookback=2, threshold_pct=5.0, cache_dir=cache) == set()
    assert "2024-01-04" in trend_days("TEST", lookback=2, threshold_pct=3.0, cache_dir=cache)


def test_insufficient_history_is_not_a_qualifying_day():
    cache = _cache(_days(3, [100.0, 150.0, 200.0]))
    # needs lookback+1 prior sessions; nothing here has them
    assert trend_days("TEST", lookback=5, threshold_pct=1.0, cache_dir=cache) == set()


def test_falling_series_qualifies_nothing():
    cache = _cache(_days(10, [100.0 - 5 * i for i in range(10)]))
    assert trend_days("TEST", lookback=3, threshold_pct=5.0, cache_dir=cache) == set()


def test_lookback_must_be_positive():
    cache = _cache(_days(5, [100.0] * 5))
    with pytest.raises(ValueError):
        trend_days("TEST", lookback=0, threshold_pct=5.0, cache_dir=cache)
