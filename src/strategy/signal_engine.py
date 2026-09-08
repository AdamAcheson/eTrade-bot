"""Orchestrates one evaluation of one ticker: eligibility -> chase rule -> benchmark
confirmation -> setup detection -> reward/risk -> scoring -> Signal. This is the only
place that decides ENTRY_CANDIDATE vs REJECTED for a fresh entry; duplicate-position
and daily-risk checks are RiskManager's job and happen after this (see main.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from data.indicators import IndicatorSnapshot
from models.bar import Bar
from models.signal import Decision, RejectionReason, Signal
from risk.position_sizing import (
    atr_multiplier_for_category,
    final_stop_price,
    r_multiple_target,
    reward_risk_ratio,
)
from strategy.benchmark import benchmark_confirmation
from strategy.scoring import score_setup
from strategy.setups import SetupResult, basic_eligibility, detect_opening_range_breakout_pullback, detect_vwap_reclaim, is_overextended


@dataclass
class EvaluationContext:
    ticker: str
    benchmark: str
    bars: Sequence[Bar]
    benchmark_bars: Sequence[Bar]
    snapshot: IndicatorSnapshot
    benchmark_snapshot: IndicatorSnapshot
    max_spread_pct: float
    volatility_category: str
    manual_only: bool
    allow_new_entries: bool
    minimum_entry_score: float
    now: datetime
    profit_target_pct: Optional[Sequence[float]] = None
    benchmark_prior_close: Optional[float] = None


def evaluate_ticker(ctx: EvaluationContext, strategy_config: dict, risk_config: dict) -> Signal:
    signal = Signal(
        timestamp=ctx.now,
        ticker=ctx.ticker,
        benchmark=ctx.benchmark,
        price=ctx.snapshot.last_price,
        bid=ctx.snapshot.bid,
        ask=ctx.snapshot.ask,
        spread=ctx.snapshot.spread,
        vwap=ctx.snapshot.vwap or 0.0,
        ema_9=ctx.snapshot.ema_9 or 0.0,
        ema_20=ctx.snapshot.ema_20 or 0.0,
        atr=ctx.snapshot.atr or 0.0,
        atr_percent=ctx.snapshot.atr_percent or 0.0,
        relative_volume=ctx.snapshot.relative_volume or 0.0,
        opening_range_high=ctx.snapshot.opening_range_high,
        opening_range_low=ctx.snapshot.opening_range_low,
        benchmark_price=ctx.benchmark_snapshot.last_price,
        benchmark_vwap=ctx.benchmark_snapshot.vwap or 0.0,
        benchmark_ema_9=ctx.benchmark_snapshot.ema_9 or 0.0,
        benchmark_ema_20=ctx.benchmark_snapshot.ema_20 or 0.0,
    )

    def reject(reason: RejectionReason) -> Signal:
        signal.decision = Decision.REJECTED
        signal.rejection_reason = reason
        return signal

    if not ctx.allow_new_entries:
        return reject(RejectionReason.REJECTED_TIME_WINDOW)

    if ctx.manual_only:
        return reject(RejectionReason.REJECTED_MANUAL_ONLY)

    bench_cfg = strategy_config["benchmark_confirmation"]
    bench_result = benchmark_confirmation(
        ctx.benchmark_snapshot,
        ctx.benchmark_bars,
        require_price_above_vwap=bench_cfg["require_price_above_vwap"],
        require_ema_alignment=bench_cfg["require_ema_alignment"],
        require_no_fresh_intraday_low=bench_cfg["require_no_fresh_intraday_low"],
        vwap_tolerance_pct=bench_cfg.get("vwap_tolerance_pct", 0.0),
        prior_session_close=ctx.benchmark_prior_close,
        min_trend_pct=bench_cfg.get("min_trend_pct", 0.0),
    )
    if not bench_result.confirmed:
        signal.extra["benchmark_rejection_detail"] = bench_result.reason
        return reject(RejectionReason.REJECTED_BENCHMARK_CONFIRMATION)

    elig = basic_eligibility(
        ctx.snapshot,
        max_spread_pct=ctx.max_spread_pct,
        min_relative_volume=strategy_config["eligibility"]["min_relative_volume"],
        require_ema_alignment=strategy_config["eligibility"].get("require_ema_alignment", True),
    )
    if not elig.eligible:
        return reject(RejectionReason(elig.reason))

    # ATR must be available before the chase rule (which silently no-ops on None,
    # rather than rejecting) and the stop-price calculation (which crashes on None)
    # both run. Previously masked by benchmark confirmation's EMA-20 gate requiring
    # ~100 minutes of warm-up, well past ATR14's ~75-minute requirement -- exposed
    # once require_ema_alignment could be disabled independently of ATR readiness.
    if ctx.snapshot.atr is None:
        return reject(RejectionReason.REJECTED_DATA_QUALITY)

    chase_cfg = strategy_config["chase_rule"]
    chase = is_overextended(
        ctx.bars,
        ctx.snapshot,
        max_consecutive_large_green_candles=chase_cfg["max_consecutive_large_green_candles"],
        large_candle_body_atr_multiple=chase_cfg["large_candle_body_atr_multiple"],
        max_atr_above_vwap=chase_cfg["max_atr_above_vwap"],
        max_pct_above_open_without_consolidation=chase_cfg["max_pct_above_open_without_consolidation"],
    )
    if chase.overextended:
        return reject(RejectionReason.REJECTED_OVEREXTENDED)

    setup_cfg = strategy_config["setups"]
    setup = SetupResult(matched=False)
    if setup_cfg.get("enable_orb_pullback", True):
        orb_bar_count = setup_cfg["opening_range_minutes"] // 5 or 1
        setup = detect_opening_range_breakout_pullback(
            ctx.bars,
            opening_range_high=ctx.snapshot.opening_range_high,
            opening_range_bar_count=orb_bar_count,
            pullback_max_atr_from_breakout=setup_cfg["pullback_max_atr_from_breakout"],
            atr_value=ctx.snapshot.atr,
            ema_9=ctx.snapshot.ema_9,
            vwap_value=ctx.snapshot.vwap,
        )
        if not setup.matched:
            signal.extra["orb_rejection_detail"] = setup.reason
    if not setup.matched:
        setup = detect_vwap_reclaim(ctx.bars, lookback_bars=setup_cfg["vwap_reclaim_lookback_bars"])

    if not setup.matched:
        return reject(RejectionReason.REJECTED_NO_SETUP)

    entry_price = setup.entry_price or ctx.snapshot.last_price
    atr_multiplier = atr_multiplier_for_category(ctx.volatility_category, risk_config["stops"]["atr_multiplier"])
    stop_price = final_stop_price(
        entry_price,
        atr_value=ctx.snapshot.atr,
        atr_multiplier=atr_multiplier,
        swing_low=setup.structure_stop_reference or ctx.snapshot.swing_low,
    )

    rr_cfg = strategy_config["reward_risk"]
    # scale_target_with_stop (default off): a fixed profit_target_pct target doesn't
    # move when the stop is widened, which silently shrinks the R-ratio the wider
    # the stop gets. When on, always target a fixed R-multiple of the ACTUAL stop
    # distance instead, so reward scales with whatever risk was actually taken.
    if ctx.profit_target_pct and not rr_cfg.get("scale_target_with_stop", False):
        target_pct = (ctx.profit_target_pct[0] + ctx.profit_target_pct[1]) / 2.0
        target_price = entry_price * (1 + target_pct / 100.0)
    else:
        target_price = r_multiple_target(entry_price, stop_price, rr_cfg["preferred_r_min"])
    r_ratio = reward_risk_ratio(entry_price, stop_price, target_price)

    if r_ratio is None or r_ratio < rr_cfg["minimum_r"]:
        return reject(RejectionReason.REJECTED_POOR_RISK_REWARD)

    score = score_setup(
        weights=strategy_config["scoring"]["weights"],
        stock_snapshot=ctx.snapshot,
        benchmark_snapshot=ctx.benchmark_snapshot,
        setup_matched=True,
        pullback_shallow=True,
        r_ratio=r_ratio,
        minimum_r=rr_cfg["minimum_r"],
        preferred_r_min=rr_cfg["preferred_r_min"],
        max_spread_pct=ctx.max_spread_pct,
    )

    signal.setup_type = setup.setup_type
    signal.setup_score = score.total
    signal.entry_price = entry_price
    signal.stop = stop_price
    signal.target = target_price
    signal.risk_per_share = entry_price - stop_price
    signal.expected_reward = target_price - entry_price
    signal.r_ratio = r_ratio

    if score.total < ctx.minimum_entry_score:
        return reject(RejectionReason.REJECTED_LOW_SCORE)

    signal.entry_candidate = True
    signal.decision = Decision.ENTRY_CANDIDATE
    signal.rejection_reason = None
    return signal
