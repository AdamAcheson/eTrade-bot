from datetime import datetime

import pytest

from config_loader import load_config
from models.signal import Decision, RejectionReason
from strategy.benchmark import benchmark_confirmation
from strategy.setups import basic_eligibility, is_overextended
from strategy.signal_engine import EvaluationContext, evaluate_ticker

from factories import base_bench_bars, base_bench_snapshot, base_stock_bars, base_stock_snapshot


@pytest.fixture(scope="module")
def config():
    return load_config()


def make_ctx(config, **overrides):
    ag = config.tickers["AG"]
    defaults = dict(
        ticker="AG",
        benchmark="SIL",
        bars=base_stock_bars(),
        benchmark_bars=base_bench_bars(),
        snapshot=base_stock_snapshot(),
        benchmark_snapshot=base_bench_snapshot(),
        max_spread_pct=ag.max_spread_pct,
        volatility_category=ag.volatility_category,
        manual_only=False,
        allow_new_entries=True,
        minimum_entry_score=70,
        now=datetime(2026, 3, 2, 9, 55),
        profit_target_pct=ag.profit_target_pct,
    )
    defaults.update(overrides)
    return EvaluationContext(**defaults)


# --- unit tests: benchmark confirmation -------------------------------------

def test_benchmark_confirmation_passes_when_bullish():
    result = benchmark_confirmation(base_bench_snapshot(), base_bench_bars())
    assert result.confirmed


def test_benchmark_confirmation_fails_below_vwap():
    result = benchmark_confirmation(base_bench_snapshot(vwap=51.0), base_bench_bars())
    assert not result.confirmed
    assert result.reason == "benchmark_below_vwap"


def test_benchmark_confirmation_tolerance_allows_small_near_miss():
    # last_price=50.5, vwap=50.55 -> price is 0.099% below VWAP, within a 0.15% band.
    result = benchmark_confirmation(base_bench_snapshot(vwap=50.55), base_bench_bars(), vwap_tolerance_pct=0.15)
    assert result.confirmed


def test_benchmark_confirmation_tolerance_still_rejects_beyond_the_band():
    # last_price=50.5, vwap=51.0 -> ~0.98% below VWAP, well outside a 0.15% band.
    result = benchmark_confirmation(base_bench_snapshot(vwap=51.0), base_bench_bars(), vwap_tolerance_pct=0.15)
    assert not result.confirmed
    assert result.reason == "benchmark_below_vwap"


def test_benchmark_confirmation_zero_tolerance_is_original_strict_behavior():
    result = benchmark_confirmation(base_bench_snapshot(vwap=50.55), base_bench_bars(), vwap_tolerance_pct=0.0)
    assert not result.confirmed


def test_benchmark_confirmation_fails_ema_not_aligned():
    result = benchmark_confirmation(base_bench_snapshot(ema_9=49.9, ema_20=50.2), base_bench_bars())
    assert not result.confirmed
    assert result.reason == "benchmark_ema_not_aligned"


# --- unit tests: basic eligibility -------------------------------------------

def test_basic_eligibility_passes():
    result = basic_eligibility(base_stock_snapshot(), max_spread_pct=0.20, min_relative_volume=1.20)
    assert result.eligible


def test_basic_eligibility_fails_below_vwap():
    result = basic_eligibility(base_stock_snapshot(last_price=10.20, vwap=10.28), max_spread_pct=0.20, min_relative_volume=1.20)
    assert not result.eligible
    assert result.reason == "REJECTED_BELOW_VWAP"


def test_basic_eligibility_fails_low_volume():
    result = basic_eligibility(base_stock_snapshot(relative_volume=1.0), max_spread_pct=0.20, min_relative_volume=1.20)
    assert not result.eligible
    assert result.reason == "REJECTED_LOW_VOLUME"


def test_basic_eligibility_fails_wide_spread():
    result = basic_eligibility(base_stock_snapshot(bid=10.00, ask=10.40), max_spread_pct=0.20, min_relative_volume=1.20)
    assert not result.eligible
    assert result.reason == "REJECTED_WIDE_SPREAD"


# --- unit tests: chase / overextension rule -----------------------------------

def test_not_overextended_by_default():
    result = is_overextended(base_stock_bars(), base_stock_snapshot())
    assert not result.overextended


def test_overextended_when_far_above_vwap():
    result = is_overextended(base_stock_bars(), base_stock_snapshot(vwap=10.00, atr=0.15))
    assert result.overextended
    assert result.reason == "extended_above_vwap"


def test_overextended_three_consecutive_large_green_candles():
    bars = base_stock_bars()
    # Replace the last three bars with unusually large, uninterrupted green candles.
    from factories import bar
    bars[3] = bar(15, 10.20, 10.60, 10.19, 10.55, 90_000)
    bars[4] = bar(20, 10.55, 11.00, 10.54, 10.95, 95_000)
    bars[5] = bar(25, 10.95, 11.45, 10.94, 11.40, 100_000)
    snapshot = base_stock_snapshot(last_price=11.40, atr=0.15, vwap=10.28)
    result = is_overextended(bars, snapshot)
    assert result.overextended
    assert result.reason == "consecutive_large_green_candles"


# --- scenario tests (spec section 35) -----------------------------------------

def test_scenario_1_benchmark_and_stock_bullish_valid_pullback_enters(config):
    ctx = make_ctx(config)
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.ENTRY_CANDIDATE
    assert signal.setup_type == "ORB_PULLBACK_CONTINUATION"
    assert signal.entry_price == pytest.approx(10.40)
    assert signal.stop == pytest.approx(10.14)
    assert signal.r_ratio >= config.strategy["reward_risk"]["minimum_r"]
    assert signal.setup_score >= ctx.minimum_entry_score


def test_scenario_2_stock_bullish_but_benchmark_below_vwap_rejects(config):
    ctx = make_ctx(config, benchmark_snapshot=base_bench_snapshot(vwap=51.0))
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_BENCHMARK_CONFIRMATION


def test_scenario_3_stock_overextended_rejects(config):
    ctx = make_ctx(config, snapshot=base_stock_snapshot(vwap=10.00, atr=0.15))
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_OVEREXTENDED


def test_scenario_4_spread_exceeds_threshold_rejects(config):
    ctx = make_ctx(config, snapshot=base_stock_snapshot(bid=10.00, ask=10.40))
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_WIDE_SPREAD


def test_scenario_5_poor_risk_reward_rejects(config):
    # A much larger ATR widens the ATR-based stop far past the structural pullback
    # low, ballooning risk-per-share against a fixed percentage target -> R < 1.5.
    ctx = make_ctx(config, snapshot=base_stock_snapshot(atr=1.0))
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_POOR_RISK_REWARD


def test_missing_atr_rejects_data_quality_instead_of_crashing(config):
    # Regression test: with require_ema_alignment disabled, benchmark confirmation
    # no longer implicitly guarantees ATR14 has warmed up on the stock side (EMA20
    # needs ~100 min, ATR14 only ~75 min -- they'd previously always overlap).
    # Without an explicit guard, a matched setup would crash computing the stop
    # price (atr_multiplier * None).
    ctx = make_ctx(config, snapshot=base_stock_snapshot(atr=None, atr_percent=None))
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_DATA_QUALITY


def test_time_window_rejected_outside_entry_window(config):
    ctx = make_ctx(config, allow_new_entries=False)
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_TIME_WINDOW


def test_manual_only_security_never_auto_entered(config):
    ctx = make_ctx(config, manual_only=True)
    signal = evaluate_ticker(ctx, config.strategy, config.risk)
    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_MANUAL_ONLY
