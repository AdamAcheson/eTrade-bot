#!/usr/bin/env python3
"""Clean up the Interactive Brokers PAPER account: cancel every open order and sell
every stock position, so the bot starts from an empty account.

Paper only: broker/ibkr.py refuses to connect unless every account TWS reports
starts with "DU". Shows what it will do and asks for YES before doing anything.
Positions are sold with market orders (this is the one place that uses them).

Usage:
    python3 scripts/ibkr_paper_flatten.py
    python3 scripts/ibkr_paper_flatten.py --port 4002   # IB Gateway paper
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Callable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from broker.ibkr import IBKRPaperBrokerAdapter, IBKRSafetyError  # noqa: E402


def run(make_adapter: Callable[[], IBKRPaperBrokerAdapter],
        ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> int:
    try:
        broker = make_adapter()
    except IBKRSafetyError as e:
        out(f"REFUSED: {e}")
        return 2
    except Exception as e:  # connection refused, timeout
        out(f"FAIL  could not connect: {type(e).__name__}: {e}")
        return 2
    try:
        # Orders from every API client and from TWS itself, not just this one.
        broker.ib.reqAllOpenOrders()
        orders = broker.get_open_orders()
        positions = broker.get_positions()
        out(f"Paper account {broker.account}")
        out(f"  open orders: {len(orders)}")
        for o in orders:
            out(f"    {o.side.value} {o.quantity} {o.ticker} @ ${o.limit_price:.2f}")
        out(f"  stock positions: {len(positions)}")
        for p in positions.values():
            out(f"    {p.quantity} {p.ticker} (avg ${p.avg_price:.2f})")
        if not orders and not positions:
            out("\nNothing to clean up.")
            return 0
        if ask("\nType YES (all caps) to cancel all orders and sell all positions: ").strip() != "YES":
            out("Aborted -- nothing changed.")
            return 0

        if orders:
            broker.ib.reqGlobalCancel()
            broker.ib.sleep(3)
        for p in positions.values():
            if p.quantity > 0:
                o = broker.close_position(p.ticker)
                fill = f" at ${o.avg_fill_price:.2f}" if o and o.avg_fill_price else ""
                out(f"  sell {p.quantity} {p.ticker}: {o.status.value if o else 'nothing to sell'}{fill}")
            else:
                out(f"  {p.ticker}: short {p.quantity} -- not something this bot opens; close it in TWS")

        broker.ib.sleep(2)
        broker.ib.reqAllOpenOrders()
        left_orders = broker.get_open_orders()
        left_positions = {t: p for t, p in broker.get_positions().items() if p.quantity > 0}
        ok = not left_orders and not left_positions
        out(f"\nopen orders now: {len(left_orders)}   stock positions now: {len(left_positions)}")
        out("RESULT: " + ("account is clean" if ok else "something is left -- check TWS"))
        return 0 if ok else 1
    finally:
        broker.disconnect()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cancel all orders and sell all positions on the IBKR PAPER account.")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=23)
    args = ap.parse_args(argv)
    logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    cfg = {"ibkr": {"host": "127.0.0.1", "port": args.port, "client_id": args.client_id}}
    return run(lambda: IBKRPaperBrokerAdapter(cfg))


if __name__ == "__main__":
    sys.exit(main())
