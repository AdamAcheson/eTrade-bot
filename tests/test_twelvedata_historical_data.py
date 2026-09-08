"""Tests for the Twelve Data historical-bar loader (deeper history than Yahoo's
60-day cap -- see src/data/twelvedata_historical_data.py). No real network calls --
requests.get is monkeypatched with responses shaped like Twelve Data's actual API."""

from datetime import datetime

import pytest

from data.twelvedata_historical_data import TwelveDataError, fetch_twelvedata_bars


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def ok_payload(rows):
    return {
        "meta": {"symbol": "AG", "interval": "5min", "exchange": "NYSE"},
        "values": rows,
        "status": "ok",
    }


def test_fetch_twelvedata_bars_parses_rows(monkeypatch):
    payload = ok_payload([
        {"datetime": "2026-03-02 09:35:00", "open": "10.10", "high": "10.20", "low": "10.05", "close": "10.15", "volume": "90000"},
        {"datetime": "2026-03-02 09:30:00", "open": "10.00", "high": "10.10", "low": "9.95", "close": "10.05", "volume": "100000"},
    ])
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse(200, payload))

    bars = fetch_twelvedata_bars("AG", datetime(2026, 3, 2), datetime(2026, 3, 3), api_key="fake-key", pause_seconds=0)

    # Sorted chronologically even though the canned payload is newest-first (as
    # Twelve Data's real API returns them).
    assert len(bars) == 2
    assert bars[0].timestamp.time().isoformat() == "09:30:00"
    assert bars[0].open == 10.00
    assert bars[1].close == 10.15
    assert bars[0].timestamp.tzinfo is not None


def test_fetch_twelvedata_bars_treats_no_data_as_empty_not_error(monkeypatch):
    payload = {"code": 400, "status": "error", "message": "No data is available on the specified dates."}
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse(200, payload))

    bars = fetch_twelvedata_bars("AG", datetime(2026, 3, 1), datetime(2026, 3, 2), api_key="fake-key", pause_seconds=0)
    assert bars == []


def test_fetch_twelvedata_bars_raises_on_real_error(monkeypatch):
    payload = {"code": 429, "status": "error", "message": "You have run out of API credits for the current minute."}
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse(200, payload))

    with pytest.raises(TwelveDataError, match="credits"):
        fetch_twelvedata_bars("AG", datetime(2026, 3, 1), datetime(2026, 3, 2), api_key="fake-key", pause_seconds=0)


def test_fetch_twelvedata_bars_requires_api_key(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    with pytest.raises(TwelveDataError):
        fetch_twelvedata_bars("AG", datetime(2026, 3, 1), datetime(2026, 3, 2))


def test_fetch_twelvedata_bars_chunks_long_ranges(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((params["start_date"], params["end_date"]))
        return FakeResponse(200, ok_payload([]))

    monkeypatch.setattr("requests.get", fake_get)

    fetch_twelvedata_bars(
        "AG", datetime(2026, 1, 1), datetime(2026, 4, 1),
        api_key="fake-key", chunk_days=30, pause_seconds=0,
    )

    # ~90 days at chunk_days=30 -> 3 requests, not one.
    assert len(calls) == 3
