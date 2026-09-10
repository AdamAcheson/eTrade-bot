"""Reconciles the bot's own view of what it holds against what the broker actually
reports (spec section 32: the broker, not PositionManager's bookkeeping, is the
authority on share counts).

Nothing in the bot used to call BrokerInterface.get_positions(), which is safe only
while the trading account holds nothing but what this bot put there. Against a real
account that is not true, and the two failure modes are asymmetric:

  * Shares the bot did not buy. Opening a position in a ticker the account already
    holds means the eventual SELL comes out of one combined pool -- under FIFO lot
    accounting the broker disposes the OLDEST lots, so the bot's exit sells the
    owner's long-term shares and leaves the bot's own behind. The position P&L is
    wrong, and so is the owner's tax basis. Not recoverable after the fact.

  * Shares the bot thinks it has and the broker does not (a fill that never
    happened, a manual sale, a partial fill booked as whole). Selling into that gap
    is not a flat exit -- it opens a SHORT, whose loss is unbounded.

So: never trade a ticker carrying shares this bot did not open, and never sell more
than the broker confirms is there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping


@dataclass
class Reconciliation:
    """What the broker holds vs what the bot believes it holds."""

    # ticker -> shares present at the broker that this bot did not open. Entries in
    # these tickers are blocked; the bot must not add to, or sell out of, a pool it
    # does not own.
    externally_held: Dict[str, int] = field(default_factory=dict)

    # ticker -> (bot_shares, broker_shares) where the broker reports FEWER shares
    # than the bot is tracking. Always a bug or an out-of-band action; the sell must
    # be clamped to the broker's number.
    short_at_broker: Dict[str, tuple] = field(default_factory=dict)

    def is_blocked(self, ticker: str) -> bool:
        return ticker in self.externally_held

    @property
    def has_discrepancy(self) -> bool:
        return bool(self.externally_held or self.short_at_broker)

    def describe(self) -> List[str]:
        """Human-readable lines for the log. Silence about a reconciliation failure
        is the thing that makes it dangerous, so every discrepancy gets said out
        loud, once per cycle it persists."""
        lines = []
        for ticker, shares in sorted(self.externally_held.items()):
            lines.append(
                f"{ticker}: {shares} share(s) held at the broker that this bot did not open "
                f"-- entries blocked (an exit would sell from the combined pool)"
            )
        for ticker, (bot_shares, broker_shares) in sorted(self.short_at_broker.items()):
            lines.append(
                f"{ticker}: bot tracks {bot_shares} share(s), broker reports {broker_shares} "
                f"-- exits clamped to the broker's count to avoid opening a short"
            )
        return lines


def reconcile(
    broker_positions: Mapping[str, object],
    bot_shares_by_ticker: Mapping[str, int],
) -> Reconciliation:
    """Compare broker holdings against the bot's tracked positions.

    `broker_positions` is BrokerInterface.get_positions() -- ticker -> object with a
    .quantity. `bot_shares_by_ticker` is what PositionManager currently has open.
    Tickers absent from either side are treated as zero.
    """
    result = Reconciliation()
    tickers = set(broker_positions) | set(bot_shares_by_ticker)
    for ticker in tickers:
        broker_qty = int(getattr(broker_positions.get(ticker), "quantity", 0) or 0)
        bot_qty = int(bot_shares_by_ticker.get(ticker, 0) or 0)

        # A short at the broker is never something this bot opened -- it only ever
        # submits BUY to enter -- so treat any negative balance as foreign and block
        # the ticker outright rather than trying to net it against a long.
        if broker_qty < 0:
            result.externally_held[ticker] = broker_qty
            continue

        if broker_qty > bot_qty:
            result.externally_held[ticker] = broker_qty - bot_qty
        elif broker_qty < bot_qty:
            result.short_at_broker[ticker] = (bot_qty, broker_qty)
    return result


def sellable_shares(ticker: str, intended: int, broker_positions: Mapping[str, object]) -> int:
    """How many shares may actually be sold: never more than the broker confirms is
    there, never negative. Clamping here rather than at the call site means a drifted
    position degrades to a smaller sell (or none) instead of a short."""
    broker_qty = int(getattr(broker_positions.get(ticker), "quantity", 0) or 0)
    return max(0, min(intended, broker_qty))
