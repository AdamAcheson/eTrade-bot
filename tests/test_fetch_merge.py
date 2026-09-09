"""Tests for the incremental merge/backfill logic in
scripts/fetch_historical_data_twelvedata.py. A deep-history pull costs more than
one day of the free tier's 800-credit budget, so it has to run across several
days without re-fetching (or discarding) what earlier runs already cached."""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from fetch_historical_data_twelvedata import merge_bars, missing_ranges  # noqa: E402

from models.bar import Bar  # noqa: E402

ET = ZoneInfo("America/New_York")


def bar(day, hour=10, minute=0, close=10.0):
    return Bar(timestamp=datetime(2026, 3, day, hour, minute, tzinfo=ET),
               open=close, high=close, low=close, close=close, volume=1000)


def test_merge_dedupes_on_timestamp_and_sorts():
    existing = [bar(2), bar(4)]
    new = [bar(3), bar(4, close=99.0), bar(1)]
    out = merge_bars(existing, new)

    assert [b.timestamp.day for b in out] == [1, 2, 3, 4]
    # Same timestamp appearing in both -- the freshly fetched bar wins.
    assert out[-1].close == 99.0


def test_merge_with_empty_existing_returns_new():
    assert merge_bars([], [bar(2), bar(1)]) == sorted([bar(2), bar(1)], key=lambda b: b.timestamp)


def test_missing_ranges_empty_cache_fetches_everything():
    start = datetime(2026, 1, 1, tzinfo=ET)
    end = datetime(2026, 3, 1, tzinfo=ET)
    assert missing_ranges([], start, end) == [(start, end)]


def test_missing_ranges_extends_backwards_only():
    existing = [bar(10), bar(20)]
    start = datetime(2026, 1, 1, tzinfo=ET)
    end = existing[-1].timestamp          # nothing newer needed
    gaps = missing_ranges(existing, start, end)

    assert gaps == [(start, existing[0].timestamp)]


def test_missing_ranges_tops_up_recent_only():
    existing = [bar(10), bar(20)]
    start = existing[0].timestamp         # nothing older needed
    end = datetime(2026, 4, 1, tzinfo=ET)
    gaps = missing_ranges(existing, start, end)

    assert gaps == [(existing[-1].timestamp, end)]


def test_missing_ranges_both_directions():
    existing = [bar(10), bar(20)]
    start = datetime(2026, 1, 1, tzinfo=ET)
    end = datetime(2026, 4, 1, tzinfo=ET)
    gaps = missing_ranges(existing, start, end)

    assert len(gaps) == 2
    assert gaps[0] == (start, existing[0].timestamp)
    assert gaps[1] == (existing[-1].timestamp, end)


def test_missing_ranges_fully_covered_fetches_nothing():
    existing = [bar(10), bar(20)]
    gaps = missing_ranges(existing, existing[0].timestamp, existing[-1].timestamp)
    assert gaps == []


def test_missing_ranges_requires_matching_awareness():
    """Cached bars are tz-aware; a naive start/end would raise TypeError on
    comparison rather than silently misbehaving."""
    existing = [bar(10)]
    with pytest.raises(TypeError):
        missing_ranges(existing, datetime(2026, 1, 1), datetime(2026, 4, 1))


# --- transient network resilience -------------------------------------------

def test_get_with_retry_recovers_from_a_transient_failure(monkeypatch):
    """A single blip killed a 3-year backfill on its fourth symbol; connection
    failures must be retried rather than ending the run."""
    import requests
    from data import twelvedata_historical_data as td

    calls = {"n": 0}

    class Resp:
        status_code = 200

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ProxyError("connection refused")
        return Resp()

    monkeypatch.setattr("requests.get", flaky)
    monkeypatch.setattr(td.time, "sleep", lambda s: None)

    assert td._get_with_retry({"symbol": "AG"}, timeout=5).status_code == 200
    assert calls["n"] == 3


def test_get_with_retry_gives_up_as_a_typed_error(monkeypatch):
    import requests
    from data import twelvedata_historical_data as td

    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("down")))
    monkeypatch.setattr(td.time, "sleep", lambda s: None)

    with pytest.raises(td.TwelveDataError, match="network error"):
        td._get_with_retry({"symbol": "AG"}, timeout=5, attempts=3)
