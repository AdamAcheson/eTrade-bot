import copy
from datetime import datetime

import pytest

from config_loader import load_config
from models.signal import Decision, RejectionReason
from risk.position_sizing import r_multiple_target
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


def orb_strategy(config):
    """config/strategy.yaml ships with enable_orb_pullback: false (ORB lost money
    on a real 47-trade sample -- see that file's comment). The spec section 35
    scenarios below exercise the ORB detection logic itself, so they enable it
    explicitly rather than depending on whatever the deployed default happens to
    be."""
    strategy = copy.deepcopy(config.strategy)
    strategy["setups"]["enable_orb_pullback"] = True
    return strategy


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


def test_benchmark_confirmation_trend_path_confirms_despite_below_vwap():
    # last_price=50.5, vwap=51.0 -> below VWAP even with tolerance, but +1.0% vs a
    # prior close of 50.0 clears a 0.5% trend threshold -> confirms anyway.
    result = benchmark_confirmation(
        base_bench_snapshot(vwap=51.0), base_bench_bars(),
        vwap_tolerance_pct=0.15, prior_session_close=50.0, min_trend_pct=0.5,
    )
    assert result.confirmed


def test_benchmark_confirmation_trend_path_still_rejects_below_threshold():
    # Same +1.0% move, but the configured trend threshold (2.0%) isn't met.
    result = benchmark_confirmation(
        base_bench_snapshot(vwap=51.0), base_bench_bars(),
        vwap_tolerance_pct=0.15, prior_session_close=50.0, min_trend_pct=2.0,
    )
    assert not result.confirmed
    assert result.reason == "benchmark_below_vwap"


def test_benchmark_confirmation_trend_path_disabled_by_default():
    # min_trend_pct=0.0 (the default) must reproduce the original VWAP-only
    # behavior even when a prior close is supplied.
    result = benchmark_confirmation(
        base_bench_snapshot(vwap=51.0), base_bench_bars(), prior_session_close=50.0,
    )
    assert not result.confirmed


def test_benchmark_confirmation_trend_path_no_prior_close_falls_back_to_vwap():
    # Cold start: no prior-day bars yet, so the trend path can't be evaluated even
    # though min_trend_pct is enabled -- must not crash or silently confirm.
    result = benchmark_confirmation(
        base_bench_snapshot(vwap=51.0), base_bench_bars(),
        vwap_tolerance_pct=0.15, prior_session_close=None, min_trend_pct=0.5,
    )
    assert not result.confirmed
    assert result.reason == "benchmark_below_vwap"


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


def test_basic_eligibility_fails_ema_not_aligned_by_default():
    # last_price above VWAP (passes that check) but below its own 9EMA.
    result = basic_eligibility(
        base_stock_snapshot(last_price=10.15, vwap=10.00, ema_9=10.20),
        max_spread_pct=0.20, min_relative_volume=1.20,
    )
    assert not result.eligible
    assert result.reason == "REJECTED_EMA_ALIGNMENT"


def test_basic_eligibility_ema_check_disabled_admits_below_ema9():
    result = basic_eligibility(
        base_stock_snapshot(last_price=10.15, vwap=10.00, ema_9=10.20),
        max_spread_pct=0.20, min_relative_volume=1.20, require_ema_alignment=False,
    )
    assert result.eligible


def test_basic_eligibility_ema_check_disabled_does_not_require_ema9_warmed_up():
    result = basic_eligibility(
        base_stock_snapshot(ema_9=None),
        max_spread_pct=0.20, min_relative_volume=1.20, require_ema_alignment=False,
    )
    assert result.eligible


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
    signal = evaluate_ticker(ctx, orb_strategy(config), config.risk)
    assert signal.decision == Decision.ENTRY_CANDIDATE
    assert signal.setup_type == "ORB_PULLBACK_CONTINUATION"
    assert signal.entry_price == pytest.approx(10.40)
    assert signal.stop == pytest.approx(10.14)
    assert signal.r_ratio >= config.strategy["reward_risk"]["minimum_r"]
    assert signal.setup_score >= ctx.minimum_entry_score


def test_scale_target_with_stop_ignores_fixed_profit_target_pct(config):
    # Same scenario-1 setup (ticker=AG, profit_target_pct=[3.0, 5.5] in tickers.yaml)
    # but with scale_target_with_stop on -- target should track the ACTUAL stop
    # distance (1.75x preferred_r_min) instead of the fixed % target.
    strategy = orb_strategy(config)
    strategy["reward_risk"]["scale_target_with_stop"] = True
    ctx = make_ctx(config)
    signal = evaluate_ticker(ctx, strategy, config.risk)

    assert signal.decision == Decision.ENTRY_CANDIDATE
    expected_target = r_multiple_target(signal.entry_price, signal.stop, strategy["reward_risk"]["preferred_r_min"])
    assert signal.target == pytest.approx(expected_target)
    # Sanity: this must differ from the fixed-%-target default behavior.
    fixed_pct_target = signal.entry_price * (1 + ((3.0 + 5.5) / 2.0) / 100.0)
    assert signal.target != pytest.approx(fixed_pct_target)


def test_enable_orb_pullback_false_skips_straight_to_vwap_reclaim(config):
    # Scenario-1's bars are a valid ORB pullback setup; with ORB disabled, the
    # engine must go straight to VWAP_RECLAIM instead (and, since these bars
    # don't form a VWAP reclaim pattern, land on REJECTED_NO_SETUP rather than
    # ENTRY_CANDIDATE via ORB).
    strategy = copy.deepcopy(config.strategy)
    strategy["setups"]["enable_orb_pullback"] = False
    ctx = make_ctx(config)
    signal = evaluate_ticker(ctx, strategy, config.risk)

    assert signal.decision == Decision.REJECTED
    assert signal.rejection_reason == RejectionReason.REJECTED_NO_SETUP
    # No ORB detail should be logged -- it was never attempted.
    assert "orb_rejection_detail" not in signal.extra


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
    signal = evaluate_ticker(ctx, orb_strategy(config), config.risk)
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
