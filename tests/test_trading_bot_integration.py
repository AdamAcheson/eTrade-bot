"""End-to-end TradingBot.run_cycle test: drives the full pipeline (data -> signal
engine -> risk -> order manager -> PaperBrokerAdapter -> PositionManager) through
InMemoryMarketDataProvider, the same path a backtest driver uses.

Regression coverage for a real wiring gap: evaluate_and_maybe_enter previously
called order_manager.submit_entry_order() and stopped -- nothing ever fed the
resulting quote back to PaperBrokerAdapter or called
PositionManager.open_position(), so a filled entry order could never become an
open Position. manage_open_positions() and run_overnight_review() therefore had
nothing to act on, and no trade could ever be recorded, even though the signal
engine, risk manager, and order manager all reported success in isolation.

The signal engine itself (eligibility/chase-rule/setup detection thresholds) is
already covered by tests/test_signals.py, so here it's stubbed with a fixed
ENTRY_CANDIDATE Signal -- these tests are about what TradingBot does with a
signal it already decided to act on, not about re-deriving one from scratch."""

from datetime import datetime

import main as main_module
from broker.paper import PaperBrokerAdapter
from config_loader import load_config
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from models.signal import Decision, Signal
from models.trade import TradeState
from reporting.journal import SignalJournal, TradeJournal


def make_bot(tmp_path):
    config = load_config()
    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(starting_equity=100_000)
    signal_journal = SignalJournal(str(tmp_path / "signals.jsonl"))
    trade_journal = TradeJournal(str(tmp_path / "trades.jsonl"))
    return TradingBot(config, provider, broker, signal_journal, trade_journal)


def push_minimal_session(bot, ticker: str, price: float, now: datetime):
    """Just enough state for _snapshot_for() to return non-None and not-stale --
    the signal engine itself is stubbed out in these tests, so the indicator
    values computed from this one bar are never used for a real eligibility
    decision."""
    bot.data_provider.push_bar(
        ticker,
        Bar(timestamp=now, open=price, high=price, low=price, close=price, volume=100_000),
        received_at=now,
    )
    bot.data_provider.push_quote(
        ticker, Quote(bid=price - 0.01, ask=price, last=price, timestamp=now), received_at=now
    )


def make_entry_signal(now: datetime, entry_price: float, stop: float, target: float) -> Signal:
    return Signal(
        timestamp=now,
        ticker="AG",
        benchmark="SIL",
        price=entry_price,
        bid=entry_price - 0.01,
        ask=entry_price,
        spread=0.01,
        vwap=entry_price - 0.1,
        ema_9=entry_price - 0.1,
        ema_20=entry_price - 0.2,
        atr=0.15,
        atr_percent=1.4,
        relative_volume=1.5,
        opening_range_high=entry_price - 0.2,
        opening_range_low=entry_price - 0.4,
        benchmark_price=50.5,
        benchmark_vwap=50.0,
        benchmark_ema_9=50.2,
        benchmark_ema_20=50.0,
        setup_type="ORB_PULLBACK_CONTINUATION",
        setup_score=85.0,
        entry_candidate=True,
        entry_price=entry_price,
        stop=stop,
        target=target,
        risk_per_share=entry_price - stop,
        expected_reward=target - entry_price,
        r_ratio=(target - entry_price) / (entry_price - stop),
        decision=Decision.ENTRY_CANDIDATE,
    )


def test_evaluate_and_maybe_enter_opens_a_position_on_entry_candidate(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    now = datetime(2026, 3, 2, 9, 55)

    push_minimal_session(bot, "AG", price=10.40, now=now)
    push_minimal_session(bot, "SIL", price=50.50, now=now)

    signal = make_entry_signal(now, entry_price=10.40, stop=10.20, target=10.70)
    monkeypatch.setattr(main_module, "evaluate_ticker", lambda ctx, strat, risk: signal)

    bot.evaluate_and_maybe_enter("AG", now)

    assert bot.position_manager.has_open_position("AG")
    position = bot.position_manager.get_position("AG")
    assert position.shares > 0
    assert position.entry_price == 10.40
    assert position.current_stop == 10.20
    assert bot.broker.get_positions()["AG"].quantity == position.shares


def test_open_position_is_managed_and_closed_on_stop(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    now = datetime(2026, 3, 2, 9, 55)

    push_minimal_session(bot, "AG", price=10.40, now=now)
    push_minimal_session(bot, "SIL", price=50.50, now=now)

    signal = make_entry_signal(now, entry_price=10.40, stop=10.20, target=10.70)
    monkeypatch.setattr(main_module, "evaluate_ticker", lambda ctx, strat, risk: signal)
    bot.evaluate_and_maybe_enter("AG", now)
    assert bot.position_manager.has_open_position("AG")

    later = datetime(2026, 3, 2, 10, 0)
    bot.data_provider.push_bar(
        "AG", Bar(timestamp=later, open=10.20, high=10.20, low=10.19, close=10.19, volume=50_000), received_at=later
    )
    bot.data_provider.push_quote(
        "AG", Quote(bid=10.19, ask=10.20, last=10.19, timestamp=later), received_at=later
    )
    bot.manage_open_positions(later)

    assert not bot.position_manager.has_open_position("AG")
    assert bot.position_manager.get_state("AG") == TradeState.WATCHING
    assert len(bot.trade_journal.trades) == 1
    trade = bot.trade_journal.trades[0]
    assert trade.exit_reason.value == "STOP_HIT"
    assert trade.net_profit < 0
    assert "AG" not in bot.broker.get_positions()
