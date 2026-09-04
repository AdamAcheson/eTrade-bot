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
) -> BenchmarkResult:
    if benchmark_snapshot.vwap is None or benchmark_snapshot.ema_9 is None or benchmark_snapshot.ema_20 is None:
        return BenchmarkResult(False, "insufficient_benchmark_data")

    if require_price_above_vwap and not (benchmark_snapshot.last_price > benchmark_snapshot.vwap):
        return BenchmarkResult(False, "benchmark_below_vwap")

    if require_ema_alignment and not (benchmark_snapshot.ema_9 >= benchmark_snapshot.ema_20):
        return BenchmarkResult(False, "benchmark_ema_not_aligned")

    if require_no_fresh_intraday_low and is_fresh_intraday_low(benchmark_bars):
        return BenchmarkResult(False, "benchmark_fresh_intraday_low")

    return BenchmarkResult(True, None)
