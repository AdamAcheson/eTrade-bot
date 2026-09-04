from datetime import datetime

from broker.base import OrderSide
from broker.paper import PaperBrokerAdapter
from execution.order_manager import OrderManager
from positions.position_manager import PositionManager
from models.trade import TradeState

from factories import base_stock_snapshot


def make_manager():
    broker = PaperBrokerAdapter(starting_equity=100_000)
    position_manager = PositionManager(max_concurrent_positions=1, pyramiding=False)
    order_manager = OrderManager(broker, position_manager)
    return broker, position_manager, order_manager


def test_successful_entry_submission():
    broker, position_manager, order_manager = make_manager()
    result = order_manager.submit_entry_order(
        ticker="AG",
        current_snapshot=base_stock_snapshot(),
        max_spread_pct=0.20,
        planned_entry_price=10.40,
        planned_limit_price=10.40,
        shares=100,
        benchmark_still_confirmed=True,
        risk_check_passed=True,
    )
    assert result.submitted
    assert result.order.ticker == "AG"
    assert result.order.side == OrderSide.BUY
    assert position_manager.has_pending_order("AG")


def test_duplicate_order_prevented():
    broker, position_manager, order_manager = make_manager()
    first = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=100,
        benchmark_still_confirmed=True, risk_check_passed=True,
    )
    assert first.submitted

    second = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=100,
        benchmark_still_confirmed=True, risk_check_passed=True,
    )
    assert not second.submitted
    assert second.rejection_reason == "duplicate_order_pending"


def test_incompatible_open_position_blocks_new_order():
    broker, position_manager, order_manager = make_manager()
    position_manager.open_position(
        ticker="AG", benchmark="SIL", entry_time=datetime(2026, 3, 2, 9, 55),
        entry_price=10.40, shares=100, stop_price=10.14, target_price=10.84,
        setup_type="ORB_PULLBACK_CONTINUATION", setup_score=85.0,
    )
    result = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=100,
        benchmark_still_confirmed=True, risk_check_passed=True,
    )
    assert not result.submitted
    assert result.rejection_reason == "incompatible_open_position"


def test_spread_recheck_blocks_order():
    broker, position_manager, order_manager = make_manager()
    result = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(bid=10.00, ask=10.40), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=100,
        benchmark_still_confirmed=True, risk_check_passed=True,
    )
    assert not result.submitted
    assert result.rejection_reason == "spread_recheck_failed"


def test_price_drift_recheck_blocks_order():
    broker, position_manager, order_manager = make_manager()
    result = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(last_price=11.00), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=100,
        benchmark_still_confirmed=True, risk_check_passed=True,
    )
    assert not result.submitted
    assert result.rejection_reason == "price_recheck_failed"


def test_benchmark_recheck_blocks_order():
    broker, position_manager, order_manager = make_manager()
    result = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=100,
        benchmark_still_confirmed=False, risk_check_passed=True,
    )
    assert not result.submitted
    assert result.rejection_reason == "benchmark_recheck_failed"


def test_risk_recheck_blocks_order():
    broker, position_manager, order_manager = make_manager()
    result = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=100,
        benchmark_still_confirmed=True, risk_check_passed=False,
    )
    assert not result.submitted
    assert result.rejection_reason == "risk_recheck_failed"


def test_zero_shares_blocks_order():
    broker, position_manager, order_manager = make_manager()
    result = order_manager.submit_entry_order(
        ticker="AG", current_snapshot=base_stock_snapshot(), max_spread_pct=0.20,
        planned_entry_price=10.40, planned_limit_price=10.40, shares=0,
        benchmark_still_confirmed=True, risk_check_passed=True,
    )
    assert not result.submitted
    assert result.rejection_reason == "zero_shares"


def test_paper_broker_fills_on_touch():
    broker = PaperBrokerAdapter(starting_equity=100_000)
    order = broker.submit_limit_order("AG", OrderSide.BUY, 100, 10.40)
    broker.process_quote("AG", bid=10.38, ask=10.41)  # ask above limit -> no fill
    assert broker.get_order_status(order.order_id).status.value == "PENDING"

    broker.process_quote("AG", bid=10.39, ask=10.40)  # ask touches limit -> fills
    filled = broker.get_order_status(order.order_id)
    assert filled.status.value == "FILLED"
    assert filled.filled_quantity == 100
    positions = broker.get_positions()
    assert positions["AG"].quantity == 100
