"""Daily/cumulative performance report (spec section 29) and read-only feedback-loop
analytics (spec section 30). This module only ever produces suggestions as data --
nothing here writes back into config/*.yaml or otherwise changes strategy behavior;
a human must review and apply any suggested parameter change."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from models.bar import Bar
from models.signal import Decision, Signal
from models.trade import Trade


@dataclass
class DailyReport:
    date: str
    stocks_evaluated: int
    setups_identified: int
    trades_taken: int
    trades_rejected: int
    rejection_reasons: Dict[str, int]
    wins: int
    losses: int
    win_rate: Optional[float]
    gross_pnl: float
    net_pnl: float
    average_gain: Optional[float]
    average_loss: Optional[float]
    average_r: Optional[float]
    best_trade: Optional[str]
    worst_trade: Optional[str]
    max_drawdown: float
    open_overnight_positions: List[str]
    missed_opportunities: List[dict] = field(default_factory=list)


def _max_drawdown(cumulative_pnl_series: Sequence[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    running = 0.0
    for pnl in cumulative_pnl_series:
        running += pnl
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return max_dd


def generate_daily_report(
    date: str,
    signals: Sequence[Signal],
    trades: Sequence[Trade],
    open_overnight_tickers: Sequence[str],
) -> DailyReport:
    day_signals = [s for s in signals]
    day_trades = [t for t in trades]

    stocks_evaluated = len({s.ticker for s in day_signals})
    setups_identified = len({s.ticker for s in day_signals if s.setup_type})
    trades_taken = len(day_trades)
    rejected = [s for s in day_signals if s.decision == Decision.REJECTED]
    trades_rejected = len(rejected)
    rejection_reasons = Counter(s.rejection_reason.value for s in rejected if s.rejection_reason)

    closed_trades = [t for t in day_trades if t.net_profit is not None]
    wins = [t for t in closed_trades if t.net_profit > 0]
    losses = [t for t in closed_trades if t.net_profit <= 0]
    win_rate = len(wins) / len(closed_trades) if closed_trades else None

    gross_pnl = sum(t.gross_profit or 0.0 for t in closed_trades)
    net_pnl = sum(t.net_profit or 0.0 for t in closed_trades)
    average_gain = sum(t.net_profit for t in wins) / len(wins) if wins else None
    average_loss = sum(t.net_profit for t in losses) / len(losses) if losses else None
    average_r = sum(t.r_return for t in closed_trades if t.r_return is not None) / len(closed_trades) if closed_trades else None

    best_trade = max(closed_trades, key=lambda t: t.net_profit).ticker if closed_trades else None
    worst_trade = min(closed_trades, key=lambda t: t.net_profit).ticker if closed_trades else None

    max_dd = _max_drawdown([t.net_profit or 0.0 for t in closed_trades])

    return DailyReport(
        date=date,
        stocks_evaluated=stocks_evaluated,
        setups_identified=setups_identified,
        trades_taken=trades_taken,
        trades_rejected=trades_rejected,
        rejection_reasons=dict(rejection_reasons),
        wins=len(wins),
        losses=len(losses),
        win_rate=win_rate,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        average_gain=average_gain,
        average_loss=average_loss,
        average_r=average_r,
        best_trade=best_trade,
        worst_trade=worst_trade,
        max_drawdown=max_dd,
        open_overnight_positions=list(open_overnight_tickers),
    )


def find_missed_opportunities(
    rejected_signals: Sequence[Signal],
    bars_after_signal_by_ticker: Dict[str, Sequence[Bar]],
) -> List[dict]:
    """For each rejected signal that still carried a computed entry/stop/target,
    check whether price would have reached target before stop using the bars that
    followed the rejection. This is analysis only -- it never resubmits a trade."""
    missed = []
    for s in rejected_signals:
        if s.entry_price is None or s.stop is None or s.target is None:
            continue
        future_bars = bars_after_signal_by_ticker.get(s.ticker, [])
        hit_target = False
        hit_stop = False
        for b in future_bars:
            if b.low <= s.stop:
                hit_stop = True
                break
            if b.high >= s.target:
                hit_target = True
                break
        if hit_target:
            missed.append({
                "ticker": s.ticker,
                "timestamp": s.timestamp.isoformat(),
                "rejection_reason": s.rejection_reason.value if s.rejection_reason else None,
                "setup_type": s.setup_type,
                "entry_price": s.entry_price,
                "target": s.target,
                "would_have_hit_target": True,
            })
    return missed


def generate_feedback_suggestions(trades: Sequence[Trade], signals: Sequence[Signal]) -> List[str]:
    """Read-only observational suggestions (spec section 30). Never applied
    automatically -- a human must approve any resulting change to config/*.yaml."""
    suggestions: List[str] = []
    closed = [t for t in trades if t.net_profit is not None]
    if not closed:
        return suggestions

    high_rvol_tickers = {s.ticker: s.relative_volume for s in signals if s.entry_candidate}
    high_rvol_trades = [t for t in closed if high_rvol_tickers.get(t.ticker, 0) > 1.5]
    low_rvol_trades = [t for t in closed if 0 < high_rvol_tickers.get(t.ticker, 0) <= 1.5]
    if high_rvol_trades and low_rvol_trades:
        hi_wr = sum(1 for t in high_rvol_trades if t.net_profit > 0) / len(high_rvol_trades)
        lo_wr = sum(1 for t in low_rvol_trades if t.net_profit > 0) / len(low_rvol_trades)
        if hi_wr > lo_wr:
            suggestions.append(
                f"Trades with RVOL > 1.5 had a higher win rate ({hi_wr:.0%} vs {lo_wr:.0%})."
            )

    by_hour: Dict[int, List[Trade]] = defaultdict(list)
    for t in closed:
        by_hour[t.entry_time.hour].append(t)
    hour_win_rates = {
        h: sum(1 for t in ts if t.net_profit > 0) / len(ts)
        for h, ts in by_hour.items() if ts
    }
    if len(hour_win_rates) > 1:
        best_hour = max(hour_win_rates, key=hour_win_rates.get)
        worst_hour = min(hour_win_rates, key=hour_win_rates.get)
        if hour_win_rates[best_hour] - hour_win_rates[worst_hour] >= 0.2:
            suggestions.append(
                f"Entries around {best_hour}:00 outperformed entries around {worst_hour}:00 "
                f"({hour_win_rates[best_hour]:.0%} vs {hour_win_rates[worst_hour]:.0%} win rate)."
            )

    stopped_before_target = [
        t for t in closed
        if t.exit_reason and t.exit_reason.value == "STOP_HIT" and t.maximum_favorable_excursion > 0
    ]
    if stopped_before_target and len(stopped_before_target) / len(closed) >= 0.3:
        suggestions.append(
            f"{len(stopped_before_target)}/{len(closed)} stopped-out trades had moved favorably "
            f"first -- consider reviewing the ATR stop multiplier."
        )

    by_ticker: Dict[str, List[Trade]] = defaultdict(list)
    for t in closed:
        by_ticker[t.ticker].append(t)
    for ticker, ts in by_ticker.items():
        if len(ts) >= 3:
            wr = sum(1 for t in ts if t.net_profit > 0) / len(ts)
            if wr <= 0.25:
                suggestions.append(f"{ticker} trades had a low win rate ({wr:.0%} over {len(ts)} trades).")

    return suggestions
