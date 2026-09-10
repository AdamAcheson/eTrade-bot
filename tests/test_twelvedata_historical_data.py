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


# --- windows that predate a symbol's listing --------------------------------

class _Resp:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


NO_DATA = {
    "code": 400,
    "message": "No data is available on the specified dates. Try setting different start/end dates.",
    "status": "error",
}


def test_no_data_available_as_http_400_is_an_empty_window_not_an_error(monkeypatch):
    """CRML listed during 2024, so its first 2023 chunk came back 400 with this
    message and aborted the whole symbol -- discarding the two years that do exist.
    The message was already handled, but only on a 200; the status code was checked
    first."""
    from data import twelvedata_historical_data as td

    monkeypatch.setattr(td, "_get_with_retry", lambda *a, **k: _Resp(400, NO_DATA))
    assert td._fetch_window("CRML", datetime(2023, 9, 6), datetime(2023, 12, 6),
                            "key", "5min", 5.0) == []


def test_no_data_available_as_http_200_still_works(monkeypatch):
    from data import twelvedata_historical_data as td

    monkeypatch.setattr(td, "_get_with_retry", lambda *a, **k: _Resp(200, dict(NO_DATA, code=200)))
    assert td._fetch_window("CRML", datetime(2023, 9, 6), datetime(2023, 12, 6),
                            "key", "5min", 5.0) == []


def test_a_real_http_error_still_raises(monkeypatch):
    from data import twelvedata_historical_data as td

    monkeypatch.setattr(td, "_get_with_retry",
                        lambda *a, **k: _Resp(401, {"message": "Invalid API key"}, "unauthorized"))
    with pytest.raises(td.TwelveDataError, match="HTTP 401"):
        td._fetch_window("AG", datetime(2024, 1, 1), datetime(2024, 2, 1), "key", "5min", 5.0)


def test_a_non_json_error_body_still_raises(monkeypatch):
    """A Cloudflare outage returns HTML, not JSON -- parsing must not mask it."""
    from data import twelvedata_historical_data as td

    monkeypatch.setattr(td, "_get_with_retry",
                        lambda *a, **k: _Resp(521, None, "<!DOCTYPE html>"))
    with pytest.raises(td.TwelveDataError, match="HTTP 521"):
        td._fetch_window("FCX", datetime(2024, 1, 1), datetime(2024, 2, 1), "key", "5min", 5.0)
