"""Regression test for a real bug found alongside the benchmark-trend-confirmation
work: TradingBot.evaluate_and_maybe_enter() passed the FULL multi-day accumulated
bar history (state.bars never resets at a day boundary, by design -- see
test_main_snapshot.py) straight into EvaluationContext.bars/benchmark_bars, unlike
run_overnight_review() a few methods down, which already filters to today's session
before calling evaluate_overnight_eligibility(). Left unfiltered, is_overextended()'s
"session_open = bars[0].open" and detect_opening_range_breakout_pullback()'s opening-
range slicing would silently anchor to day ONE's open/opening-range forever, not
today's -- once the bot (or a backtest) runs past a single day."""

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


def test_evaluation_context_bars_exclude_prior_days(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path)
    ticker = "AG"
    ticker_cfg = bot.config.tickers[ticker]
    benchmark = ticker_cfg.benchmark

    day1 = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)
    day2 = datetime(2026, 3, 3, 9, 30, tzinfo=timezone.utc)

    for symbol, price in ((ticker, 10.30), (benchmark, 50.30)):
        provider.push_bar(symbol, Bar(timestamp=day1, open=price - 0.3, high=price + 0.2, low=price - 0.4, close=price, volume=100_000))
        provider.push_bar(symbol, Bar(timestamp=day2, open=price - 0.3, high=price + 0.2, low=price - 0.4, close=price, volume=100_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=day2), received_at=day2)

    captured = {}

    def fake_evaluate_ticker(ctx, strategy_config, risk_config):
        captured["ctx"] = ctx
        return Signal(
            timestamp=ctx.now, ticker=ticker, benchmark=benchmark,
            price=10.30, bid=10.29, ask=10.30, spread=0.01,
            vwap=10.20, ema_9=10.20, ema_20=10.10, atr=0.15, atr_percent=1.45,
            relative_volume=1.5, opening_range_high=10.12, opening_range_low=9.95,
            benchmark_price=50.30, benchmark_vwap=50.0, benchmark_ema_9=50.2, benchmark_ema_20=50.0,
            decision=Decision.REJECTED, rejection_reason=None,
        )

    monkeypatch.setattr("main.evaluate_ticker", fake_evaluate_ticker)

    bot.evaluate_and_maybe_enter(ticker, day2)

    ctx = captured["ctx"]
    assert [b.timestamp for b in ctx.bars] == [day2]
    assert [b.timestamp for b in ctx.benchmark_bars] == [day2]
    assert ctx.benchmark_prior_close == 50.30


def test_evaluation_context_benchmark_prior_close_none_on_cold_start(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path)
    ticker = "AG"
    ticker_cfg = bot.config.tickers[ticker]
    benchmark = ticker_cfg.benchmark
    now = datetime(2026, 3, 2, 9, 55, tzinfo=timezone.utc)

    for symbol, price in ((ticker, 10.30), (benchmark, 50.30)):
        provider.push_bar(symbol, Bar(timestamp=now, open=price - 0.3, high=price + 0.2, low=price - 0.4, close=price, volume=100_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=now), received_at=now)

    captured = {}

    def fake_evaluate_ticker(ctx, strategy_config, risk_config):
        captured["ctx"] = ctx
        return Signal(
            timestamp=ctx.now, ticker=ticker, benchmark=benchmark,
            price=10.30, bid=10.29, ask=10.30, spread=0.01,
            vwap=10.20, ema_9=10.20, ema_20=10.10, atr=0.15, atr_percent=1.45,
            relative_volume=1.5, opening_range_high=10.12, opening_range_low=9.95,
            benchmark_price=50.30, benchmark_vwap=50.0, benchmark_ema_9=50.2, benchmark_ema_20=50.0,
            decision=Decision.REJECTED, rejection_reason=None,
        )

    monkeypatch.setattr("main.evaluate_ticker", fake_evaluate_ticker)

    bot.evaluate_and_maybe_enter(ticker, now)

    assert captured["ctx"].benchmark_prior_close is None
