"""The live account is cash-only with no borrowing: every open position together must
stay within the account's value. The IBKR paper account reports $20,000 of buying
power on $5,000, so the broker will not enforce this for us -- the bot has to."""

from datetime import datetime, timezone

import pytest

from broker.paper import PaperBrokerAdapter
from config_loader import load_config
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from models.signal import Decision, Signal
from reporting.journal import SignalJournal, TradeJournal
from models.trade import ExitReason
from risk.position_sizing import cap_shares_to_exposure, cap_shares_to_settled_cash


# --- the pure function ---------------------------------------------------------

def test_fits_untouched_when_there_is_room():
    assert cap_shares_to_exposure(242, 10.30, 0.0, 5000, 1.0) == 242


def test_trims_to_what_is_left():
    # $2,500 already open on a $4,000 account leaves $1,500 -> 145 shares at $10.30
    assert cap_shares_to_exposure(242, 10.30, 2500.0, 4000, 1.0) == 145


def test_nothing_left_means_no_trade():
    assert cap_shares_to_exposure(242, 10.30, 5000.0, 5000, 1.0) == 0


def test_over_exposed_already_means_no_trade():
    # e.g. open positions bought when equity was higher, then a losing day
    assert cap_shares_to_exposure(100, 10.0, 5200.0, 5000, 1.0) == 0


def test_none_disables_the_check():
    assert cap_shares_to_exposure(242, 10.30, 9999.0, 100, None) == 242


def test_bad_price_is_no_trade():
    assert cap_shares_to_exposure(100, 0.0, 0.0, 5000, 1.0) == 0


# --- the shipped decision --------------------------------------------------------

def test_shipped_config_is_the_chosen_cash_account_setup():
    """Pins the configuration chosen 2026-09-23 for a $5,000 cash account:
    $2,500 per trade, two at a time, never more than the account is worth."""
    c = load_config()
    assert c.risk["sizing"]["max_position_size_dollars"] == 2500
    assert c.risk["sizing"]["max_position_size_pct_equity"] == 0.5
    assert c.risk["sizing"]["max_total_exposure_pct_equity"] == 1.0
    assert c.risk["safety"]["max_position_size_dollars"] == 2500
    assert c.risk["behavior"]["max_concurrent_positions"] == 2
    assert c.risk["sizing"]["max_daily_purchases_pct_equity"] == 1.0
    assert c.risk["sizing"]["min_trimmed_fraction"] == 0.5


def test_two_full_positions_fit_the_account_exactly():
    c = load_config()
    per_trade = c.risk["sizing"]["max_position_size_dollars"]
    slots = c.risk["behavior"]["max_concurrent_positions"]
    assert per_trade * slots <= 5000
    assert c.risk["sizing"]["max_position_size_pct_equity"] * slots <= \
        c.risk["sizing"]["max_total_exposure_pct_equity"]


# --- through the real entry path --------------------------------------------------

def _bot(tmp_path, equity, pct_equity=None):
    config = load_config()
    if pct_equity is not None:
        config.risk["sizing"]["max_position_size_pct_equity"] = pct_equity
    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(starting_equity=equity)
    bot = TradingBot(config, provider, broker,
                     SignalJournal(str(tmp_path / "s.jsonl")), TradeJournal(str(tmp_path / "t.jsonl")))
    return bot, provider


def _enter(bot, provider, monkeypatch, ticker, now, price=10.30, stop_distance=0.055):
    # 0.055 on $10.30 is ~0.53%, the median stop distance in the holdout. A wider
    # stop lets the 0.75% risk budget bind first and the dollar caps never engage.
    benchmark = bot.config.tickers[ticker].benchmark
    for symbol, p in ((ticker, price), (benchmark, 50.30)):
        provider.push_bar(symbol, Bar(timestamp=now, open=p - 0.3, high=p + 0.2, low=p - 0.4, close=p, volume=100_000))
        provider.push_quote(symbol, Quote(bid=p - 0.01, ask=p, last=p, timestamp=now), received_at=now)
    sig = Signal(
        timestamp=now, ticker=ticker, benchmark=benchmark,
        price=price, bid=price - 0.01, ask=price, spread=0.01,
        vwap=price - 0.1, ema_9=price - 0.1, ema_20=price - 0.2, atr=0.15, atr_percent=1.45,
        relative_volume=1.5, opening_range_high=price - 0.2, opening_range_low=price - 0.4,
        benchmark_price=50.30, benchmark_vwap=50.0, benchmark_ema_9=50.2, benchmark_ema_20=50.0,
        setup_type="VWAP_RECLAIM", setup_score=85.0,
        entry_candidate=True, entry_price=price, stop=price - stop_distance,
        target=price + 2 * stop_distance,
        risk_per_share=stop_distance, expected_reward=2 * stop_distance, r_ratio=2.0,
        decision=Decision.ENTRY_CANDIDATE, rejection_reason=None,
    )
    monkeypatch.setattr("main.evaluate_ticker", lambda *a, **k: sig)
    bot.evaluate_and_maybe_enter(ticker, now)


def _two_tickers(bot):
    names = [t for t in bot.config.tickers if bot.config.tickers[t].benchmark]
    return names[0], names[1]


