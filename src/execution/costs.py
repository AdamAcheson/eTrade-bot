"""Transaction cost model (spread, impact, commission).

Backtests in this project originally filled at bar prices with no costs at all, so
`gross_profit == net_profit` on every trade. That is harmless when comparing two
configurations to each other and badly misleading in absolute terms: the holdout
turns ~$92M of notional on $100k of equity, so it breaks even at roughly 10 bps
per side.

Cost is modelled per SHARE rather than in basis points, because the binding
constraint here is the one-cent minimum tick, not a percentage. Crossing a
penny-wide market costs half a cent per share whatever the stock costs -- which is
0.5 bps on a $95 stock and 12 bps on a $4 one. Over half this strategy's notional
sits in sub-$10 miners, so a flat-bps model would hide exactly the effect that
matters.

The three components:

  * SPREAD -- `spread_ticks` is the assumed quoted width in cents. Crossing costs
    half of it per side. 1.0 is the tightest a US equity quote can legally be, so
    the default is a FLOOR on real cost, not an estimate of it.
  * IMPACT -- `impact_bps` per side on notional, for the book moving against a
    $25k order. Zero by default; genuinely zero only for small orders in liquid
    names.
  * COMMISSION -- per order. Zero at E*TRADE for US equities.

`crossing_fraction` scales the spread term for strategies that sometimes rest a
passive order instead of crossing. Exits here always cross (`submit_exit_order`
sends a marketable limit), so 1.0 is right for this strategy unless entries are
modelled separately; anything below 1.0 is an assumption that passive entries fill
without adverse selection, which is optimistic in a momentum strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

TICK_SIZE = 0.01


@dataclass(frozen=True)
class TransactionCostModel:
    spread_ticks: float = 0.0
    impact_bps: float = 0.0
    commission_per_order: float = 0.0
    crossing_fraction: float = 1.0
    tick_size: float = TICK_SIZE

    @property
    def enabled(self) -> bool:
        return bool(self.spread_ticks or self.impact_bps or self.commission_per_order)

    def per_side(self, price: float, shares: int) -> float:
        """Cost of one fill of `shares` at `price`."""
        if shares <= 0:
            return 0.0
        half_spread = self.spread_ticks * self.tick_size / 2.0 * self.crossing_fraction
        spread_cost = shares * half_spread
        impact_cost = shares * price * self.impact_bps / 10000.0
        return spread_cost + impact_cost + self.commission_per_order

    def round_trip(self, entry_price: float, exit_price: float, shares: int) -> float:
        return self.per_side(entry_price, shares) + self.per_side(exit_price, shares)

    @classmethod
    def from_config(cls, risk_config: dict) -> "TransactionCostModel":
        cfg = (risk_config or {}).get("transaction_costs") or {}
        return cls(
            spread_ticks=float(cfg.get("spread_ticks", 0.0)),
            impact_bps=float(cfg.get("impact_bps", 0.0)),
            commission_per_order=float(cfg.get("commission_per_order", 0.0)),
            crossing_fraction=float(cfg.get("crossing_fraction", 1.0)),
        )
