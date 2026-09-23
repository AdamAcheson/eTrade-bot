"""scripts/ibkr_check.py is run by the user against their own TWS, so it has to be
right the first time and must never be able to trade. These drive it with a fake
IB object standing in for TWS."""

import importlib.util
import os
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("ibkr_check", os.path.join(ROOT, "scripts", "ibkr_check.py"))
ibkr_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ibkr_check)

pytest.importorskip("ib_async")


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def fire(self, *args):
        for h in self.handlers:
            h(*args)


class FakeIB:
    """Only the read-only methods the script is allowed to use. Any order method
    is absent, so calling one would raise AttributeError and fail the test."""

    def __init__(self, *, connect_error=None, accounts=("DU1234567",), quote=(10.0, 10.02, 10.01),
                 bars=True, api_refused=False, delivered_type=None):
        self.errorEvent = _Event()
        self.connect_error = connect_error
        self._accounts = list(accounts)
        self.quote = quote
        self._bars = bars
        self.api_refused = api_refused
        self.delivered_type = delivered_type
        self.connect_kwargs = None
        self.disconnected = False

    def connect(self, host, port, **kw):
        self.connect_kwargs = dict(host=host, port=port, **kw)
        if self.connect_error:
            raise self.connect_error
        if self.api_refused:
            self.errorEvent.fire(-1, 10000, "API support is not available for this account", None)

    def managedAccounts(self):
        return [] if self.api_refused else self._accounts

    def accountSummary(self):
        return [SimpleNamespace(tag="NetLiquidation", value="1000000", currency="USD"),
                SimpleNamespace(tag="AccountType", value="INDIVIDUAL", currency="")]

    def qualifyContracts(self, c):
        return [SimpleNamespace(conId=42, primaryExchange="NYSE", exchange="SMART")]

    def reqMarketDataType(self, t):
        self.mdt = t

    def reqMktData(self, contract, *a):
        bid, ask, last = self.quote
        # with TWS auto-fallback on, a live request can come back delayed
        return SimpleNamespace(bid=bid, ask=ask, last=last,
                               marketDataType=self.delivered_type or self.mdt)

    def cancelMktData(self, contract):
        pass

    def sleep(self, s):
        pass

    def reqHistoricalData(self, contract, **kw):
        self.hist_kwargs = kw
        if not self._bars:
            return []
        return [SimpleNamespace(date="2026-09-23 15:55", open=1, high=2, low=0.5, close=1.5, volume=100)]

    def disconnect(self):
        self.disconnected = True


def _run(ib, port=7497):
    lines = []
    code = ibkr_check.run(ib, "127.0.0.1", port, 17, "AG", out=lines.append)
    return code, "\n".join(lines)


def test_connects_read_only():
    ib = FakeIB()
    _run(ib)
    assert ib.connect_kwargs["readonly"] is True


def test_happy_path_passes_and_disconnects():
    ib = FakeIB()
    code, text = _run(ib)
    assert code == 0
    assert "DU1234567: looks paper" in text
    assert "all checks passed" in text
    assert ib.disconnected


def test_asks_for_the_bars_the_strategy_uses():
    ib = FakeIB()
    _run(ib)
    assert ib.hist_kwargs["barSizeSetting"] == "5 mins"
    assert ib.hist_kwargs["useRTH"] is True


def test_connection_refused_explains_what_to_check():
    code, text = _run(FakeIB(connect_error=ConnectionRefusedError("refused")))
    assert code == 2
    assert "Enable ActiveX and Socket Clients" in text


def test_api_refusal_is_reported_verbatim():
    # this is what a Lite-account refusal is expected to look like
    code, text = _run(FakeIB(api_refused=True))
    assert code != 0
    assert "API support is not available" in text
    assert "no accounts returned" in text


def test_live_account_id_is_flagged():
    _, text = _run(FakeIB(accounts=("U7654321",)))
    assert "LIVE" in text


def test_live_port_warns():
    _, text = _run(FakeIB(), port=7496)
    assert "LIVE-trading port" in text


def test_nan_quote_counts_as_no_quote():
    nan = float("nan")
    code, text = _run(FakeIB(quote=(nan, nan, nan)))
    assert code == 1
    assert "no quote at all" in text


def test_missing_bars_fail():
    code, text = _run(FakeIB(bars=False))
    assert code == 1
    assert "no bars returned" in text


@pytest.mark.parametrize("acct,paper", [("DU123", True), ("du999", True), ("U123", False), ("", False)])
def test_looks_like_paper(acct, paper):
    assert ibkr_check.looks_like_paper(acct) is paper


def test_live_request_answered_with_delayed_data_is_called_delayed():
    # TWS "Auto-fallback to delayed market data" ticked, no live subscription
    code, text = _run(FakeIB(delivered_type=3))
    assert "asked for live, received delayed" in text
    assert "Only DELAYED data is available" in text


def test_live_data_is_reported_as_live():
    _, text = _run(FakeIB())
    assert "asked for live, received live" in text
    assert "Only DELAYED" not in text


def test_frozen_after_close_is_not_mistaken_for_delayed():
    _, text = _run(FakeIB(delivered_type=2))
    assert "received frozen" in text
    assert "Only DELAYED" not in text
