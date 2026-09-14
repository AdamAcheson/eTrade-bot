"""FINRA pattern-day-trader gate (config/risk.yaml: safety.pattern_day_trader).

A day trade is a position opened and closed in the same session. A margin account
under $25,000 may make at most 3 in any 5 rolling business days; the 4th triggers a
PDT restriction. RiskManager enforces this at ENTRY, since by exit time the only way
to avoid the 4th day trade would be to hold overnight against the thesis.
"""

from datetime import date, datetime, timedelta

from models.signal import RejectionReason
from risk.risk_manager import RiskManager, business_days_elapsed


def make_risk_manager(pdt=None, **overrides):
    risk_config = {
        "safety": {
            "max_trades_per_day": 50,
            "max_daily_loss_pct": 0.02,
            "max_consecutive_losses": 99,
            "ticker_cooldown_after_stop_minutes": 0,
        },
        "behavior": {
            "max_concurrent_positions": 5,
            "averaging_down": False,
            "pyramiding": False,
        },
    }
    if pdt is not None:
        risk_config["safety"]["pattern_day_trader"] = pdt
    risk_config["safety"].update(overrides.pop("safety", {}))
    return RiskManager(risk_config)


def base_kwargs(**overrides):
    kwargs = dict(
        ticker="AG",
        account_equity=5_000,
        has_open_position=False,
        open_position_count=0,
        has_pending_order_for_ticker=False,
        spread_pct=0.10,
        max_spread_pct=0.20,
        data_is_stale=False,
        broker_connected=True,
        kill_switch_active=False,
    )
    kwargs.update(overrides)
    return kwargs


ENABLED = {"enabled": True, "max_day_trades": 3, "rolling_business_days": 5}


def day_trade(rm, day, ticker="AG"):
    """Record one same-session round trip on `day` (a Monday-anchored trading day)."""
    entry = datetime.combine(day, datetime.min.time()).replace(hour=10)
    exit_ = entry.replace(hour=14)
    rm.record_trade_result(10.0, exit_, ticker, entry_time=entry)


# --- business-day arithmetic -------------------------------------------------

def test_business_days_elapsed_same_day_is_zero():
    assert business_days_elapsed(date(2026, 3, 23), date(2026, 3, 23)) == 0


def test_business_days_elapsed_skips_the_weekend():
    # Fri 2026-03-27 -> Mon 2026-03-30 is one business day, not three calendar days.
    assert business_days_elapsed(date(2026, 3, 27), date(2026, 3, 30)) == 1


def test_business_days_elapsed_counts_a_full_week_as_five():
    assert business_days_elapsed(date(2026, 3, 23), date(2026, 3, 30)) == 5


# --- the gate ----------------------------------------------------------------

def test_three_day_trades_allowed_then_fourth_entry_blocked():
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    for _ in range(3):
        day_trade(rm, monday)

    now = datetime.combine(monday, datetime.min.time()).replace(hour=15)
    result = rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now)
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_PDT_LIMIT
    assert result.detail == "pattern_day_trader_limit"


def test_entry_allowed_while_under_the_limit():
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    day_trade(rm, monday)
    day_trade(rm, monday)

    now = datetime.combine(monday, datetime.min.time()).replace(hour=15)
    assert rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now).allowed
    assert rm.day_trades_remaining(monday) == 1


def test_day_trades_age_out_of_the_rolling_window():
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    for _ in range(3):
        day_trade(rm, monday)

    # Still blocked on the Friday -- Mon..Fri is exactly the 5-business-day window.
    friday = date(2026, 3, 27)
    assert rm.day_trades_remaining(friday) == 0

    # The following Monday is 5 business days after, so Monday's three have aged out.
    next_monday = date(2026, 3, 30)
    assert rm.day_trades_remaining(next_monday) == 3
    now = datetime.combine(next_monday, datetime.min.time()).replace(hour=10)
    assert rm.can_open_new_position(**base_kwargs(), now=now).allowed


