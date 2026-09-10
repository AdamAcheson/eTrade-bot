"""session_bars / _prior_session_close replaced linear scans over a ticker's entire
accumulated bar history with a binary search. That history reaches ~58k bars per
symbol with three years cached, and the scan ran four times per ticker per
five-minute step -- a 183-day backtest spent over half an hour in it. These tests
pin the equivalence, including the cases a binary search could plausibly get wrong:
a DST boundary, naive datetimes, and an empty or single-session history."""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from main import _prior_session_close, _same_session_day, session_bars  # noqa: E402

from models.bar import Bar  # noqa: E402

ET = ZoneInfo("America/New_York")


def bars_for(days, tz=ET):
    out = []
    for d in days:
        for i in range(4):
            ts = d.replace(hour=9, minute=30) + timedelta(minutes=5 * i)
            out.append(Bar(timestamp=ts if tz is None else ts.replace(tzinfo=tz),
                           open=1.0, high=1.0, low=1.0, close=float(d.day * 10 + i), volume=1.0))
    return out


def reference(bars, now):
    return [b for b in bars if _same_session_day(b.timestamp, now)]


def test_matches_the_linear_filter_across_a_multi_day_history():
    days = [datetime(2026, 3, d) for d in (2, 3, 4, 5, 6)]
    bars = bars_for(days)
    for d in days:
        now = d.replace(hour=11, tzinfo=ET)
        assert session_bars(bars, now) == reference(bars, now)


def test_matches_across_the_spring_dst_boundary():
    """US DST starts 2026-03-08, so bars either side carry different UTC offsets --
    the comparison has to normalize rather than assume a fixed offset."""
    days = [datetime(2026, 3, d) for d in (6, 9, 10)]
    bars = bars_for(days)
    for d in days:
        now = d.replace(hour=11, tzinfo=ET)
        assert session_bars(bars, now) == reference(bars, now)


def test_matches_with_naive_datetimes():
    days = [datetime(2026, 3, d) for d in (2, 3)]
    bars = bars_for(days, tz=None)
    now = datetime(2026, 3, 3, 11)
    assert session_bars(bars, now) == reference(bars, now)


def test_empty_history_and_a_day_with_no_bars():
    now = datetime(2026, 3, 4, 11, tzinfo=ET)
    assert session_bars([], now) == []
    bars = bars_for([datetime(2026, 3, 2)])
    assert session_bars(bars, now) == []          # a later day, nothing cached for it
    # An EARLIER day must not pick up the sessions that follow it.
    assert session_bars(bars, datetime(2026, 3, 1, 11, tzinfo=ET)) == []


def test_prior_session_close_is_the_last_bar_before_today():
    days = [datetime(2026, 3, d) for d in (2, 3, 4)]
    bars = bars_for(days)
    now = datetime(2026, 3, 4, 11, tzinfo=ET)
    assert _prior_session_close(bars, now) == bars_for([datetime(2026, 3, 3)])[-1].close


def test_prior_session_close_is_none_on_a_cold_start():
    bars = bars_for([datetime(2026, 3, 2)])
    assert _prior_session_close(bars, datetime(2026, 3, 2, 11, tzinfo=ET)) is None
    assert _prior_session_close([], datetime(2026, 3, 2, 11, tzinfo=ET)) is None
