"""Tests for the Yahoo Finance historical-bar loader used by scripts/backtest.py.
No real network calls -- requests.get is monkeypatched with a canned response
shaped like Yahoo's actual chart API (see data/historical_data.py's docstring)."""

from datetime import time as dtime

import pytest

from data.historical_data import fetch_yahoo_bars, group_bars_by_day, load_bars_csv, save_bars_csv
from models.bar import Bar


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def canned_chart_response(timestamps, opens, highs, lows, closes, volumes):
    return {
        "chart": {
            "result": [{
                "meta": {"symbol": "AG", "timezone": "EDT", "gmtoffset": -14400},
                "timestamp": timestamps,
                "indicators": {"quote": [{"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}]},
            }],
            "error": None,
        }
    }


def test_fetch_yahoo_bars_filters_regular_hours_and_drops_nulls(monkeypatch):
    # 1788442200 = 2026-09-02 09:30:00 America/New_York (regular hours)
    # 1788414600 = 2026-09-02 02:00:00 America/New_York (pre-market -- should be dropped)
    timestamps = [1788414600, 1788442200, 1788442500, 1788442800]
    opens = [9.0, 10.00, 10.05, None]  # last bar has a null OHLC -> dropped
    highs = [9.1, 10.10, 10.12, None]
    lows = [8.9, 9.95, 10.00, None]
    closes = [9.05, 10.05, 10.08, None]
    volumes = [1000, 100000, 90000, None]

    payload = canned_chart_response(timestamps, opens, highs, lows, closes, volumes)
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse(200, payload))

    bars = fetch_yahoo_bars("AG")

    assert len(bars) == 2
    assert bars[0].open == 10.00
    assert bars[0].timestamp.time() == dtime(9, 30)
    assert bars[1].close == 10.08


def test_fetch_yahoo_bars_raises_on_error(monkeypatch):
    payload = {"chart": {"result": None, "error": {"code": "Not Found", "description": "no data"}}}
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse(200, payload))

    with pytest.raises(Exception):
        fetch_yahoo_bars("BOGUS")


def test_save_and_load_bars_csv_roundtrip(tmp_path):
    from datetime import datetime, timezone
    bars = [
        Bar(timestamp=datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc), open=10.0, high=10.5, low=9.9, close=10.2, volume=1000.0),
        Bar(timestamp=datetime(2026, 3, 2, 9, 35, tzinfo=timezone.utc), open=10.2, high=10.6, low=10.1, close=10.4, volume=1100.0),
    ]
    path = str(tmp_path / "AG.csv")
    save_bars_csv(bars, path)
    loaded = load_bars_csv(path)

    assert len(loaded) == 2
    assert loaded[0].open == 10.0
    assert loaded[1].close == 10.4


def test_group_bars_by_day(tmp_path):
    from datetime import datetime, timezone
    bars = [
        Bar(timestamp=datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc), open=1, high=1, low=1, close=1, volume=1),
        Bar(timestamp=datetime(2026, 3, 2, 9, 35, tzinfo=timezone.utc), open=1, high=1, low=1, close=1, volume=1),
        Bar(timestamp=datetime(2026, 3, 3, 9, 30, tzinfo=timezone.utc), open=1, high=1, low=1, close=1, volume=1),
    ]
    by_day = group_bars_by_day(bars)
    assert set(by_day.keys()) == {"2026-03-02", "2026-03-03"}
    assert len(by_day["2026-03-02"]) == 2
    assert len(by_day["2026-03-03"]) == 1
