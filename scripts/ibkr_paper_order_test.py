#!/usr/bin/env python3
"""One-off test of the order connection on your Interactive Brokers PAPER account.

Run it on the same computer as TWS, logged into Paper Trading, with the market open.
It will not run against a live account: broker/ibkr.py refuses any connection where
TWS reports an account id that does not start with "DU".

What it does, after you type YES:
  1. Buys 1 share of SYMBOL at half its price -- this cannot fill, so after
     ~10 seconds it must come back CANCELLED (proves nothing is left resting).
  2. Buys 1 share at 2% above the last price -- fills straight away at the market.
  3. Sells that 1 share at 2% below the last price -- fills straight away.
  4. Checks the account holds the same shares as before it started.

At most one share is ever held, for a few seconds. On a paper account this costs
nothing real; IBKR shows a simulated commission of about $1 per order.

Setup, once, in TWS: Global Configuration > API > Settings
  - UNtick "Read-Only API" (orders are refused while it is ticked)
  - keep "Allow connections from localhost only" ticked

Usage:
    python3 scripts/ibkr_paper_order_test.py            # AG, TWS paper port 7497
    python3 scripts/ibkr_paper_order_test.py --symbol HL
    python3 scripts/ibkr_paper_order_test.py --port 4002 # IB Gateway paper
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from typing import Callable, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from broker.base import OrderSide, OrderStatus  # noqa: E402
from broker.ibkr import IBKRPaperBrokerAdapter, IBKRSafetyError  # noqa: E402

CODE_NOTES = {
    321: "TWS is in Read-Only mode: untick 'Read-Only API' in Global Configuration > API > Settings",
    202: "normal: this is the cancel of the test order that could not fill",
    2104: "normal status note", 2106: "normal status note", 2158: "normal status note",
    354: "no real-time data subscription (expected; the test uses delayed prices)",
    10167: "delayed data shown instead of real-time (expected)",
    300: "harmless",
    399: "IBKR warning about the order; read the text",
    201: "IBKR REJECTED the order; read the text",
}


def last_price(ib, contract) -> float:
    bars = ib.reqHistoricalData(contract, endDateTime="", durationStr="1 D",
                                barSizeSetting="5 mins", whatToShow="TRADES",
                                useRTH=True, formatDate=1)
    if not bars:
        raise RuntimeError("no price bars returned -- is the market open?")
    return float(bars[-1].close)


def held(broker, symbol: str, expect: int, tries: int = 10) -> int:
    """Shares of `symbol` held. IBKR updates positions a moment after a fill, so give
    it a few seconds to reach `expect` before believing a different number."""
    qty = 0
    for _ in range(tries):
        p = broker.get_positions().get(symbol)
        qty = p.quantity if p else 0
        if qty == expect:
            break
        broker.ib.sleep(0.5)
    return qty


def run(make_adapter: Callable[[], IBKRPaperBrokerAdapter], symbol: str,
        ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> int:
    errors: List[Tuple[int, str]] = []
    try:
        broker = make_adapter()
    except IBKRSafetyError as e:
        out(f"REFUSED: {e}")
        return 2
    except Exception as e:  # connection refused, timeout
        out(f"FAIL  could not connect: {type(e).__name__}: {e}")
        out("      Is TWS open and logged into Paper Trading, with the API enabled?")
        return 2
    broker.ib.errorEvent += lambda req_id, code, msg, *rest: errors.append((code, msg))
    failures = 0
    try:
        out(f"Connected to paper account {broker.account}")
        acct = broker.get_account()
        out(f"  cash ${acct.cash:,.2f}   net liquidation ${acct.equity:,.2f}")
        start_qty = held(broker, symbol, 0, tries=1)
        already_open = {o.order_id for o in broker.get_open_orders()}
        if already_open:
            out(f"  note: {len(already_open)} order(s) were already open before this test "
                f"(scripts/ibkr_paper_flatten.py clears them); they are not counted")
        out(f"  {symbol} held before the test: {start_qty}")

        price = last_price(broker.ib, broker._contract(symbol))
        out(f"  last {symbol} price (may be ~15 min delayed): ${price:.2f}")
        out("")
        out(f"About to place 3 orders for 1 share of {symbol} on PAPER account {broker.account}.")
        if ask("Type YES (all caps) to go ahead: ").strip() != "YES":
            out("Aborted -- no order placed.")
            return 0

        def step(title, side, limit, want_status):
            nonlocal failures
            o = broker.submit_limit_order(symbol, side, 1, limit)
            ok = o.status == want_status
            failures += 0 if ok else 1
            fill = f" at ${o.avg_fill_price:.2f}" if o.avg_fill_price else ""
            fee = f", commission ${o.commission:.2f}" if o.commission is not None else ""
            out(f"  {'PASS' if ok else 'FAIL'}  {title}: {side.value} 1 @ ${o.limit_price:.2f} -> "
                f"{o.status.value}, filled {o.filled_quantity}{fill}{fee}")
            return o

        out("\n1. An order that cannot fill must come back cancelled (takes ~10 s):")
        step("far-below-market buy", OrderSide.BUY, math.floor(price * 50) / 100, OrderStatus.CANCELLED)
        out("\n2. A marketable buy must fill:")
        buy = step("buy", OrderSide.BUY, price * 1.02, OrderStatus.FILLED)
        out(f"        {symbol} now held: {held(broker, symbol, start_qty + buy.filled_quantity)}")
        if buy.filled_quantity > 0:
            out("\n3. A marketable sell must fill:")
            step("sell", OrderSide.SELL, price * 0.98, OrderStatus.FILLED)

        end_qty = held(broker, symbol, start_qty)
        ok = end_qty == start_qty
        failures += 0 if ok else 1
        out(f"\n  {'PASS' if ok else 'FAIL'}  {symbol} held after the test: {end_qty} (before: {start_qty})")
        if not ok:
            out(f"        Sell the extra {symbol} share in TWS, or run scripts/ibkr_paper_flatten.py.")
        leftover = [o for o in broker.get_open_orders(symbol) if o.order_id not in already_open]
        if leftover:
            failures += 1
            out(f"  FAIL  {len(leftover)} order(s) still open -- cancel them in TWS")
    finally:
        out("\nMessages from IBKR (verbatim):")
        for code, msg in errors or [(None, "none")]:
            note = CODE_NOTES.get(code)
            out(f"  [{code}] {msg}" + (f"\n         -> {note}" if note else "") if code else "  none")
        broker.disconnect()
    out("\nRESULT: " + ("order connection works" if failures == 0 else f"{failures} check(s) failed"))
    return 0 if failures == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Test the IBKR order connection on a PAPER account.")
    ap.add_argument("--port", type=int, default=7497, help="7497 TWS paper (default), 4002 IB Gateway paper")
    ap.add_argument("--client-id", type=int, default=22)
    ap.add_argument("--symbol", default="AG")
    args = ap.parse_args(argv)
    logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    cfg = {"ibkr": {"host": "127.0.0.1", "port": args.port, "client_id": args.client_id}}
    return run(lambda: IBKRPaperBrokerAdapter(cfg), args.symbol.upper())


if __name__ == "__main__":
    sys.exit(main())
