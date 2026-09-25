"""data/ibkr_market_data.py against a fake TWS. What must hold: only CLOSED bars reach
the strategy; a quote is never invented; delayed data is reported as delayed; one
bad symbol does not stop the rest; and the pieces wire together in run_bot."""

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("ib_async")

from data.ibkr_market_data import IBKRMarketDataProvider

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def fire(self, *a):
        for h in self.handlers:
            h(*a)


class BarList(list):
    def __init__(self, *a):
        super().__init__(*a)
        self.updateEvent = _Event()


def _bars(day=24, start=(9, 30), n=4, volume=1000.0, price=18.70):
    t0 = datetime(2026, 9, day, *start, tzinfo=ET).astimezone(UTC)
    return [SimpleNamespace(date=t0 + timedelta(minutes=5 * i), open=price, high=price + 0.05,
                            low=price - 0.05, close=price + 0.01 * i, volume=volume) for i in range(n)]


class FakeIB:
    def __init__(self, stream=True, quote=(18.69, 18.71, 18.70), mdt=1, bad=()):
        self.stream = stream
        self.quote = quote
        self.mdt = mdt
        self.bad = set(bad)
        self.hist_calls = []
        self.requested_type = None
        self.slept = 0.0

    def reqMarketDataType(self, t):
        self.requested_type = t

    def reqMktData(self, contract, *a):
        bid, ask, last = self.quote
        return SimpleNamespace(bid=bid, ask=ask, last=last, marketDataType=self.mdt,
                               time=datetime(2026, 9, 24, 14, 0, 5, tzinfo=UTC))

    def reqHistoricalData(self, contract, **kw):
        self.hist_calls.append((contract.symbol, kw["keepUpToDate"]))
        if kw["keepUpToDate"] and not self.stream:
            return []
        return BarList(_bars()) if kw["keepUpToDate"] else _bars()

    def cancelHistoricalData(self, bars):
        pass

    def cancelMktData(self, c):
        pass

    def isConnected(self):
        return True

    def sleep(self, s):
        self.slept += s


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)

    def __call__(self):
        return self.t


def _contract(bad=()):
    def resolve(sym):
        if sym in bad:
            raise ValueError(f"IBKR does not recognise {sym}")
        return SimpleNamespace(symbol=sym)
    return resolve


def _provider(ib=None, bad=()):
    ib = ib or FakeIB()
    clock = Clock()
    return IBKRMarketDataProvider(ib, _contract(bad), clock=clock), ib, clock


def test_asks_for_live_where_subscribed_and_delayed_otherwise():
    p, ib, _ = _provider()
    p.subscribe(["AG"])
    assert ib.requested_type == 3


def test_only_closed_bars_reach_the_strategy():
    p, _, _ = _provider()
    p.subscribe(["AG"])
    p.poll("AG")
    bars = p.get_state("AG").bars
    assert len(bars) == 3                       # the 4th is still forming
    assert bars[0].timestamp == datetime(2026, 9, 24, 9, 30, tzinfo=ET)
    assert bars[0].timestamp.tzinfo is not None


def test_streamed_updates_are_picked_up_without_a_new_request():
    p, ib, clock = _provider()
    p.subscribe(["AG"])
    p._bars["AG"].append(_bars(start=(9, 50), n=1)[0])   # IBKR appended a new bar
    clock.t += timedelta(seconds=30)
    p._bars["AG"].updateEvent.fire(p._bars["AG"], True)
    p.poll("AG")
    assert len(p.get_state("AG").bars) == 4
    assert ib.hist_calls == [("AG", True)]
    assert p.get_state("AG").received_at == clock.t


def test_quote_comes_from_ibkr():
    p, _, _ = _provider()
    p.subscribe(["AG"])
    p.poll("AG")
    q = p.get_state("AG").quote
    assert (q.bid, q.ask, q.last) == (18.69, 18.71, 18.70)


@pytest.mark.parametrize("quote", [(math.nan, math.nan, math.nan), (-1.0, -1.0, 18.7),
                                   (0.0, 18.71, 18.7), (18.75, 18.71, 18.7)])
