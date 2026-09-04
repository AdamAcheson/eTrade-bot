from datetime import datetime, timedelta

from models.signal import RejectionReason
from risk.risk_manager import RiskManager


def make_risk_manager(**overrides):
    risk_config = {
        "safety": {
            "max_trades_per_day": 3,
            "max_daily_loss_pct": 0.02,
            "max_consecutive_losses": 2,
            "ticker_cooldown_after_stop_minutes": 45,
        },
        "behavior": {
            "max_concurrent_positions": 1,
            "averaging_down": False,
            "pyramiding": False,
        },
    }
    risk_config["safety"].update(overrides.pop("safety", {}))
    risk_config["behavior"].update(overrides.pop("behavior", {}))
    return RiskManager(risk_config)


def base_kwargs(**overrides):
    kwargs = dict(
        ticker="AG",
        account_equity=100_000,
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


def test_allows_trade_when_all_clear():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs())
    assert result.allowed


def test_blocks_on_kill_switch():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs(kill_switch_active=True))
    assert not result.allowed


def test_blocks_duplicate_position():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs(has_open_position=True))
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_DUPLICATE_POSITION


def test_blocks_duplicate_pending_order():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs(has_pending_order_for_ticker=True))
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_DUPLICATE_POSITION


def test_blocks_when_max_concurrent_positions_reached():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs(open_position_count=1))
    assert not result.allowed


def test_blocks_after_max_trades_per_day():
    rm = make_risk_manager()
    for _ in range(3):
        rm.record_trade_result(pnl=10.0)
    result = rm.can_open_new_position(**base_kwargs())
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_MAX_DAILY_RISK


def test_blocks_after_max_consecutive_losses():
    rm = make_risk_manager()
    rm.record_trade_result(pnl=-50.0, ticker="AG")
    rm.record_trade_result(pnl=-50.0, ticker="SVM")
    result = rm.can_open_new_position(**base_kwargs())
    assert not result.allowed


def test_blocks_after_max_daily_loss():
    rm = make_risk_manager()
    rm.record_trade_result(pnl=-2500.0)  # 2.5% of 100k equity > 2% cap
    result = rm.can_open_new_position(**base_kwargs())
    assert not result.allowed


def test_win_resets_consecutive_losses():
    rm = make_risk_manager()
    rm.record_trade_result(pnl=-50.0, ticker="AG")
    rm.record_trade_result(pnl=50.0, ticker="AG")
    assert rm.consecutive_losses == 0


def test_ticker_cooldown_after_stop():
    rm = make_risk_manager()
    now = datetime(2026, 3, 2, 10, 0)
    rm.record_trade_result(pnl=-50.0, ticker="AG", now=now)
    result = rm.can_open_new_position(**base_kwargs(), now=now + timedelta(minutes=10))
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_MAX_DAILY_RISK

    result_after_cooldown = rm.can_open_new_position(**base_kwargs(), now=now + timedelta(minutes=46))
    assert result_after_cooldown.allowed


def test_cooldown_does_not_block_other_tickers():
    rm = make_risk_manager()
    now = datetime(2026, 3, 2, 10, 0)
    rm.record_trade_result(pnl=-50.0, ticker="AG", now=now)
    result = rm.can_open_new_position(**base_kwargs(ticker="SVM"), now=now + timedelta(minutes=5))
    assert result.allowed


def test_blocks_on_wide_spread_recheck():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs(spread_pct=0.50, max_spread_pct=0.20))
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_SPREAD_TOO_WIDE


def test_blocks_on_stale_data():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs(data_is_stale=True))
    assert not result.allowed
    assert result.reason == RejectionReason.REJECTED_DATA_QUALITY


def test_blocks_on_broker_disconnected():
    rm = make_risk_manager()
    result = rm.can_open_new_position(**base_kwargs(broker_connected=False))
    assert not result.allowed


def test_averaging_down_never_permitted():
    rm = make_risk_manager(behavior={"averaging_down": True})
    # Even if misconfigured, the risk manager's own API refuses to permit it.
    assert rm.can_average_down() is False


def test_reset_daily_counters():
    rm = make_risk_manager()
    rm.record_trade_result(pnl=-50.0, ticker="AG")
    rm.block_new_entries("test")
    rm.reset_daily_counters()
    assert rm.trades_today == 0
    assert rm.daily_realized_pnl == 0.0
    assert rm.consecutive_losses == 0
    result = rm.can_open_new_position(**base_kwargs())
    assert result.allowed
