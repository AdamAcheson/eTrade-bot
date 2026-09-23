#!/usr/bin/env python3
"""Read-only connectivity check against Interactive Brokers Trader Workstation (TWS)
or IB Gateway. Run this on the SAME computer as TWS -- the API listens on localhost
only, so it cannot be reached from a cloud machine.

It answers three questions before any broker adapter is built on top of IBKR:

  1. Does the API accept a connection on this account at all? IBKR's own FAQ says
     API support is not available on IBKR Lite; if that applies to your paper
     account, IBKR will say so in the error list at the end.
  2. Is live 5-minute market data available, or only delayed data?
  3. What does the account look like (paper or live, cash, buying power,
     day trades remaining)?

It connects with readonly=True and makes no order calls of any kind. It cannot
place, modify or cancel an order.

Setup, once, in TWS:
    Edit (or File) > Global Configuration > API > Settings
      - tick   "Enable ActiveX and Socket Clients"
      - note   the "Socket port" (TWS paper default is 7497)
      - leave  "Read-Only API" ticked for this check
      - leave  127.0.0.1 in Trusted IPs

Usage:
    pip install -r requirements.txt          # needs Python 3.10+
    python scripts/ibkr_check.py             # TWS paper on port 7497, symbol AG
    python scripts/ibkr_check.py --port 4002 # IB Gateway paper
    python scripts/ibkr_check.py --symbol FCX

Please paste the full output back -- especially the "errors reported by IBKR"
section, which is where a Lite-account API refusal would appear.
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Callable, List, Optional, Tuple

PAPER_PORTS = {7497: "TWS paper", 4002: "IB Gateway paper"}
LIVE_PORTS = {7496: "TWS LIVE", 4001: "IB Gateway LIVE"}

ACCOUNT_TAGS = ("AccountType", "NetLiquidation", "TotalCashValue", "AvailableFunds",
                "BuyingPower", "DayTradesRemaining", "Cushion")


def looks_like_paper(account_id: str) -> bool:
    """IBKR paper accounts are issued ids beginning 'DU' (live individual accounts
    begin 'U'). A heuristic for a loud warning, not a safety guarantee -- the
    connection is read-only regardless."""
    return account_id.upper().startswith("DU")


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def run(ib, host: str, port: int, client_id: int, symbol: str,
        out: Callable[[str], None] = print) -> int:
    errors: List[Tuple[int, int, str]] = []

    def on_error(req_id, code, msg, *rest):
        errors.append((req_id, code, msg))

    ib.errorEvent += on_error
    failures = 0

    out(f"Connecting to {host}:{port} "
        f"({PAPER_PORTS.get(port) or LIVE_PORTS.get(port) or 'custom port'}), read-only ...")
    if port in LIVE_PORTS:
        out("  WARNING: that is a LIVE-trading port. This check is read-only, but you said you "
            "set TWS to paper -- double-check which session is open.")
    try:
        ib.connect(host, port, clientId=client_id, timeout=10, readonly=True)
    except Exception as exc:  # connection refused, timeout, API disabled
        out(f"  FAIL  could not connect: {type(exc).__name__}: {exc}")
        out("        Is TWS open and logged in? Is 'Enable ActiveX and Socket Clients' ticked?")
        out(f"        Does the Socket port in TWS match {port}?")
        _print_errors(errors, out)
        return 2
    out("  PASS  connected")

    # -- 1. account ---------------------------------------------------------------
    accounts = ib.managedAccounts()
    out(f"\nAccounts visible to the API: {accounts or 'NONE'}")
    if not accounts:
        failures += 1
        out("  FAIL  no accounts returned -- often what an API refusal looks like")
    for acct in accounts:
        kind = "paper" if looks_like_paper(acct) else "LIVE (does not start with 'DU')"
        out(f"  {acct}: looks {kind}")

    values = {}
    for av in ib.accountSummary():
        if av.tag in ACCOUNT_TAGS and av.tag not in values:
            values[av.tag] = f"{av.value} {av.currency}".strip()
    if values:
        out("\nAccount summary:")
        for tag in ACCOUNT_TAGS:
            if tag in values:
                out(f"  {tag:20} {values[tag]}")
    else:
        out("\n  (no account summary returned)")

    # -- 2. contract --------------------------------------------------------------
    from ib_async import Stock
    out(f"\nLooking up {symbol} (SMART, USD) ...")
    try:
        qualified = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
    except Exception as exc:
        qualified = []
        out(f"  FAIL  {type(exc).__name__}: {exc}")
    if not qualified:
        out("  FAIL  contract not found -- skipping market data checks")
        _print_errors(errors, out)
        ib.disconnect()
        return 1
    contract = qualified[0]
    out(f"  PASS  conId {contract.conId} on {contract.primaryExchange or contract.exchange}")

    # -- 3. live quote, falling back to delayed -----------------------------------
    out("\nQuote:")
    got_quote = False
    for mdt, label in ((1, "live"), (3, "delayed")):
        ib.reqMarketDataType(mdt)
        ticker = ib.reqMktData(contract, "", False, False)
        ib.sleep(4)
        bid, ask, last = _num(ticker.bid), _num(ticker.ask), _num(ticker.last)
        ib.cancelMktData(contract)
        if any(v is not None and v > 0 for v in (bid, ask, last)):
            out(f"  PASS  {label}: bid {bid}  ask {ask}  last {last}")
            if label == "delayed":
                out("        Only DELAYED data is available. The bot needs live bars; either add a "
                    "market data subscription in IBKR, or keep Twelve Data for bars.")
            got_quote = True
            break
        out(f"  --    no {label} quote returned")
    if not got_quote:
        failures += 1
        out("  FAIL  no quote at all (outside market hours this can be normal for live data)")

    # -- 4. the bars the strategy actually uses -------------------------------------
    out("\n5-minute bars, most recent session (regular hours only):")
    try:
        bars = ib.reqHistoricalData(contract, endDateTime="", durationStr="1 D",
                                    barSizeSetting="5 mins", whatToShow="TRADES",
                                    useRTH=True, formatDate=1)
    except Exception as exc:
        bars = []
        out(f"  FAIL  {type(exc).__name__}: {exc}")
    if bars:
        b = bars[-1]
        out(f"  PASS  {len(bars)} bars; last {b.date}  O {b.open}  H {b.high}  L {b.low}  "
            f"C {b.close}  V {b.volume}")
    else:
        failures += 1
        out("  FAIL  no bars returned")

    _print_errors(errors, out)
    ib.disconnect()
    out("\nRESULT: " + ("all checks passed" if failures == 0 else f"{failures} check(s) failed"))
    return 0 if failures == 0 else 1


def _print_errors(errors, out) -> None:
    out("\nErrors reported by IBKR (verbatim):")
    if not errors:
        out("  none")
        return
    for req_id, code, msg in errors:
        out(f"  [{code}] (req {req_id}) {msg}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only IBKR API connectivity check.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497,
                    help="7497 TWS paper (default), 4002 IB Gateway paper")
    ap.add_argument("--client-id", type=int, default=17)
    ap.add_argument("--symbol", default="AG")
    args = ap.parse_args(argv)
    try:
        from ib_async import IB
    except ImportError:
        print("ib_async is not installed: pip install -r requirements.txt (Python 3.10+)")
        return 3
    return run(IB(), args.host, args.port, args.client_id, args.symbol.upper())


if __name__ == "__main__":
    sys.exit(main())