def test_no_valid_quote_means_no_quote_never_an_invented_one(quote):
    p, _, _ = _provider(FakeIB(quote=quote))
    p.subscribe(["AG"])
    p.poll("AG")
    assert p.get_state("AG").quote is None


def test_missing_last_uses_the_midpoint():
    p, _, _ = _provider(FakeIB(quote=(18.69, 18.71, math.nan)))
    p.subscribe(["AG"])
    p.poll("AG")
    assert p.get_state("AG").quote.last == pytest.approx(18.70)


def test_falls_back_to_one_request_per_bar_when_streaming_is_refused():
    p, ib, clock = _provider(FakeIB(stream=False))
    p.subscribe(["AG"])
    assert ib.hist_calls == [("AG", True), ("AG", False)]
    p.poll("AG")
    clock.t += timedelta(seconds=60)
    p.poll("AG")
    assert len(ib.hist_calls) == 2               # not yet a bar later
    clock.t += timedelta(seconds=240)
    p.poll("AG")
    assert ib.hist_calls[-1] == ("AG", False) and len(ib.hist_calls) == 3


@pytest.mark.parametrize("mdt,expected", [(1, False), (3, True), (4, True), (None, None)])
def test_reports_whether_prices_are_delayed(mdt, expected):
    p, _, _ = _provider(FakeIB(mdt=mdt))
    p.subscribe(["AG", "HL"])
    assert p.data_delayed() is expected


def test_one_bad_symbol_does_not_stop_the_rest():
    p, _, _ = _provider(bad=("ZZZZ",))
    p.subscribe(["AG", "ZZZZ", "HL"])
    assert list(p.failed) == ["ZZZZ"]
    p.poll("ZZZZ")                               # ignored, not a crash
    p.poll("HL")
    assert len(p.get_state("HL").bars) == 3


def test_session_volumes_by_eastern_date():
    p, _, _ = _provider()
    p.subscribe(["AG"])
    assert p.session_volumes("AG") == {"2026-09-24": 4000.0}


def test_staleness_uses_the_last_time_ibkr_sent_something():
    p, _, clock = _provider()
    p.subscribe(["AG"])
    p.poll("AG")
    now = datetime(2026, 9, 24, 14, 0, 20, tzinfo=UTC)
    assert not p.is_stale("AG", 30, now)
    assert p.is_stale("AG", 30, now + timedelta(seconds=60))


def test_wait_runs_the_ib_event_loop():
    p, ib, _ = _provider()
    p.wait(30)
    assert ib.slept == 30


# --- the bot reads it -------------------------------------------------------------

def test_the_bot_builds_a_snapshot_from_it(tmp_path):
    from config_loader import load_config
    from main import TradingBot
    from broker.paper import PaperBrokerAdapter
    from reporting.journal import SignalJournal, TradeJournal
    p, _, _ = _provider()
    p.subscribe(["AG"])
    p.poll("AG")
    bot = TradingBot(load_config(), p, PaperBrokerAdapter(5000),
                     SignalJournal(str(tmp_path / "s.jsonl")), TradeJournal(str(tmp_path / "t.jsonl")))
    snap = bot._snapshot_for("AG", datetime(2026, 9, 24, 10, 0, tzinfo=ET))
    assert snap is not None and snap.last_price == 18.70 and snap.bid == 18.69


# --- config and wiring --------------------------------------------------------------

def _config_dir(tmp_path, broker_yaml):
    import shutil
    from config_loader import load_config
    d = tmp_path / "config"
    shutil.copytree(load_config().config_dir, d)
    (d / "broker.yaml").write_text(broker_yaml)
    return str(d)


def test_ibkr_prices_need_a_paper_port_too(tmp_path):
    from config_loader import ConfigError, load_config
    d = _config_dir(tmp_path, "mode: paper\nmarket_data_source: ibkr\nibkr:\n  host: 127.0.0.1\n  port: 7496\n")
    with pytest.raises(ConfigError, match="market_data_source is 'ibkr'"):
        load_config(d)


