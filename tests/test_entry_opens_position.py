"""Regression test for a real, previously-undetected bug: TradingBot.evaluate_and_
maybe_enter() submitted entry orders but discarded the result -- nothing ever called
PositionManager.open_position(), so a filled (even simulated paper-filled) order
never became a tracked position. Confirmed against a real 60-day historical
backtest: real ENTRY_CANDIDATE signals fired but zero trades ever closed, because
no position was ever opened for the broker to later manage/exit.

This isolates exactly the fixed logic (signal -> order -> fill -> open_position) by
stubbing the signal engine itself, rather than trying to satisfy every indicator
warm-up requirement needed to produce a real ENTRY_CANDIDATE end-to-end (the full
backtest run already covers that path with real data)."""

from datetime import datetime, timezone

from broker.paper import PaperBrokerAdapter
from config_loader import load_config
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from models.signal import Decision, Signal
from reporting.journal import SignalJournal, TradeJournal


def make_bot(tmp_path):
    config = load_config()
    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(starting_equity=100_000)
    sj = SignalJournal(str(tmp_path / "signals.jsonl"))
    tj = TradeJournal(str(tmp_path / "trades.jsonl"))
    return TradingBot(config, provider, broker, sj, tj), provider


def test_entry_candidate_signal_actually_opens_a_position(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path)
    ticker = "AG"
    ticker_cfg = bot.config.tickers[ticker]
    benchmark = ticker_cfg.benchmark
    now = datetime(2026, 3, 2, 9, 55, tzinfo=timezone.utc)

    # Minimal bars/quote so _snapshot_for doesn't return None (real indicator
    # warm-up is irrelevant here since evaluate_ticker is stubbed below).
    for symbol, price in ((ticker, 10.30), (benchmark, 50.30)):
        provider.push_bar(symbol, Bar(timestamp=now, open=price - 0.3, high=price + 0.2, low=price - 0.4, close=price, volume=100_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=now), received_at=now)

    fake_signal = Signal(
        timestamp=now, ticker=ticker, benchmark=benchmark,
        price=10.30, bid=10.29, ask=10.30, spread=0.01,
        vwap=10.20, ema_9=10.20, ema_20=10.10, atr=0.15, atr_percent=1.45,
        relative_volume=1.5, opening_range_high=10.12, opening_range_low=9.95,
        benchmark_price=50.30, benchmark_vwap=50.0, benchmark_ema_9=50.2, benchmark_ema_20=50.0,
        setup_type="ORB_PULLBACK_CONTINUATION", setup_score=85.0,
        entry_candidate=True, entry_price=10.30, stop=10.10, target=10.70,
        risk_per_share=0.20, expected_reward=0.40, r_ratio=2.0,
        decision=Decision.ENTRY_CANDIDATE, rejection_reason=None,
    )
    monkeypatch.setattr("main.evaluate_ticker", lambda *a, **k: fake_signal)

    assert not bot.position_manager.has_open_position(ticker)

    bot.evaluate_and_maybe_enter(ticker, now)

    assert bot.position_manager.has_open_position(ticker)
    position = bot.position_manager.get_position(ticker)
    assert position.shares > 0
    assert position.entry_price > 0
    assert position.initial_stop == 10.10
    assert position.initial_target == 10.70

    # And the broker side actually reflects a filled order, not just local bookkeeping.
    broker_positions = bot.broker.get_positions()
    assert ticker in broker_positions
    assert broker_positions[ticker].quantity == position.shares
