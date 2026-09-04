"""0-100 setup scoring (spec section 22). Each component is scored on a 0..weight
scale by a small heuristic and summed. Weights come from config/strategy.yaml so they
can be retuned without touching this code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from data.indicators import IndicatorSnapshot


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


@dataclass
class ScoreBreakdown:
    benchmark_strength: float
    relative_volume: float
    price_vs_vwap: float
    ema_alignment: float
    opening_range_structure: float
    pullback_quality: float
    risk_reward: float
    spread_liquidity: float
    sector_strength: float

    @property
    def total(self) -> float:
        return sum(vars(self).values())


def score_setup(
    weights: Dict[str, float],
    stock_snapshot: IndicatorSnapshot,
    benchmark_snapshot: IndicatorSnapshot,
    setup_matched: bool,
    pullback_shallow: bool,
    r_ratio: Optional[float],
    minimum_r: float,
    preferred_r_min: float,
    max_spread_pct: float,
) -> ScoreBreakdown:
    # Benchmark strength: how far the benchmark trades above its own VWAP, scaled by
    # its ATR, plus EMA alignment.
    bench_strength_frac = 0.5
    if benchmark_snapshot.atr and benchmark_snapshot.vwap:
        distance_atrs = (benchmark_snapshot.last_price - benchmark_snapshot.vwap) / benchmark_snapshot.atr
        bench_strength_frac = _clamp(0.5 + distance_atrs / 2.0)
    if benchmark_snapshot.ema_9 is not None and benchmark_snapshot.ema_20 is not None:
        if benchmark_snapshot.ema_9 < benchmark_snapshot.ema_20:
            bench_strength_frac *= 0.5

    # Relative volume: min threshold (assumed 1.20) maps to a modest score, 2x+ maps
    # to full credit.
    rvol = stock_snapshot.relative_volume or 0.0
    rvol_frac = _clamp((rvol - 1.0) / 1.0)

    # Price vs VWAP: reward being comfortably above VWAP without being extended
    # (extension itself is rejected separately by the chase rule).
    vwap_frac = 0.0
    if stock_snapshot.vwap and stock_snapshot.atr:
        distance_atrs = (stock_snapshot.last_price - stock_snapshot.vwap) / stock_snapshot.atr
        vwap_frac = _clamp(distance_atrs)

    # EMA alignment: price above both EMAs and 9 >= 20.
    ema_frac = 0.0
    if stock_snapshot.ema_9 is not None and stock_snapshot.ema_20 is not None:
        aligned = stock_snapshot.ema_9 >= stock_snapshot.ema_20
        above = stock_snapshot.last_price > stock_snapshot.ema_9
        ema_frac = (0.5 if aligned else 0.0) + (0.5 if above else 0.0)

    # Opening range / pullback quality: full credit if a setup matched at all;
    # pullback quality further rewards a shallow, orderly pullback.
    orb_frac = 1.0 if setup_matched else 0.0
    pullback_frac = 1.0 if (setup_matched and pullback_shallow) else (0.5 if setup_matched else 0.0)

    # Risk/reward: scaled between minimum_r (low credit) and preferred_r_min+ (full).
    rr_frac = 0.0
    if r_ratio is not None and minimum_r > 0:
        if r_ratio < minimum_r:
            rr_frac = 0.0
        else:
            span = max(preferred_r_min - minimum_r, 0.01)
            rr_frac = _clamp((r_ratio - minimum_r) / span)

    # Spread/liquidity: tighter spread relative to the ticker's own max is better.
    spread_frac = 0.0
    spread_pct = stock_snapshot.spread_pct
    if spread_pct is not None and max_spread_pct > 0:
        spread_frac = _clamp(1.0 - (spread_pct / max_spread_pct))

    # Sector/commodity strength: proxy using the same benchmark-strength fraction
    # until a broader sector index feed is added.
    sector_frac = bench_strength_frac

    return ScoreBreakdown(
        benchmark_strength=bench_strength_frac * weights["benchmark_strength"],
        relative_volume=rvol_frac * weights["relative_volume"],
        price_vs_vwap=vwap_frac * weights["price_vs_vwap"],
        ema_alignment=ema_frac * weights["ema_alignment"],
        opening_range_structure=orb_frac * weights["opening_range_structure"],
        pullback_quality=pullback_frac * weights["pullback_quality"],
        risk_reward=rr_frac * weights["risk_reward"],
        spread_liquidity=spread_frac * weights["spread_liquidity"],
        sector_strength=sector_frac * weights["sector_strength"],
    )
