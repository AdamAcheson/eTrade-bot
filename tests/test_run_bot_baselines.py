"""Tests for seeding relative-volume baselines in the live loop.

scripts/run_bot.py never set them. min_relative_volume is 1.10 and an unset
baseline leaves RVOL unavailable, so every signal rejected REJECTED_LOW_VOLUME --
the loop would poll real quotes all session and never take a trade. Its own
docstring recorded this as a known limitation; the historical cache now makes it
fixable."""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from run_bot import session_bar_index, volume_baselines_from_cache  # noqa: E402

from models.bar import Bar  # noqa: E402

ET = ZoneInfo("America/New_York")


def test_session_slot_counts_five_minute_bars_from_the_open():
    assert session_bar_index(datetime(2026, 9, 11, 9, 30), "09:30") == 0
    assert session_bar_index(datetime(2026, 9, 11, 9, 34), "09:30") == 0
    assert session_bar_index(datetime(2026, 9, 11, 9, 35), "09:30") == 1
    assert session_bar_index(datetime(2026, 9, 11, 10, 35), "09:30") == 13
    assert session_bar_index(datetime(2026, 9, 11, 15, 55), "09:30") == 77


def test_pre_open_is_negative_not_clamped_to_the_first_slot():
    """A quote at 09:00 is not the first bar of the day -- treating it as slot 0
    would compare pre-market volume against the opening five minutes."""
    assert session_bar_index(datetime(2026, 9, 11, 9, 0), "09:30") < 0


def test_baseline_is_cumulative_per_slot(monkeypatch):
    """RVOL compares volume-so-far against a normal day's volume BY THIS TIME, so
    the baseline must accumulate across the session rather than be a flat daily
    average."""
    import run_bot

    def fake_bars(symbol):
        out = []
        for day in (2, 3):
            for i in range(4):
                out.append(Bar(timestamp=datetime(2026, 3, day, 9, 30 + 5 * i, tzinfo=ET),
                               open=1.0, high=1.0, low=1.0, close=1.0, volume=100.0))
        return out

    monkeypatch.setattr(run_bot, "get_bars", fake_bars)
    b = run_bot.volume_baselines_from_cache("AG", 20)
    assert b == {0: 100.0, 1: 200.0, 2: 300.0, 3: 400.0}


def test_only_the_most_recent_sessions_are_averaged(monkeypatch):
    """A three-year average would understate a name whose volume has since doubled."""
    import run_bot

    def fake_bars(symbol):
        out = []
        for day, vol in ((2, 100.0), (3, 100.0), (4, 500.0)):
            out.append(Bar(timestamp=datetime(2026, 3, day, 9, 30, tzinfo=ET),
                           open=1.0, high=1.0, low=1.0, close=1.0, volume=vol))
        return out

    monkeypatch.setattr(run_bot, "get_bars", fake_bars)
    assert run_bot.volume_baselines_from_cache("AG", 1) == {0: 500.0}
    assert run_bot.volume_baselines_from_cache("AG", 3)[0] == (100 + 100 + 500) / 3


def test_a_symbol_with_no_cache_yields_no_baseline_rather_than_crashing(monkeypatch):
    """One uncached symbol must not stop the loop for the other twenty."""
    import run_bot
    from data.historical_data import HistoricalDataError

    def boom(symbol):
        raise HistoricalDataError("no cache")

    monkeypatch.setattr(run_bot, "get_bars", boom)
    assert run_bot.volume_baselines_from_cache("NOPE", 20) == {}


def test_real_cached_symbol_produces_a_full_session_of_slots():
    b = volume_baselines_from_cache("AG", 20)
    assert len(b) >= 78
    assert all(b[i] <= b[i + 1] + 1 for i in range(77))   # cumulative
    assert b[0] > 0
