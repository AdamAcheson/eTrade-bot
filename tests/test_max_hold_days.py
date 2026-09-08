"""Tests for the experimental hard max-hold-days force-exit (risk.yaml:
safety.max_hold_days) added alongside the wider-stop/flat-dollar-risk experiment --
see main.py's manage_open_positions and config/risk.yaml."""

from datetime import datetime, timedelta, timezone

from broker.paper import PaperBrokerAdapter
from config_loader import load_config
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from models.signal import Decision, Signal
from models.trade import ExitReason
from reporting.journal import SignalJournal, TradeJournal


def make_bot(tmp_path):
    config = load_config()
    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(starting_equity=100_000)
    sj = SignalJournal(str(tmp_path / "signals.jsonl"))
    tj = TradeJournal(str(tmp_path / "trades.jsonl"))
    return TradingBot(config, provider, broker, sj, tj), provider


def open_a_position(bot, provider, monkeypatch, ticker, benchmark, entry_time):
    for symbol, price in ((ticker, 10.30), (benchmark, 50.30)):
        provider.push_bar(symbol, Bar(timestamp=entry_time, open=price - 0.3, high=price + 0.2, low=price - 0.4, close=price, volume=100_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=entry_time), received_at=entry_time)

    fake_signal = Signal(
        timestamp=entry_time, ticker=ticker, benchmark=benchmark,
        price=10.30, bid=10.29, ask=10.30, spread=0.01,
        vwap=10.20, ema_9=10.20, ema_20=10.10, atr=0.15, atr_percent=1.45,
        relative_volume=1.5, opening_range_high=10.12, opening_range_low=9.95,
        benchmark_price=50.30, benchmark_vwap=50.0, benchmark_ema_9=50.2, benchmark_ema_20=50.0,
        setup_type="ORB_PULLBACK_CONTINUATION", setup_score=85.0,
        entry_candidate=True, entry_price=10.30, stop=9.50, target=12.00,
        risk_per_share=0.80, expected_reward=1.70, r_ratio=2.13,
        decision=Decision.ENTRY_CANDIDATE, rejection_reason=None,
    )
    monkeypatch.setattr("main.evaluate_ticker", lambda *a, **k: fake_signal)
    bot.evaluate_and_maybe_enter(ticker, entry_time)
    assert bot.position_manager.has_open_position(ticker)


def test_position_force_closed_after_max_hold_days(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path)
    bot.config.risk["safety"]["max_hold_days"] = 3
    ticker, benchmark = "AG", "SIL"
    entry_time = datetime(2026, 3, 2, 9, 55, tzinfo=timezone.utc)

    open_a_position(bot, provider, monkeypatch, ticker, benchmark, entry_time)

    later = entry_time + timedelta(days=3)
    for symbol, price in ((ticker, 10.40), (benchmark, 50.40)):
        provider.push_bar(symbol, Bar(timestamp=later, open=price - 0.1, high=price + 0.1, low=price - 0.2, close=price, volume=50_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=later), received_at=later)

    bot.manage_open_positions(later)

    assert not bot.position_manager.has_open_position(ticker)
    trades = bot.trade_journal.trades
    assert len(trades) == 1
    assert trades[0].exit_reason == ExitReason.MAX_HOLD_EXCEEDED


def test_position_not_closed_before_max_hold_days(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path)
    bot.config.risk["safety"]["max_hold_days"] = 3
    ticker, benchmark = "AG", "SIL"
    entry_time = datetime(2026, 3, 2, 9, 55, tzinfo=timezone.utc)

    open_a_position(bot, provider, monkeypatch, ticker, benchmark, entry_time)

    sooner = entry_time + timedelta(days=1)
    for symbol, price in ((ticker, 10.40), (benchmark, 50.40)):
        provider.push_bar(symbol, Bar(timestamp=sooner, open=price - 0.1, high=price + 0.1, low=price - 0.2, close=price, volume=50_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=sooner), received_at=sooner)

    bot.manage_open_positions(sooner)

    assert bot.position_manager.has_open_position(ticker)
    assert len(bot.trade_journal.trades) == 0


def test_max_hold_days_disabled_by_default_none(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path)
    bot.config.risk["safety"]["max_hold_days"] = None
    ticker, benchmark = "AG", "SIL"
    entry_time = datetime(2026, 3, 2, 9, 55, tzinfo=timezone.utc)

    open_a_position(bot, provider, monkeypatch, ticker, benchmark, entry_time)

    much_later = entry_time + timedelta(days=30)
    for symbol, price in ((ticker, 10.40), (benchmark, 50.40)):
        provider.push_bar(symbol, Bar(timestamp=much_later, open=price - 0.1, high=price + 0.1, low=price - 0.2, close=price, volume=50_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=much_later), received_at=much_later)

    bot.manage_open_positions(much_later)

    assert bot.position_manager.has_open_position(ticker)