def test_second_position_is_trimmed_to_what_the_account_can_afford(tmp_path, monkeypatch):
    # Loosen the percentage cap so ONLY the exposure cap can stop the overshoot:
    # $2,500 + $2,500 on a $4,000 account must become $2,500 + ~$1,500.
    bot, provider = _bot(tmp_path, equity=4000, pct_equity=1.0)
    a, b = _two_tickers(bot)
    now = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)
    _enter(bot, provider, monkeypatch, a, now)
    _enter(bot, provider, monkeypatch, b, now)
    pa, pb = bot.position_manager.get_position(a), bot.position_manager.get_position(b)
    assert pa is not None and pb is not None
    assert pb.shares < pa.shares
    assert bot.position_manager.open_notional() <= 4000 + 1e-6


def test_shipped_setup_puts_two_full_positions_on_five_thousand(tmp_path, monkeypatch):
    bot, provider = _bot(tmp_path, equity=5000)
    a, b = _two_tickers(bot)
    now = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)
    _enter(bot, provider, monkeypatch, a, now)
    _enter(bot, provider, monkeypatch, b, now)
    notional = bot.position_manager.open_notional()
    assert 4900 <= notional <= 5000


def test_a_wide_stop_is_still_governed_by_the_risk_budget(tmp_path, monkeypatch):
    """The exposure cap only ever REDUCES size. With a 1.9% stop the 0.75% risk budget
    ($37.50 on $5,000) allows ~187 shares, under both dollar caps -- and that must
    still win."""
    bot, provider = _bot(tmp_path, equity=5000)
    a, _ = _two_tickers(bot)
    now = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)
    _enter(bot, provider, monkeypatch, a, now, stop_distance=0.20)
    assert bot.position_manager.get_position(a).shares == 187



# --- settled cash only (IBKR: "you can only trade with settled cash") -------------

def test_settled_cash_budget_ignores_that_positions_were_sold():
    # $5,000 already spent today; both positions closed since. Proceeds settle
    # tomorrow, so nothing is left to buy with today.
    assert cap_shares_to_settled_cash(242, 10.30, 5000.0, 5000.0, 1.0) == 0


def test_settled_cash_budget_allows_the_remainder():
    assert cap_shares_to_settled_cash(242, 10.30, 2492.6, 5000.0, 1.0) == 242


def test_settled_cash_none_disables():
    assert cap_shares_to_settled_cash(242, 10.30, 99999.0, 5000.0, None) == 242


def _close(bot, ticker, now):
    pos = bot.position_manager.get_position(ticker)
    bot.position_manager.close_position(ticker, now, pos.entry_price, ExitReason.TARGET_HIT)


def _tickers(bot, n):
    return [t for t in bot.config.tickers if bot.config.tickers[t].benchmark][:n]


def test_third_buy_same_day_is_refused_even_after_both_positions_closed(tmp_path, monkeypatch):
    bot, provider = _bot(tmp_path, equity=5000)
    a, b, c = _tickers(bot, 3)
    now = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)
    _enter(bot, provider, monkeypatch, a, now)
    _enter(bot, provider, monkeypatch, b, now)
    _close(bot, a, now)
    _close(bot, b, now)
    assert bot.position_manager.open_position_count() == 0   # slots are free...
    _enter(bot, provider, monkeypatch, c, now)
    assert not bot.position_manager.has_open_position(c)     # ...but the cash is not settled


def test_next_day_the_cash_is_settled_again(tmp_path, monkeypatch):
    bot, provider = _bot(tmp_path, equity=5000)
    a, b, c = _tickers(bot, 3)
    now = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)
    _enter(bot, provider, monkeypatch, a, now)
    _enter(bot, provider, monkeypatch, b, now)
    _close(bot, a, now)
    _close(bot, b, now)
    bot.risk_manager.reset_daily_counters()                  # next session
    _enter(bot, provider, monkeypatch, c, datetime(2026, 3, 3, 14, 55, tzinfo=timezone.utc))
    assert bot.position_manager.has_open_position(c)


def test_margin_style_reuse_is_possible_when_the_rule_is_off(tmp_path, monkeypatch):
    """Guards the test above against passing for the wrong reason."""
    bot, provider = _bot(tmp_path, equity=5000)
    bot.config.risk["sizing"]["max_daily_purchases_pct_equity"] = None
    a, b, c = _tickers(bot, 3)
    now = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)
    _enter(bot, provider, monkeypatch, a, now)
    _enter(bot, provider, monkeypatch, b, now)
    _close(bot, a, now)
    _close(bot, b, now)
    _enter(bot, provider, monkeypatch, c, now)
    assert bot.position_manager.has_open_position(c)



def test_leftover_crumbs_do_not_become_a_position(tmp_path, monkeypatch):
    """$14.80 of settled cash would buy 1 share; that must be skipped, not traded."""
    bot, provider = _bot(tmp_path, equity=5000)
    a, b, c = _tickers(bot, 3)
    now = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)
    _enter(bot, provider, monkeypatch, a, now)
    _enter(bot, provider, monkeypatch, b, now)
    _close(bot, a, now)
    _close(bot, b, now)
    left = bot.risk_manager.day_start_equity - bot.risk_manager.purchases_today
    assert 0 < left < 100
    _enter(bot, provider, monkeypatch, c, now)
    assert not bot.position_manager.has_open_position(c)
