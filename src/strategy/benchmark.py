"""Benchmark confirmation rule (spec section 7). A stock may not be bought unless its
mapped commodity/mining ETF confirms strength. This module is pure logic over
already-computed indicator snapshots -- it does not know about brokers or data
vendors (spec section 33)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from data.indicators import IndicatorSnapshot, is_fresh_intraday_low
from models.bar import Bar


@dataclass
class BenchmarkResult:
    confirmed: bool
    reason: Optional[str] = None


def benchmark_confirmation(
    benchmark_snapshot: IndicatorSnapshot,
    benchmark_bars: Sequence[Bar],
    require_price_above_vwap: bool = True,
    require_ema_alignment: bool = True,
    require_no_fresh_intraday_low: bool = True,
    vwap_tolerance_pct: float = 0.0,
    prior_session_close: Optional[float] = None,
    min_trend_pct: float = 0.0,
) -> BenchmarkResult:
    """vwap_tolerance_pct softens the VWAP check: the benchmark may trade up to
    that percentage below its own VWAP and still pass, instead of requiring
    strictly above. 0.0 (the default) preserves the original strict behavior.
    Each data-availability check only applies to a requirement that's actually
    enabled -- e.g. disabling require_ema_alignment also stops gating on whether
    the EMAs have warmed up yet, since nothing downstream needs them anymore.

    min_trend_pct adds a second, independent way to satisfy require_price_above_vwap:
    even when the benchmark is trading below today's VWAP, it still confirms if it's
    at least this % above the PRIOR session's close. VWAP resets every morning, so a
    benchmark that's genuinely trending up over days/weeks can still read "below VWAP"
    for large stretches of any single day (open-and-fade, midday chop); this path lets
    a real multi-day trend confirm even on a day that's below its own intraday VWAP.
    <= 0.0 (the default) disables this path entirely -- original VWAP-only behavior.
    prior_session_close=None (no prior-day data yet, e.g. cold start) also disables it
    for that evaluation, rather than crashing or silently confirming."""
    if require_price_above_vwap and benchmark_snapshot.vwap is None:
        return BenchmarkResult(False, "insufficient_benchmark_data")
    if require_ema_alignment and (benchmark_snapshot.ema_9 is None or benchmark_snapshot.ema_20 is None):
        return BenchmarkResult(False, "insufficient_benchmark_data")

    if require_price_above_vwap:
        threshold = benchmark_snapshot.vwap * (1 - vwap_tolerance_pct / 100.0)
        above_vwap = benchmark_snapshot.last_price > threshold

        trending_up = False
        if not above_vwap and min_trend_pct > 0.0 and prior_session_close:
            pct_vs_prior_close = (benchmark_snapshot.last_price - prior_session_close) / prior_session_close * 100.0
            trending_up = pct_vs_prior_close >= min_trend_pct

        if not (above_vwap or trending_up):
            return BenchmarkResult(False, "benchmark_below_vwap")

    if require_ema_alignment and not (benchmark_snapshot.ema_9 >= benchmark_snapshot.ema_20):
        return BenchmarkResult(False, "benchmark_ema_not_aligned")

    if require_no_fresh_intraday_low and is_fresh_intraday_low(benchmark_bars):
        return BenchmarkResult(False, "benchmark_fresh_intraday_low")

    return BenchmarkResult(True, None)