def test_overnight_hold_is_not_a_day_trade():
    rm = make_risk_manager(ENABLED)
    entry = datetime(2026, 3, 23, 15, 0)
    exit_ = datetime(2026, 3, 24, 10, 0)   # next session
    rm.record_trade_result(10.0, exit_, "AG", entry_time=entry)
    assert rm.day_trades_in_window(date(2026, 3, 24)) == 0


def test_reset_daily_counters_does_not_clear_the_pdt_window():
    """The daily reset runs at every session rollover; the PDT window is explicitly
    the thing that must survive it."""
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    for _ in range(3):
        day_trade(rm, monday)
    rm.reset_daily_counters()
    assert rm.day_trades_in_window(date(2026, 3, 24)) == 3


def test_disabled_by_default_when_config_key_absent():
    """An account over the $25,000 PDT minimum should be unconstrained, and an older
    risk.yaml without the key must keep working."""
    rm = make_risk_manager(None)
    monday = date(2026, 3, 23)
    for _ in range(10):
        day_trade(rm, monday)
    assert rm.day_trades_remaining(monday) is None
    now = datetime.combine(monday, datetime.min.time()).replace(hour=15)
    assert rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now).allowed


def test_explicitly_disabled_allows_unlimited_day_trades():
    rm = make_risk_manager({"enabled": False, "max_day_trades": 3, "rolling_business_days": 5})
    monday = date(2026, 3, 23)
    for _ in range(10):
        day_trade(rm, monday)
    now = datetime.combine(monday, datetime.min.time()).replace(hour=15)
    assert rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now).allowed


def test_entry_time_omitted_records_no_day_trade():
    """Callers that genuinely don't know the entry time must not silently inflate the
    count -- record_trade_result skips PDT accounting entirely rather than guessing."""
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    now = datetime.combine(monday, datetime.min.time()).replace(hour=14)
    for _ in range(5):
        rm.record_trade_result(10.0, now, "AG")
    assert rm.day_trades_in_window(monday) == 0


def test_max_day_trades_is_configurable():
    rm = make_risk_manager({"enabled": True, "max_day_trades": 1, "rolling_business_days": 5})
    monday = date(2026, 3, 23)
    day_trade(rm, monday)
    now = datetime.combine(monday, datetime.min.time()).replace(hour=15)
    result = rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now)
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_PDT_LIMIT


# --- open positions reserve day trades ---------------------------------------

def test_open_positions_opened_today_count_against_the_budget():
    """Regression: counting only COMPLETED day trades let max_concurrent_positions
    entries open before the first close, and all of them closed same-session -- the
    backtest produced 5 day trades in a day against a limit of 3."""
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    now = datetime.combine(monday, datetime.min.time()).replace(hour=10)

    # Nothing closed yet, but three positions are already open from today.
    result = rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now,
                                      open_positions_opened_today=3)
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_PDT_LIMIT


def test_completed_and_open_day_trades_share_one_budget():
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    day_trade(rm, monday)          # 1 spent
    now = datetime.combine(monday, datetime.min.time()).replace(hour=13)

    # 1 completed + 1 open = 2 of 3 spent, so one more entry is still allowed.
    assert rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now,
                                    open_positions_opened_today=1).allowed
    # 1 completed + 2 open = 3 of 3 spent.
    assert not rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now,
                                        open_positions_opened_today=2).allowed


def test_positions_opened_on_an_earlier_session_do_not_reserve_a_day_trade():
    """A position carried overnight closes as a normal sale, not a day trade, so it
    must not consume budget. main.py filters on entry date; this pins the contract."""
    rm = make_risk_manager(ENABLED)
    monday = date(2026, 3, 23)
    now = datetime.combine(monday, datetime.min.time()).replace(hour=10)
    assert rm.can_open_new_position(**base_kwargs(ticker="HL"), now=now,
                                    open_positions_opened_today=0).allowed