def test_use_ibkr_paper_switches_orders_and_prices():
    from config_loader import load_config, use_ibkr_paper
    c = load_config()
    use_ibkr_paper(c)
    assert c.broker["mode"] == "ibkr_paper" and c.broker["market_data_source"] == "ibkr"


def test_use_ibkr_paper_still_refuses_a_live_port():
    from config_loader import ConfigError, load_config, use_ibkr_paper
    c = load_config()
    c.broker["ibkr"]["port"] = 7496
    with pytest.raises(ConfigError):
        use_ibkr_paper(c)


def test_orders_and_prices_share_one_tws_connection(monkeypatch):
    import ib_async
    from test_ibkr_broker import FakeIB as OrderIB
    made = []

    class Both(OrderIB, FakeIB):
        def __init__(self):
            OrderIB.__init__(self)
            FakeIB.__init__(self)
            made.append(self)

    monkeypatch.setattr(ib_async, "IB", Both)
    from config_loader import load_config, use_ibkr_paper
    from main import build_default_bot
    c = load_config()
    use_ibkr_paper(c)
    bot = build_default_bot(c)
    assert len(made) == 1 and bot.data_provider.ib is bot.broker.ib


# --- run_bot's startup report ---------------------------------------------------------

def _run_bot():
    import importlib.util, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("run_bot", os.path.join(root, "scripts", "run_bot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_startup_report_says_delayed_loudly():
    p, _, _ = _provider(FakeIB(mdt=3))
    p.subscribe(["AG"])
    text = "\n".join(_run_bot().ibkr_data_report(p))
    assert "DELAYED" in text and "plumbing" in text


def test_startup_report_says_live():
    p, _, _ = _provider(FakeIB(mdt=1))
    p.subscribe(["AG"])
    text = "\n".join(_run_bot().ibkr_data_report(p))
    assert "LIVE" in text and "DELAYED" not in text


def test_volume_scale_is_measured_on_completed_shared_sessions(monkeypatch):
    rb = _run_bot()
    p, _, _ = _provider()
    p.subscribe(["AG"])
    p._bars["AG"] = _bars(day=23, n=4, volume=780.0) + _bars(day=24, n=4, volume=5000.0)
    cached = {"2026-09-23": [SimpleNamespace(volume=1000.0)] * 4,
              "2026-09-24": [SimpleNamespace(volume=1.0)] * 4}     # today: ignored
    monkeypatch.setattr(rb, "cached_sessions", lambda sym: cached)
    assert rb.volume_scales(p, ["AG"], "2026-09-24") == {"AG": pytest.approx(0.78)}


def test_baselines_are_scaled_and_unmeasured_symbols_take_the_median():
    rb = _run_bot()
    baselines = {"AG": {0: 100.0, 1: 200.0}, "HL": {0: 50.0}, "CDE": {0: 10.0}}
    median, n = rb.apply_volume_scales(baselines, {"AG": 0.78, "HL": 0.70, "CDE": 0.90})
    assert (median, n) == (0.78, 3)
    assert baselines["AG"] == {0: pytest.approx(78.0), 1: pytest.approx(156.0)}
    b2 = {"AG": {0: 100.0}, "XX": {0: 100.0}}
    rb.apply_volume_scales(b2, {"AG": 0.5})
    assert b2["XX"][0] == pytest.approx(50.0)


def test_no_shared_session_scales_nothing_and_says_so():
    rb = _run_bot()
    baselines = {"AG": {0: 100.0}}
    assert rb.apply_volume_scales(baselines, {}) == (None, 0)
    assert baselines["AG"][0] == 100.0
    p, _, _ = _provider()
    p.subscribe(["AG"])
    assert "NOT scaled" in "\n".join(rb.ibkr_data_report(p, (None, 0)))


def test_rvol_after_scaling_matches_the_backtests_scale():
    """IBKR today = 0.78 x what Twelve Data would say. Scaled, RVOL comes out the
    same as it would have in the backtest, where both sides were Twelve Data."""
    rb = _run_bot()
    td_baseline, td_today = 1000.0, 1500.0             # true RVOL 1.5
    baselines = {"AG": {5: td_baseline}}
    rb.apply_volume_scales(baselines, {"AG": 0.78})
    assert (0.78 * td_today) / baselines["AG"][5] == pytest.approx(1.5)


def test_report_is_empty_for_other_providers():
    from data.market_data import InMemoryMarketDataProvider
    assert _run_bot().ibkr_data_report(InMemoryMarketDataProvider(), ["AG"]) == []


def test_run_bot_ibkr_paper_end_to_end(monkeypatch, capsys, tmp_path):
    """One full cycle of scripts/run_bot.py --ibkr-paper against the fake TWS, then
    Ctrl+C: it must start, report what data arrived, run, and disconnect."""
    import ib_async
    import sys
    from test_ibkr_broker import FakeIB as OrderIB
    made = []

    class Both(OrderIB, FakeIB):
        def __init__(self):
            OrderIB.__init__(self)
            FakeIB.__init__(self, mdt=3)
            made.append(self)

        def sleep(self, s):
            if s == 7:                      # the loop's poll interval: stop after one cycle
                raise KeyboardInterrupt
            FakeIB.sleep(self, s)

    monkeypatch.setattr(ib_async, "IB", Both)
    monkeypatch.setattr(sys, "argv", ["run_bot.py", "7", "--ibkr-paper"])
    import main
    from reporting.journal import SignalJournal, TradeJournal
    monkeypatch.setattr(main, "SignalJournal", lambda path: SignalJournal(str(tmp_path / "s.jsonl")))
    monkeypatch.setattr(main, "TradeJournal", lambda path: TradeJournal(str(tmp_path / "t.jsonl")))
    assert _run_bot().main() == 0
    out = capsys.readouterr().out
    assert "IBKRPaperBrokerAdapter" in out and "IBKRMarketDataProvider" in out
    assert "DELAYED" in out and "Stopped." in out
    assert made and not made[0].connected


# --- delayed data: the plumbing-test relaxations (2026-09-24: every symbol was
# skipped silently on the first delayed run) ---------------------------------------

def _delayed_no_quotes(**kw):
    ib = FakeIB(quote=(math.nan, math.nan, math.nan), mdt=3, **kw)
    clock = Clock()
    p = IBKRMarketDataProvider(ib, _contract(), clock=clock, synthetic_spread_pct=lambda s: 0.5)
    return p, ib, clock


def test_delayed_without_quotes_takes_the_quote_from_the_newest_bar():
    p, _, _ = _delayed_no_quotes()
    p.subscribe(["AG"])
    p.poll("AG")
    q = p.get_state("AG").quote
    newest = 18.70 + 0.03                               # the forming bar's close
    assert q.last == pytest.approx(newest)
    assert q.ask - q.bid == pytest.approx(newest * 0.005)
    assert "AG" in p.synthetic_quotes


def test_live_without_quotes_never_gets_one_from_bars():
    ib = FakeIB(quote=(math.nan, math.nan, math.nan), mdt=1)
    p = IBKRMarketDataProvider(ib, _contract(), clock=Clock(), synthetic_spread_pct=lambda s: 0.5)
    p.subscribe(["AG"])
    p.poll("AG")
    assert p.get_state("AG").quote is None


def test_real_delayed_quotes_are_used_when_they_arrive():
    ib = FakeIB(mdt=3)
    p = IBKRMarketDataProvider(ib, _contract(), clock=Clock(), synthetic_spread_pct=lambda s: 0.5)
    p.subscribe(["AG"])
    p.poll("AG")
    assert p.get_state("AG").quote.bid == 18.69 and not p.synthetic_quotes


def test_delayed_data_counts_as_fresh_while_connected():
    p, _, clock = _delayed_no_quotes(stream=False)      # the case that went silent
    p.subscribe(["AG"])
    clock.t += timedelta(minutes=3)
    p.poll("AG")
    assert not p.is_stale("AG", 30, clock.t + timedelta(seconds=5))


def test_live_data_staleness_stays_strict():
    p, _, clock = _provider(FakeIB(stream=False, mdt=1))
    p.subscribe(["AG"])
    clock.t += timedelta(minutes=3)
    p.poll("AG")
    assert p.is_stale("AG", 30, clock.t)


def test_health_counts_what_the_bot_can_use():
    p, _, clock = _delayed_no_quotes()
    p.subscribe(["AG", "HL"])
    for s in ("AG", "HL"):
        p.poll(s)
    h = p.health(["AG", "HL"], clock.t, 30)
    assert h == {"symbols": 2, "streaming": 2, "bars_today": 2, "quotes": 0,
                 "quotes_from_bars": 2, "fresh": 2}
    line = _run_bot().health_line(p, ["AG", "HL"], clock.t, 30)
    assert "quotes 0/2 + 2 from bars" in line and "fresh 2/2" in line


def test_the_bot_evaluates_symbols_on_delayed_data(tmp_path):
    """The failure itself: with delayed data and no delayed quotes, the bot must
    now reach the strategy and log a decision, not skip silently."""
    from config_loader import load_config
    from main import TradingBot
    from broker.paper import PaperBrokerAdapter
    from reporting.journal import SignalJournal, TradeJournal
    config = load_config()
    t = config.auto_tradeable_universe()[0]
    bench = config.benchmark_of(t)
    ib = FakeIB(quote=(math.nan, math.nan, math.nan), mdt=3, stream=False)
    clock = Clock()
    p = IBKRMarketDataProvider(ib, _contract(), clock=clock,
                               synthetic_spread_pct=lambda s: config.max_spread_pct(s) * 0.3)
    p.subscribe([t, bench])
    clock.t += timedelta(minutes=3)
    for s in (t, bench):
        p.poll(s)
    bot = TradingBot(config, p, PaperBrokerAdapter(5000),
                     SignalJournal(str(tmp_path / "s.jsonl")), TradeJournal(str(tmp_path / "t.jsonl")))
    bot.evaluate_and_maybe_enter(t, datetime(2026, 9, 24, 10, 3, tzinfo=ET))
    assert len(bot.signal_journal.signals) == 1


class PerSymbolIB(FakeIB):
    """Like TWS on 2026-09-25 with no subscription: IBKR marked ONE symbol delayed and
    sent no quote for any. ib_async leaves the rest at its default, 1 (live)."""

    def reqMktData(self, contract, *a):
        mdt = 3 if contract.symbol == "AG" else 1
        return SimpleNamespace(bid=math.nan, ask=math.nan, last=math.nan, marketDataType=mdt, time=None)


def test_one_symbol_marked_delayed_makes_the_whole_feed_delayed():
    ib = PerSymbolIB()
    clock = Clock()
    p = IBKRMarketDataProvider(ib, _contract(), clock=clock, synthetic_spread_pct=lambda s: 0.5)
    syms = ["AG", "HL", "CDE"]
    p.subscribe(syms)
    for s in syms:
        p.poll(s)
    h = p.health(syms, clock.t, 30)
    assert h["quotes_from_bars"] == 3 and h["fresh"] == 3


def test_live_quotes_anywhere_mean_the_feed_is_live():
    """With a subscription, one unsubscribed symbol reporting delayed must not relax
    the rules for the whole feed."""
    class Mixed(FakeIB):
        def reqMktData(self, contract, *a):
            if contract.symbol == "ZZZ":
                return SimpleNamespace(bid=math.nan, ask=math.nan, last=math.nan, marketDataType=3, time=None)
            return FakeIB.reqMktData(self, contract)
    p = IBKRMarketDataProvider(Mixed(mdt=1), _contract(), clock=Clock(), synthetic_spread_pct=lambda s: 0.5)
    p.subscribe(["AG", "ZZZ"])
    p.poll("ZZZ")
    assert p.data_delayed() is False
    assert p.get_state("ZZZ").quote is None
