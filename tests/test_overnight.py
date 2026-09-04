from datetime import datetime

import pytest

from models.position import Position
from models.trade import TradeState
from positions.overnight import evaluate_overnight_eligibility, shares_to_hold_overnight

from factories import base_bench_bars, base_bench_snapshot, base_stock_bars, base_stock_snapshot


def make_position(entry_price=10.00, shares=200):
    return Position(
        ticker="AG",
        benchmark="SIL",
        state=TradeState.POSITION_OPEN,
        entry_time=datetime(2026, 3, 2, 9, 55),
        entry_price=entry_price,
        shares=shares,
        original_shares=shares,
        initial_stop=9.70,
        current_stop=9.70,
        initial_target=10.80,
        current_target=10.80,
    )


def test_overnight_eligible_when_thesis_holds():
    decision = evaluate_overnight_eligibility(
        position=make_position(entry_price=10.00),
        stock_bars=base_stock_bars(),
        stock_snapshot=base_stock_snapshot(),
        benchmark_bars=base_bench_bars(),
        benchmark_snapshot=base_bench_snapshot(),
        overnight_category="normal",
        overnight_position_multiplier=1.0,
        max_spread_pct=0.20,
    )
    assert decision.eligible
    assert decision.size_multiplier == 1.0
    assert decision.reasons == []


def test_manual_only_is_never_eligible_regardless_of_thesis():
    decision = evaluate_overnight_eligibility(
        position=make_position(),
        stock_bars=base_stock_bars(),
        stock_snapshot=base_stock_snapshot(),
        benchmark_bars=base_bench_bars(),
        benchmark_snapshot=base_bench_snapshot(),
        overnight_category="manual_only",
        overnight_position_multiplier=0.0,
        max_spread_pct=0.75,
    )
    assert not decision.eligible
    assert decision.size_multiplier == 0.0


def test_conditional_category_reduces_size_when_eligible():
    decision = evaluate_overnight_eligibility(
        position=make_position(entry_price=10.00),
        stock_bars=base_stock_bars(),
        stock_snapshot=base_stock_snapshot(),
        benchmark_bars=base_bench_bars(),
        benchmark_snapshot=base_bench_snapshot(),
        overnight_category="conditional",
        overnight_position_multiplier=0.50,
        max_spread_pct=0.20,
    )
    assert decision.eligible
    assert decision.size_multiplier == 0.50


def test_ineligible_when_stock_below_vwap():
    decision = evaluate_overnight_eligibility(
        position=make_position(entry_price=10.00),
        stock_bars=base_stock_bars(),
        stock_snapshot=base_stock_snapshot(last_price=10.20, vwap=10.28),
        benchmark_bars=base_bench_bars(),
        benchmark_snapshot=base_bench_snapshot(),
        overnight_category="normal",
        overnight_position_multiplier=1.0,
        max_spread_pct=0.20,
    )
    assert not decision.eligible
    assert "stock_below_vwap" in decision.reasons


def test_ineligible_when_earnings_before_next_session():
    decision = evaluate_overnight_eligibility(
        position=make_position(entry_price=10.00),
        stock_bars=base_stock_bars(),
        stock_snapshot=base_stock_snapshot(),
        benchmark_bars=base_bench_bars(),
        benchmark_snapshot=base_bench_snapshot(),
        overnight_category="normal",
        overnight_position_multiplier=1.0,
        max_spread_pct=0.20,
        has_earnings_before_next_session=True,
    )
    assert not decision.eligible
    assert "earnings_before_next_session" in decision.reasons


def test_ineligible_when_benchmark_trend_not_bullish():
    decision = evaluate_overnight_eligibility(
        position=make_position(entry_price=10.00),
        stock_bars=base_stock_bars(),
        stock_snapshot=base_stock_snapshot(),
        benchmark_bars=base_bench_bars(),
        benchmark_snapshot=base_bench_snapshot(ema_9=49.9, ema_20=50.2),
        overnight_category="normal",
        overnight_position_multiplier=1.0,
        max_spread_pct=0.20,
    )
    assert not decision.eligible
    assert "benchmark_trend_not_bullish" in decision.reasons


@pytest.mark.parametrize("shares,multiplier,expected", [
    (200, 0.50, 100),
    (200, 1.0, 200),
    (201, 0.50, 100),
    (100, 0.0, 0),
])
def test_shares_to_hold_overnight(shares, multiplier, expected):
    assert shares_to_hold_overnight(shares, multiplier) == expected
