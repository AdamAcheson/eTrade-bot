"""Tests for the pre-backtest session check.

A session fetched while still in progress returns partial volume -- on
2026-09-11 at 12:00 ET, AG's opening bar held 13,784 shares against 1,551,879 the
session before, with prices perfectly sane. Relative volume is a hard entry gate,
so that session backtests to zero entries and reads as a quiet market rather than
a data problem. These tests pin the check that catches it."""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import check_session_data as csd  # noqa: E402

from models.bar import Bar  # noqa: E402

ET = ZoneInfo("America/New_York")


def session(day, bars=78, volume_each=100_000.0):
    base = datetime(2026, 9, day, 9, 30, tzinfo=ET)
    return [Bar(timestamp=base + timedelta(minutes=5 * i), open=20.0, high=20.1,
                low=19.9, close=20.0, volume=volume_each) for i in range(bars)]


def fake_cache(monkeypatch, target_bars, target_volume):
    """Ten normal prior sessions plus a target session under test."""
    all_bars = []
    for d in range(1, 11):
        all_bars += session(d)
    all_bars += session(11, bars=target_bars, volume_each=target_volume)
    monkeypatch.setattr(csd, "get_bars", lambda symbol: all_bars)


def test_a_complete_session_passes(monkeypatch):
    fake_cache(monkeypatch, 78, 100_000.0)
    status, detail = csd.check_symbol("AG", "2026-09-11", 10, 0.5)
    assert status == "ok", detail


def test_a_session_still_in_progress_is_flagged(monkeypatch):
    """31 of 78 bars -- exactly the midday case that prompted this."""
    fake_cache(monkeypatch, 31, 100_000.0)
    status, detail = csd.check_symbol("AG", "2026-09-11", 10, 0.5)
    assert status == "warn"
    assert "incomplete" in detail


def test_a_full_length_session_with_thin_volume_is_flagged(monkeypatch):
    """The subtler failure: all 78 bars present, but volume never consolidated.
    Bar count alone would pass this."""
    fake_cache(monkeypatch, 78, 2_000.0)
    status, detail = csd.check_symbol("AG", "2026-09-11", 10, 0.5)
    assert status == "warn"
    assert "too thin" in detail


def test_a_genuinely_quiet_but_valid_session_passes(monkeypatch):
    """60% of typical volume is a slow day, not a broken feed -- it must not be
    flagged, or the check trains you to ignore it."""
    fake_cache(monkeypatch, 78, 60_000.0)
    assert csd.check_symbol("AG", "2026-09-11", 10, 0.5)[0] == "ok"


def test_a_missing_session_is_reported(monkeypatch):
    fake_cache(monkeypatch, 78, 100_000.0)
    status, detail = csd.check_symbol("AG", "2026-09-12", 10, 0.5)
    assert status == "missing"


def test_no_cache_at_all_is_reported_not_raised(monkeypatch):
    from data.historical_data import HistoricalDataError

    def boom(symbol):
        raise HistoricalDataError("nothing cached")

    monkeypatch.setattr(csd, "get_bars", boom)
    assert csd.check_symbol("NOPE", "2026-09-11", 10, 0.5)[0] == "missing"
